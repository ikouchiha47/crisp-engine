"""Step definitions for features/pruning_lifecycle.feature."""
from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

from pytest_bdd import given, parsers, scenarios, then, when

from lib.consolidate import PruningService
from lib.store import MemoryEpisode, MemoryStore

scenarios("features/pruning_lifecycle.feature")


@given("a memory store", target_fixture="store")
def _store():
    return MemoryStore(tempfile.mkdtemp())


@given(parsers.parse('an episode "{episode_id}" with timestamp {days:d} days ago is saved'))
def _save_old_episode(store, episode_id, days):
    ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    ep = MemoryEpisode(
        id=episode_id,
        session_id="sess_test",
        timestamp=ts,
        layer=0,
        title=episode_id,
        content=f"content for {episode_id}",
        category="test",
    )
    store.save_episode(ep)


@given(parsers.parse('"{episode_id}" is already archived with an archive file mtime {days:d} days old'))
def _archive_and_backdate(store, episode_id, days):
    ep = store.get_episode(episode_id)
    src = store._filepath(episode_id, ep.layer)
    archive_dir = store.layers_path / f"l{ep.layer}" / "archived"
    archive_dir.mkdir(exist_ok=True)
    dest = archive_dir / src.name
    src.rename(dest)
    old_time = time.time() - days * 86400
    os.utime(dest, (old_time, old_time))


@when("pruning is run")
def _prune(store):
    PruningService(store).prune()


@then(parsers.parse('episode "{episode_id}" must be archived'))
def _assert_archived(store, episode_id):
    for layer in range(4):
        archived_path = store.layers_path / f"l{layer}" / "archived" / f"{episode_id}.md"
        if archived_path.exists():
            return
    raise AssertionError(
        f"episode {episode_id} was never archived — "
        "archive_low_value likely still gates on parent_id, which a lone episode never has"
    )


@then(parsers.parse('episode "{episode_id}" must be permanently deleted'))
def _assert_deleted(store, episode_id):
    for layer in range(4):
        for sub in ("", "archived/"):
            p = store.layers_path / f"l{layer}" / sub / f"{episode_id}.md"
            assert not p.exists(), f"episode {episode_id} still exists at {p}"
