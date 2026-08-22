"""Step definitions for features/json_state_robustness.feature."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pytest_bdd import given, scenarios, then, when

from lib.store import MemoryEpisode, MemoryStore

scenarios("features/json_state_robustness.feature")


@given("a memory store directory", target_fixture="store_dir")
def _store_dir():
    return Path(tempfile.mkdtemp())


@given("a memory store", target_fixture="store")
def _store():
    return MemoryStore(tempfile.mkdtemp())


@given("its hashes.json contains two concatenated JSON objects like a torn write")
def _write_corrupt_hashes(store_dir):
    cache_dir = store_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    obj1 = {"hash1": "episode_a", "hash2": "episode_b"}
    obj2 = {"hash3": "episode_c"}
    torn = json.dumps(obj1) + "garbage_tail" + json.dumps(obj2)
    (cache_dir / "hashes.json").write_text(torn)


@when("a MemoryStore is opened against that directory", target_fixture="open_result")
def _open_store(store_dir):
    try:
        return {"store": MemoryStore(str(store_dir)), "error": None}
    except Exception as e:  # noqa: BLE001
        return {"store": None, "error": e}


@then("it must not raise an exception")
def _assert_no_exception(open_result):
    assert open_result["error"] is None, f"raised: {open_result['error']!r}"


@then("the hash cache must contain the first object's entries")
def _assert_first_object_recovered(open_result):
    store = open_result["store"]
    assert store.hash_cache.get("hash1") == "episode_a"
    assert store.hash_cache.get("hash2") == "episode_b"


@when("50 episodes with distinct content are saved in sequence")
def _save_many(store):
    for i in range(50):
        ep = MemoryEpisode(
            id=f"ep{i}",
            session_id="sess_test",
            timestamp="2026-01-01T00:00:00Z",
            title=f"ep{i}",
            content=f"distinct content number {i}",
            category="test",
        )
        store.save_episode(ep)
        # Assert validity after every single save, not just at the end —
        # this is the actual claim: no torn write ever hits disk, not just
        # "it's fine by the time we check."
        json.loads(store.hash_cache_file.read_text())


@then("hashes.json must be valid JSON after every single save")
def _assert_final_valid(store):
    data = json.loads(store.hash_cache_file.read_text())
    assert len(data) == 50
