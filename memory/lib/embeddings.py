"""Pluggable embedding providers for the USER-TRIGGERED semantic search path.

Nothing in the automatic path (hooks, reflection, SessionStart injection) computes
embeddings. Only `huh search --semantic` does. Model + API route are configurable
via the store config:

    embedding_provider : "huggingface" | "hf" | "ollama" | "dspy" | "word2vec"
    embedding_model    : e.g. "BAAI/bge-small-en-v1.5"
    embedding_api_url  : e.g. "http://localhost:11434/api/embeddings"  (ollama)
    embedding_dim      : e.g. 384

Fallback chain (all providers fall through to the next on failure):
    hf       → sentence-transformers, local, no GPU required (~130 MB).
    ollama   → HTTP to local Ollama; unreachable → hf.
    dspy     → dspy.Embedder; unconfigured/missing → hf.
    word2vec → gensim averaged word vectors; missing → raises (no silent garbage).

word2vec is the last-resort real-semantic fallback. If nothing is installed,
an ImportError surfaces so the operator knows to install something.
"""

from __future__ import annotations

import json
import math
import urllib.request
from typing import List, Protocol


class EmbeddingProvider(Protocol):
    dim: int

    def embed(self, text: str) -> List[float]: ...


# ---------------------------------------------------------------------------
# HuggingFace (sentence-transformers, local CPU/GPU)
# ---------------------------------------------------------------------------

class HuggingFaceEmbeddingProvider:
    """Local inference via sentence-transformers. No network after first download.

    Default: BAAI/bge-small-en-v1.5 (384-dim, MTEB top-tier, ~130 MB).
    Alt:     all-MiniLM-L6-v2       (384-dim, very fast,      ~90 MB).
    Models cached in ~/.cache/huggingface/.
    """

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers not installed — run: pip install 'crisp[semantic]'"
            ) from exc
        self._model = SentenceTransformer(model)
        self.dim: int = self._model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> List[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

class OllamaEmbeddingProvider:
    """HTTP POST to a local Ollama /api/embeddings endpoint."""

    def __init__(
        self,
        model: str = "BAAI/bge-small-en-v1.5:latest",
        api_url: str = "http://localhost:11434/api/embeddings",
        dim: int = 384,
        timeout: float = 10.0,
    ):
        self.model = model
        self.api_url = api_url
        self.dim = dim
        self.timeout = timeout

    def embed(self, text: str) -> List[float]:
        payload = json.dumps({"model": self.model, "prompt": text}).encode()
        req = urllib.request.Request(
            self.api_url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        vec = data.get("embedding")
        if not vec and isinstance(data.get("data"), list) and data["data"]:
            vec = data["data"][0].get("embedding")
        if not vec:
            raise ValueError(f"no embedding in response from {self.api_url}")
        if self.dim == 0:
            self.dim = len(vec)
        return _l2_normalize([float(x) for x in vec])


# ---------------------------------------------------------------------------
# DSPy — configurable backend via dspy.settings
# ---------------------------------------------------------------------------

class DSPyEmbeddingProvider:
    """Embedding via dspy.Embedder — backend follows whatever dspy.configure() set.

    This lets you point at OpenAI, Cohere, a local model, or anything DSPy
    supports without changing this file. Configure once at startup:

        import dspy
        dspy.configure(lm=dspy.LM("openai/text-embedding-3-small"))

    Then set  embedding_provider: "dspy"  in the store config.

    The `embedding_model` config key is forwarded to dspy.Embedder as the
    model string if provided; otherwise DSPy uses whatever its default is.
    """

    def __init__(self, model: str = "", dim: int = 0):
        try:
            import dspy  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "dspy not installed — run: pip install dspy"
            ) from exc

        kwargs = {"model": model} if model else {}
        self._embedder = dspy.Embedder(**kwargs)
        self.dim = dim  # 0 = discover on first call

    def embed(self, text: str) -> List[float]:
        result = self._embedder([text])
        # dspy.Embedder returns a 2-D array-like; grab first row
        if hasattr(result, "tolist"):
            vec = result[0].tolist()
        elif isinstance(result, list):
            vec = list(result[0]) if isinstance(result[0], (list, tuple)) else list(result)
        else:
            vec = list(result[0])
        if self.dim == 0:
            self.dim = len(vec)
        return _l2_normalize([float(x) for x in vec])


# ---------------------------------------------------------------------------
# Word2Vec via gensim — lightweight last-resort real-semantic fallback
# ---------------------------------------------------------------------------

class Word2VecEmbeddingProvider:
    """Averaged word vectors via gensim. No GPU, ~66 MB model, works offline after first use.

    Default model: glove-wiki-gigaword-50 (50-dim, fast, reasonable quality).
    Better quality: word2vec-google-news-300 (300-dim, 1.6 GB — set embedding_model).
    Models are downloaded once to ~/gensim-data/ and cached.

    OOV words are skipped; if the entire text is OOV, returns a zero vector.
    """

    def __init__(self, model_name: str = "glove-wiki-gigaword-50"):
        try:
            import gensim.downloader as api  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "gensim not installed — run: pip install gensim"
            ) from exc
        self._wv = api.load(model_name)
        self.dim: int = self._wv.vector_size

    def embed(self, text: str) -> List[float]:
        tokens = text.lower().split()
        vecs = [self._wv[t] for t in tokens if t in self._wv]
        if not vecs:
            return [0.0] * self.dim
        avg = [sum(v[i] for v in vecs) / len(vecs) for i in range(self.dim)]
        return _l2_normalize(avg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity. Assumes L2-normalized inputs (all providers normalise)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _make_hf(config: dict) -> HuggingFaceEmbeddingProvider:
    model = config.get("embedding_model") or "BAAI/bge-small-en-v1.5"
    return HuggingFaceEmbeddingProvider(model=model)


def _make_word2vec(config: dict) -> Word2VecEmbeddingProvider:
    model = config.get("embedding_model") or "glove-wiki-gigaword-50"
    return Word2VecEmbeddingProvider(model_name=model)


def _hf_then_w2v(config: dict) -> EmbeddingProvider:
    """Try HF, then word2vec, then raise — no silent garbage."""
    try:
        return _make_hf(config)
    except ImportError:
        return _make_word2vec(config)  # raises ImportError itself if gensim missing


def get_provider(config: dict) -> EmbeddingProvider:
    """Build an embedding provider from store config.

    Provider values: "huggingface" | "hf" | "ollama" | "dspy" | "word2vec"
    Fallback order on failure: hf → word2vec → ImportError (no silent garbage).
    """
    provider = str(config.get("embedding_provider") or "huggingface").lower()
    dim = int(config.get("embedding_dim", 384))

    if provider in ("huggingface", "hf"):
        return _make_hf(config)

    if provider == "word2vec":
        return _make_word2vec(config)

    if provider == "ollama":
        try:
            p = OllamaEmbeddingProvider(
                model=config.get("embedding_model") or "BAAI/bge-small-en-v1.5:latest",
                api_url=config.get("embedding_api_url") or "http://localhost:11434/api/embeddings",
                dim=dim,
            )
            p.embed("ping")  # health-check
            return p
        except Exception:
            return _hf_then_w2v(config)

    if provider == "dspy":
        try:
            model = config.get("embedding_model") or ""
            return DSPyEmbeddingProvider(model=model, dim=dim)
        except Exception:
            return _hf_then_w2v(config)

    raise ValueError(
        f"Unknown embedding_provider {provider!r}. "
        "Valid: huggingface, hf, ollama, dspy, word2vec"
    )
