"""Step definitions for features/file_state_identity.feature."""
from __future__ import annotations

import tempfile

from pytest_bdd import given, parsers, scenarios, then, when

from lib.store import MemoryEpisode, MemoryStore

scenarios("features/file_state_identity.feature")

_counter = {"n": 0}


@given("a memory store", target_fixture="store")
def _store():
    return MemoryStore(tempfile.mkdtemp())


@given(parsers.parse(
    'a file-sourced episode "{episode_id}" for path "{path}" with source hash "{source_hash}" is saved'
))
def _save_file_episode(store, episode_id, path, source_hash):
    _counter["n"] += 1
    ep = MemoryEpisode(
        id=episode_id,
        session_id="sess_test",
        timestamp="2026-01-01T00:00:00Z",
        title=episode_id,
        content=f"distinct content {_counter['n']}",
        category="test",
        source_type="file",
        source_path=path,
        source_hash=source_hash,
    )
    store.save_episode(ep)


@when(parsers.parse('I ask for the file state of "{path}"'), target_fixture="file_state")
def _get_file_state(store, path):
    return store.get_file_state(path)


@then(parsers.parse('the file state must be "{expected}"'))
def _assert_file_state(file_state, expected):
    assert file_state == expected, f"expected {expected!r}, got {file_state!r}"


@then(parsers.parse('only one episode must exist for path "{path}"'))
def _assert_single_episode(store, path):
    matches = [ep for ep in store.list_episodes() if ep.source_path == path]
    assert len(matches) == 1, f"expected 1 episode for {path}, found {len(matches)}: {[e.id for e in matches]}"
