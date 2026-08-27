"""End-to-end tests for lib/narrate.py against a real local Ollama model
(qwen3.5:4b — the exact config verified live earlier this session). No
mocks, no fake LMs. Requires Ollama running locally with the model pulled;
these tests hit the real network and take real time.
"""
import pytest

from lib.config import load as cfg_load
from lib.dspy_lm import get_dspy_lm
from lib.narrate import narrate_l1, narrate_l2, narrate_l3


@pytest.fixture(scope="module")
def lm():
    cfg = cfg_load(overrides={
        "memory_model_config": {
            "model": "ollama_chat/qwen3.5:4b",
            "api_base": "http://localhost:11434",
            "timeout": 90,
            "think": False,
        },
    })
    m = get_dspy_lm(cfg)
    if m is None:
        pytest.skip("no reachable dspy lm (Ollama not running / model not pulled)")
    return m


# ── pure logic, no lm needed ────────────────────────────────────────────────

def test_narrate_l1_none_lm_returns_none():
    assert narrate_l1(None, ["something"]) is None


def test_narrate_l1_empty_episodes_returns_none():
    assert narrate_l1(_NeverCalled(), []) is None


def test_narrate_l2_none_lm_returns_none():
    assert narrate_l2(None, ["l1 one"]) is None


def test_narrate_l3_none_lm_returns_none():
    assert narrate_l3(None, ["l2 one"]) is None


class _NeverCalled:
    def __call__(self, *args, **kwargs):
        raise AssertionError("lm must not be called for empty input")


# ── real end-to-end, real model, real output ────────────────────────────────

def test_narrate_l1_real_call_produces_real_narrative(lm):
    episodes = [
        "User asked to fix the call-graph truncation bug in callwalk.py where "
        "calls past 2000 characters into a function body were invisible.",
        "Claude found that treesitter_strategy.py truncates elem.body to "
        "body_str[:2000], and fixed callwalk.py to read the untruncated "
        "source directly from disk instead of re-parsing the truncated body.",
        "User confirmed the fix by checking main() in hooks.py, which now "
        "shows both real callers of build_context_block instead of one.",
    ]
    result = narrate_l1(lm, episodes)
    print("\n--- REAL L1 NARRATIVE ---\n" + str(result) + "\n-------------------------")
    assert result is not None
    assert len(result) > 20
    # must reflect the actual content, not be a generic template
    assert "personal development" not in result.lower()


def test_narrate_l2_real_call_produces_topic_and_synthesis(lm):
    l1s = [
        "Session 1: Built a cross-file call graph using tree-sitter and networkx, "
        "added crisp graph build/show/explain/path commands.",
        "Session 2: Fixed a truncation bug where calls past 2000 characters in a "
        "function body were invisible to the call graph.",
        "Session 3: Added .venv exclusion so the graph builder never walks "
        "installed library code as if it were project source.",
    ]
    result = narrate_l2(lm, l1s)
    print("\n--- REAL L2 TOPIC + SYNTHESIS ---\n" + str(result) + "\n----------------------------------")
    assert result is not None
    topic, synthesis = result
    assert len(topic) > 0
    assert len(synthesis) > 20


def test_narrate_l3_real_call_produces_real_arc_not_personal_development(lm):
    l2s = [
        "Topic: Code graph infrastructure. Built and hardened a cross-file "
        "call graph for the codebase: tree-sitter extraction, symbol "
        "resolution with confidence labels, a persisted graph, and CLI "
        "query commands. Fixed a real truncation bug and a .venv leakage bug.",
        "Topic: Hook architecture refactor. Split a 1100-line god-object "
        "hook handler into single-responsibility collaborators: episode "
        "writer, structural indexer, context injector, watcher dispatch, "
        "transcript service, signal detector.",
    ]
    result = narrate_l3(lm, l2s)
    print("\n--- REAL L3 ARC NAME + META-LESSONS ---\n" + str(result) + "\n-----------------------------------------")
    assert result is not None
    name, lessons = result
    assert name.strip().lower() != "personal development"
    assert len(lessons) >= 1
    for lesson in lessons:
        assert len(lesson) > 5
