"""StructuralIndexer tested directly — no MemoryHookHandler needed, proves
the DI split actually decouples it."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.episode_writer import EpisodeWriter
from lib.hooks.structural_index import StructuralIndexer
from lib.store import MemoryEpisode


def _indexer():
    store = MagicMock()
    store.list_episodes.return_value = []
    store.config = {}
    writer = EpisodeWriter(store)
    return StructuralIndexer(store, writer), store


def test_is_indexed_fresh_true_when_matching_non_stale_episode_exists():
    indexer, store = _indexer()
    ep = MemoryEpisode.__new__(MemoryEpisode)
    ep.__dict__.update(dict(
        id="e1", source_path="/tmp/foo.py", category="code_element", tags=[],
    ))
    store.list_episodes.return_value = [ep]
    assert indexer.is_indexed_fresh("/tmp/foo.py") is True


def test_is_indexed_fresh_false_when_stale():
    indexer, store = _indexer()
    ep = MemoryEpisode.__new__(MemoryEpisode)
    ep.__dict__.update(dict(
        id="e1", source_path="/tmp/foo.py", category="code_element", tags=["stale"],
    ))
    store.list_episodes.return_value = [ep]
    assert indexer.is_indexed_fresh("/tmp/foo.py") is False


def test_is_indexed_fresh_false_when_no_match():
    indexer, store = _indexer()
    assert indexer.is_indexed_fresh("/tmp/nothing.py") is False


def test_dir_placeholder_stubs_are_gone():
    # docs/next-steps-sequence.md Phase 1.3 — _ensure_dir_entries deleted
    indexer, _ = _indexer()
    assert not hasattr(indexer, "ensure_dir_entries")
    assert not hasattr(indexer, "_ensure_dir_entries")
