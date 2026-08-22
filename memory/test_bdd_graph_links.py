"""Step definitions for features/graph_links.feature."""
from __future__ import annotations

import tempfile

from pytest_bdd import given, parsers, scenarios, then, when

from lib.consolidate import PruningService
from lib.retrieve import RetrievalOrchestrator
from lib.store import MemoryEpisode, MemoryStore

scenarios("features/graph_links.feature")


@given("a memory store", target_fixture="store")
def _store():
    return MemoryStore(tempfile.mkdtemp())


@given(parsers.parse('an episode "{episode_id}" with content "{content}" is saved'))
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


@given(parsers.parse('"{source_id}" is linked to "{target_id}"'))
def _link(store, source_id, target_id):
    store.add_link(source_id, target_id, link_type="relates_to", strength=0.9)


@when(parsers.parse('a graph-expanded search for "{query}" is run'), target_fixture="search_error")
def _search(store, query):
    try:
        RetrievalOrchestrator(store).search(query)
        return None
    except Exception as e:  # noqa: BLE001 — capturing for the Then step, not swallowing
        return e


@then("the search must complete without raising an exception")
def _assert_search_ok(search_error):
    assert search_error is None, f"search raised: {search_error!r}"


@when("pruning is run", target_fixture="prune_error")
def _prune(store):
    try:
        PruningService(store).prune()
        return None
    except Exception as e:  # noqa: BLE001
        return e


@then("pruning must complete without raising an exception")
def _assert_prune_ok(prune_error):
    assert prune_error is None, f"prune raised: {prune_error!r}"
