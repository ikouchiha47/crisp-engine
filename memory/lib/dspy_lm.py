"""dspy.LM factory for narrate.py/affect.py.

Separate from lib/embeddings.py (different concern, untouched). Still
depends on lib/generate.py for exactly one thing: HFChatProvider, the local
(zero-network) transformers-pipeline fallback for when neither the
configured primary model nor Ollama are reachable — dspy.LM has no built-in
equivalent (its huggingface/<model> route calls HF's *hosted* Inference
API, not a local pipeline), so rather than duplicate HFChatProvider's local
inference or drop the fallback tier, _LocalHFLM wraps it as a real
dspy.LM so it still runs through the same dspy.Predict(Signature) call
path as every other backend.

Reads one nested config key, `memory_model_config`, passed straight through
to dspy.LM(**memory_model_config) — dspy.LM/litellm route by the
provider/model prefix already encoded in "model" itself (e.g.
"gemini/gemini-3.6-flash", "ollama_chat/qwen3.5:4b"), so there's nothing
for this module to branch on:
    {
        "model": "gemini/gemini-3.6-flash",
        "api_key": "...",       # falls back to GEMINI_API_KEY env var
        "api_base": "...",      # ollama_chat/* only
        "timeout": 60
    }
api_key falls back to the GEMINI_API_KEY env var when unset — that env var
name is already what every other Gemini-aware tool on this machine uses.

Fallback order, same as the old get_generate_provider: configured primary
-> local HF pipeline -> None. Callers must degrade to no-narrative/no-distill
on None, never guess.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import dspy

from .generate import HFChatProvider


class _LocalHFLM(dspy.LM):
    """Wraps HFChatProvider (local transformers pipeline, zero network) as
    a real dspy.LM. Last-resort fallback only — HFChatProvider itself is
    untouched, this just adapts its .generate(system, user) -> str to the
    shape dspy.BaseLM expects back from forward(): an object with
    .choices[0].message.content and .model (see dspy.BaseLM._process_completion).
    Only forward() is implemented, not aforward() — narrate.py/affect.py
    only ever call the synchronous dspy.Predict path.
    """

    def __init__(self, model: str = "Qwen/Qwen2.5-1.5B-Instruct"):
        super().__init__(model=f"local-hf/{model}")
        self._hf = HFChatProvider(model)

    def forward(self, prompt: Optional[str] = None, messages: Optional[list] = None, **kwargs):
        messages = messages or [{"role": "user", "content": prompt}]
        system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        user = "\n".join(m["content"] for m in messages if m.get("role") != "system")
        text = self._hf.generate(system, user) or ""

        message = type("Message", (), {"content": text})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice], "model": self.model})()


def _local_hf_fallback() -> Optional[dspy.LM]:
    try:
        return _LocalHFLM()
    except Exception:
        return None


_DEFAULT_MM_CONFIG = {
    "model": "ollama_chat/qwen3.5:4b",
    "api_base": "http://localhost:11434",
    "timeout": 30,
    "num_retries": 0,
}


def get_dspy_lm(config: Dict[str, Any]) -> Optional[dspy.LM]:
    """Build a dspy.LM from config["memory_model_config"], falling back to
    a local HF pipeline, or None if both are unconfigured/unreachable.

    Defaults to ollama_chat/qwen3.5:4b @ localhost:11434 when
    memory_model_config is unset entirely — same zero-config default
    get_generate_provider had (generate_provider="ollama",
    generate_model="qwen3.5:4b"), so a fresh install still works without
    requiring the user to hand-configure a model first.
    """
    mm_config = dict(config.get("memory_model_config") or _DEFAULT_MM_CONFIG)

    if "model" in mm_config:
        primary = _build_primary(mm_config)
        if primary is not None:
            return primary

    return _local_hf_fallback()


def _build_primary(mm_config: Dict[str, Any]) -> Optional[dspy.LM]:
    if mm_config["model"].startswith("gemini/") and "api_key" not in mm_config:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None
        mm_config["api_key"] = api_key

    if mm_config["model"].startswith("ollama_chat/") and "think" not in mm_config:
        # qwen3.5 (and other reasoning-by-default models) emit visible
        # chain-of-thought unless told not to — verified directly: a bare
        # "say hi" took 37s with thinking on vs 1.7s with think=False on
        # this same model/hardware. litellm passes this straight through
        # to Ollama's /api/chat body.
        mm_config["think"] = False

    try:
        lm = dspy.LM(**mm_config)
        if mm_config["model"].startswith("ollama_chat/"):
            # Local-liveness check only — a real Gemini call here would
            # waste a billed request just to prove the API key parses.
            lm(messages=[{"role": "user", "content": "ping"}])
        return lm
    except Exception:
        return None
