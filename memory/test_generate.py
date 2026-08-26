"""Black-box tests for lib/generate.py — proves config values actually reach
the provider instance, not just get read and dropped. HTTP is mocked; no
real network/model calls in the unit suite (the real model was already
verified live in this session)."""
from unittest.mock import patch

from lib.config import load as cfg_load
from lib.generate import OllamaChatProvider, get_generate_provider


def test_ollama_provider_built_with_configured_model_and_think_false():
    cfg = {"generate_provider": "ollama", "generate_model": "qwen3.5:4b", "generate_think": False}
    with patch.object(OllamaChatProvider, "health_check", return_value=True):
        provider = get_generate_provider(cfg)
    assert isinstance(provider, OllamaChatProvider)
    assert provider.model == "qwen3.5:4b"
    assert provider.think is False


def test_generate_think_string_false_coerces_to_bool_false():
    # as it would arrive from an env var or .crisp.json — a truthy string
    # must not coerce to Python True
    merged = cfg_load(overrides={"generate_think": "false"})
    assert merged["generate_think"] is False


def test_generate_think_string_true_coerces_to_bool_true():
    merged = cfg_load(overrides={"generate_think": "true"})
    assert merged["generate_think"] is True


def test_ollama_health_check_failure_falls_through_to_hf():
    cfg = {"generate_provider": "ollama", "generate_model": "qwen3.5:4b"}
    with patch.object(OllamaChatProvider, "health_check", return_value=False):
        with patch("lib.generate._make_hf", return_value="HF_PROVIDER_SENTINEL"):
            provider = get_generate_provider(cfg)
    assert provider == "HF_PROVIDER_SENTINEL"


def test_nothing_configured_and_unreachable_returns_none():
    cfg = {"generate_provider": "ollama"}
    with patch.object(OllamaChatProvider, "health_check", return_value=False):
        with patch("lib.generate._make_hf", side_effect=ImportError):
            provider = get_generate_provider(cfg)
    assert provider is None


def test_request_body_contains_configured_think_and_model():
    captured = {}

    class _FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            import json
            return json.dumps({"message": {"content": "{}"}}).encode()

    def _fake_urlopen(req, timeout=None):
        import json
        captured["body"] = json.loads(req.data)
        return _FakeResp()

    provider = OllamaChatProvider(model="qwen3.5:4b", think=False, timeout=5.0)
    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        provider.generate("sys", "user")

    assert captured["body"]["model"] == "qwen3.5:4b"
    assert captured["body"]["think"] is False
