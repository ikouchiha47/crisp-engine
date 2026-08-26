"""Black-box tests for lib/affect.py. The chat provider is a fake — no real
Ollama calls in the unit suite (the real model was already verified live
in this session, and again via the config-driven smoke test)."""
from lib.affect import correction_episode, distill_to_episodes, distill_transcript, preference_episode


class _FakeProvider:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def generate(self, system, user):
        self.calls.append((system, user))
        return self.response


def test_distill_transcript_parses_valid_json():
    provider = _FakeProvider('{"preferences": ["use uv"], "corrections": ["port is 8080 not 3000"]}')
    result = distill_transcript("some transcript", provider)
    assert result == {"preferences": ["use uv"], "corrections": ["port is 8080 not 3000"]}


def test_distill_transcript_none_provider_returns_empty():
    result = distill_transcript("some transcript", None)
    assert result == {"preferences": [], "corrections": []}


def test_distill_transcript_empty_text_returns_empty_without_calling_provider():
    provider = _FakeProvider('{"preferences": ["x"], "corrections": []}')
    result = distill_transcript("   ", provider)
    assert result == {"preferences": [], "corrections": []}
    assert provider.calls == []


def test_distill_transcript_malformed_json_returns_empty_not_raises():
    provider = _FakeProvider("not json at all")
    result = distill_transcript("transcript", provider)
    assert result == {"preferences": [], "corrections": []}


def test_distill_transcript_non_dict_json_returns_empty():
    provider = _FakeProvider('["preferences", "corrections"]')
    result = distill_transcript("transcript", provider)
    assert result == {"preferences": [], "corrections": []}


def test_distill_transcript_provider_returns_none_returns_empty():
    provider = _FakeProvider(None)
    result = distill_transcript("transcript", provider)
    assert result == {"preferences": [], "corrections": []}


def test_distill_transcript_drops_non_string_junk_items():
    provider = _FakeProvider('{"preferences": ["real", "", null, 5], "corrections": []}')
    result = distill_transcript("transcript", provider)
    assert result["preferences"] == ["real", "5"]


def test_preference_episode_shape():
    ep = preference_episode("sess1", "always use uv")
    assert ep.category == "preference"
    assert ep.layer == 1
    assert ep.is_permanent is True
    assert ep.content == "always use uv"


def test_correction_episode_shape():
    ep = correction_episode("sess1", "port is 8080 not 3000")
    assert ep.category == "correction"
    assert ep.layer == 1
    assert ep.correction_applied is True
    assert ep.is_permanent is True


def test_distill_to_episodes_builds_both_kinds():
    provider = _FakeProvider('{"preferences": ["use uv"], "corrections": ["fix the port"]}')
    episodes = distill_to_episodes("sess1", "transcript", provider)
    cats = sorted(e.category for e in episodes)
    assert cats == ["correction", "preference"]


def test_distill_to_episodes_empty_when_nothing_extracted():
    provider = _FakeProvider('{"preferences": [], "corrections": []}')
    episodes = distill_to_episodes("sess1", "transcript", provider)
    assert episodes == []
