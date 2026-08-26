"""Pluggable local chat/generation providers for the distillation path
(lib/affect.py / lib/distill.py — not built yet, this is their dependency).

Model + every generation parameter are configurable via store config,
mirroring lib/embeddings.py's provider chain exactly — nothing about which
model runs, or how, is hardcoded:

    generate_provider    : "ollama" | "huggingface" | "hf"
    generate_model       : e.g. "qwen3.5:4b"
    generate_api_url     : e.g. "http://localhost:11434"           (ollama)
    generate_think       : bool  — disable a thinking model's chain-of-thought
    generate_temperature : float
    generate_top_p       : float
    generate_top_k       : int
    generate_timeout     : float — seconds; a thinking model with think=true
                            and no timeout can stall for minutes (verified:
                            qwen3.5:4b, thinking on, timed out at 2min on this
                            machine — this is exactly the failure a
                            configurable timeout exists to cut off)

Fallback chain: ollama -> huggingface -> None (no silent garbage; a caller
getting None must skip distillation this run, not guess).
"""

from __future__ import annotations

import json
import urllib.request
from typing import Optional, Protocol


class ChatProvider(Protocol):
    def generate(self, system: str, user: str) -> Optional[str]: ...


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

class OllamaChatProvider:
    """HTTP POST to a local Ollama /api/chat endpoint.

    Every parameter below is config-sourced (see get_generate_provider) —
    none of this is a hardcoded default baked into behavior; the defaults
    here only apply when the caller passes nothing, same as
    OllamaEmbeddingProvider's constructor defaults.
    """

    def __init__(
        self,
        model: str = "qwen3.5:4b",
        api_url: str = "http://localhost:11434",
        think: bool = False,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        timeout: float = 60.0,
        response_format: str = "json",
    ):
        self.model = model
        self.api_url = api_url.rstrip("/")
        self.think = think
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.timeout = timeout
        self.response_format = response_format

    def _options(self) -> dict:
        opts = {}
        if self.temperature is not None:
            opts["temperature"] = self.temperature
        if self.top_p is not None:
            opts["top_p"] = self.top_p
        if self.top_k is not None:
            opts["top_k"] = self.top_k
        return opts

    def generate(self, system: str, user: str) -> Optional[str]:
        body = {
            "model": self.model,
            "stream": False,
            "think": self.think,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.response_format:
            body["format"] = self.response_format
        opts = self._options()
        if opts:
            body["options"] = opts

        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.api_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except Exception:
            return None
        return data.get("message", {}).get("content") or None

    def health_check(self) -> bool:
        """Cheap real call — same role as OllamaEmbeddingProvider.embed('ping')."""
        try:
            return self.generate("You are a test.", "reply with the word ok") is not None
        except Exception:
            return False


# ---------------------------------------------------------------------------
# HuggingFace (transformers text-generation pipeline, local)
# ---------------------------------------------------------------------------

class HFChatProvider:
    """Local inference via transformers' text-generation pipeline.

    Fallback when Ollama isn't reachable — same role HuggingFaceEmbeddingProvider
    plays for embeddings. No GPU required, slower than a warm Ollama model.
    """

    def __init__(self, model: str = "Qwen/Qwen2.5-1.5B-Instruct"):
        try:
            from transformers import pipeline  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "transformers not installed — run: pip install 'crisp[generate]'"
            ) from exc
        self._pipe = pipeline("text-generation", model=model)

    def generate(self, system: str, user: str) -> Optional[str]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            result = self._pipe(messages, max_new_tokens=512)
            return result[0]["generated_text"][-1]["content"]
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _make_hf(config: dict) -> HFChatProvider:
    model = config.get("generate_model") or "Qwen/Qwen2.5-1.5B-Instruct"
    return HFChatProvider(model=model)


def _make_ollama(config: dict) -> OllamaChatProvider:
    return OllamaChatProvider(
        model=config.get("generate_model") or "qwen3.5:4b",
        api_url=config.get("generate_api_url") or "http://localhost:11434",
        think=bool(config.get("generate_think", False)),
        temperature=config.get("generate_temperature"),
        top_p=config.get("generate_top_p"),
        top_k=config.get("generate_top_k"),
        timeout=float(config.get("generate_timeout", 60.0)),
    )


def get_generate_provider(config: dict) -> Optional[ChatProvider]:
    """Build a chat provider from store config.

    Provider values: "ollama" | "huggingface" | "hf"
    Fallback order on failure: ollama -> hf -> None (caller must skip
    distillation on None, never guess).
    """
    provider = str(config.get("generate_provider") or "ollama").lower()

    if provider == "ollama":
        try:
            p = _make_ollama(config)
            if p.health_check():
                return p
        except Exception:
            pass
        try:
            return _make_hf(config)
        except ImportError:
            return None

    if provider in ("huggingface", "hf"):
        try:
            return _make_hf(config)
        except ImportError:
            return None

    return None
