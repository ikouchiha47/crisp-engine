"""SignalDetector tested directly — no MemoryHookHandler needed."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.episode_writer import EpisodeWriter
from lib.hooks.signals import SignalDetector


def _detector():
    store = MagicMock()
    store.config = {}
    writer = EpisodeWriter(store)
    return SignalDetector(store, writer)


def test_detect_correction_matches_explicit_no():
    d = _detector()
    result = d._detect_correction("no, that's wrong", [])
    assert result is not None
    assert result["type"] == "explicit"


def test_detect_correction_none_for_neutral_message():
    d = _detector()
    assert d._detect_correction("looks good, ship it", []) is None


def test_detect_frustration_scores_multiple_patterns():
    d = _detector()
    result = d._detect_frustration("ugh, still not working, this is annoying")
    assert result is not None
    assert result["score"] > 0.5


def test_handle_stop_saves_correction_episode():
    d = _detector()
    result = d.handle_stop({
        "session_id": "s1", "cwd": "/proj",
        "message": "no, don't do that", "tool_outputs": [],
    })
    assert "correction" in result
    assert d.store.save_episode.called
