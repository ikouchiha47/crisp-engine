"""Step definitions for features/dedup_correctness.feature."""
from __future__ import annotations

import tempfile

from pytest_bdd import given, parsers, scenarios, then, when

from lib.store import MemoryEpisode, MemoryStore

scenarios("features/dedup_correctness.feature")


@given("a memory store", target_fixture="store")
def _store():
    return MemoryStore(tempfile.mkdtemp())


@given(parsers.parse('an episode "{episode_id}" with content "{content}" is saved'))
@when(parsers.parse('a new episode "{episode_id}" with content "{content}" is saved'))
def _save_episode(store, episode_id, content):
    ep = MemoryEpisode(
        id=episode_id,
        session_id="sess_test",
        timestamp="2026-01-01T00:00:00Z",
        title=episode_id,
        content=content,
        category="test",
    )
    store.save_episode(ep)


@when(parsers.parse('episode "{episode_id}" is deleted'))
def _delete_episode(store, episode_id):
    store.delete_episode(episode_id)


@then(parsers.parse('episode "{episode_id}" must exist in the store'))
def _assert_exists(store, episode_id):
    assert store.get_episode(episode_id) is not None, (
        f"episode {episode_id} was not persisted — "
        "stale hash_cache entry from the deleted original silently dropped it"
    )
