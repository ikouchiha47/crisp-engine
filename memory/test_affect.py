"""Black-box tests for lib/affect.py. Uses dspy.utils.DummyLM — dspy's own
test double, returning canned structured answers with no real network call.
No mocks of our own code; the real model (Ollama and Gemini) was already
verified live in this session."""
import dspy

from lib.affect import (
    correction_episode,
    distill_to_episodes,
    distill_transcript,
    preference_episode,
    reversal_episode,
    undelivered_episode,
)

_EMPTY_ANSWER = {
    "preferences": [], "corrections": [], "reversals": [], "undelivered": [],
    "frustration_present": False, "frustration_intensity": "none",
    "exit_type": "none", "profanity_present": False, "summary": "",
}


def _answer(**overrides):
    out = dict(_EMPTY_ANSWER)
    out.update(overrides)
    return out


def test_distill_transcript_parses_valid_json():
    lm = dspy.utils.DummyLM([
        _answer(preferences=["use uv"], corrections=["port is 8080 not 3000"], summary="talked ports"),
    ])
    result = distill_transcript("some transcript", lm)
    assert result["preferences"] == ["use uv"]
    assert result["corrections"] == ["port is 8080 not 3000"]
    assert result["summary"] == "talked ports"
    assert result["reversals"] == []
    assert result["undelivered"] == []
    assert result["frustration_present"] is False


def test_distill_transcript_none_lm_returns_empty():
    result = distill_transcript("some transcript", None)
    assert result["preferences"] == []
    assert result["corrections"] == []
    assert result["reversals"] == []
    assert result["undelivered"] == []
    assert result["frustration_present"] is False
    assert result["frustration_intensity"] == "none"
    assert result["exit_type"] == "none"
    assert result["summary"] == ""


def test_distill_transcript_empty_text_returns_empty_without_calling_lm():
    lm = dspy.utils.DummyLM([_answer(preferences=["x"])])
    result = distill_transcript("   ", lm)
    assert result["preferences"] == []
    assert lm.history == []


def test_distill_transcript_captures_reversal_and_undelivered():
    lm = dspy.utils.DummyLM([
        _answer(
            reversals=["OpenCode support deferred"],
            undelivered=["real e2e tests were requested but not shipped"],
            frustration_present=True, frustration_intensity="high",
            exit_type="disagreement", profanity_present=True,
        ),
    ])
    result = distill_transcript("some transcript", lm)
    assert result["reversals"] == ["OpenCode support deferred"]
    assert result["undelivered"] == ["real e2e tests were requested but not shipped"]
    assert result["frustration_present"] is True
    assert result["frustration_intensity"] == "high"
    assert result["exit_type"] == "disagreement"
    assert result["profanity_present"] is True


def test_distill_transcript_rejects_invalid_enum_values():
    """A model hallucinating an out-of-set enum value must not corrupt the
    result — falls back to the safe default rather than storing garbage."""
    lm = dspy.utils.DummyLM([_answer(frustration_intensity="mega", exit_type="rage_quit")])
    result = distill_transcript("some transcript", lm)
    assert result["frustration_intensity"] == "none"
    assert result["exit_type"] == "none"


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


def test_reversal_episode_shape():
    ep = reversal_episode("sess1", "OpenCode support deferred")
    assert ep.category == "reversal"
    assert ep.layer == 1
    assert ep.is_permanent is True
    assert ep.content == "OpenCode support deferred"


def test_undelivered_episode_shape():
    ep = undelivered_episode("sess1", "real e2e tests requested, not shipped")
    assert ep.category == "undelivered"
    assert ep.layer == 1
    assert ep.is_permanent is False  # open item, not a settled standing fact


def test_distill_to_episodes_builds_all_kinds():
    lm = dspy.utils.DummyLM([
        _answer(
            preferences=["use uv"], corrections=["fix the port"],
            reversals=["OpenCode deferred"], undelivered=["graph never shipped"],
            summary="discussed tooling",
        ),
    ])
    episodes, summary, frustration = distill_to_episodes("sess1", "transcript", lm)
    cats = sorted(e.category for e in episodes)
    assert cats == ["correction", "preference", "reversal", "undelivered"]
    assert summary == "discussed tooling"
    assert frustration == {
        "present": False, "intensity": "none", "exit_type": "none", "profanity_present": False,
    }


def test_distill_to_episodes_empty_when_nothing_extracted():
    lm = dspy.utils.DummyLM([_answer()])
    episodes, summary, frustration = distill_to_episodes("sess1", "transcript", lm)
    assert episodes == []
    assert summary == ""
    assert frustration["present"] is False


def test_distill_to_episodes_threads_prior_summary_into_prompt():
    lm = dspy.utils.DummyLM([_answer(summary="next")])
    distill_to_episodes("sess1", "transcript", lm, prior_summary="earlier chunk was about X")
    sent_messages = lm.history[-1]["messages"]
    assert any("earlier chunk was about X" in m.get("content", "") for m in sent_messages)
