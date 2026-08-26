"""Crisp observability event bus.

Fire-and-forget: emit() appends to an in-process ring buffer (deque) and
returns immediately -- no disk I/O on the hot hook path. A daemon background
thread drains the deque every 200ms and batch-writes to a WAL SQLite file.

If the writer thread fails or the db is unavailable, events are silently
dropped from the full deque -- same behaviour as statsd UDP loss. The hook
path is never blocked.

Usage:
    from lib.bus import emit
    emit("episode_saved", {"id": ep.id, "layer": ep.layer})

Clients tail with:
    from lib.bus import tail
    for event in tail(since_id=0, limit=50): ...
"""

from __future__ import annotations

import collections
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_log = logging.getLogger("crisp.bus")

_RING_SIZE = 500
_FLUSH_INTERVAL = 0.2   # seconds
_DB_PATH = Path.home() / ".cache" / "crisp" / "events.db"
_KEEP_ROWS = 50_000
_KEEP_DAYS = 7

_deque: collections.deque = collections.deque(maxlen=_RING_SIZE)
_lock = threading.Lock()   # protects _deque reads in the flusher only
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def emit(event: str, payload: dict[str, Any] | None = None, **kwargs) -> None:
    """Append an event to the ring buffer. Never blocks, never raises."""
    try:
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        data = payload or {}
        if kwargs:
            data = {**data, **kwargs}
        _deque.append((ts, event, json.dumps(data, default=str)))
        _ensure_thread()
    except Exception:
        pass


def tail(since_id: int = 0, limit: int = 100) -> list[dict]:
    """Return up to `limit` events with id > since_id, oldest first."""
    try:
        con = _reader_con()
        rows = con.execute(
            "SELECT id, ts, event, session, project, payload "
            "FROM events WHERE id > ? ORDER BY id LIMIT ?",
            (since_id, limit),
        ).fetchall()
        con.close()
        return [
            {
                "id": r[0], "ts": r[1], "event": r[2],
                "session": r[3], "project": r[4],
                "payload": json.loads(r[5]),
            }
            for r in rows
        ]
    except Exception:
        return []


def latest_id() -> int:
    """Return the highest event id in the store, or 0."""
    try:
        con = _reader_con()
        row = con.execute("SELECT MAX(id) FROM events").fetchone()
        con.close()
        return row[0] or 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Internal: writer thread
# ---------------------------------------------------------------------------

def _ensure_thread() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    with _thread_lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_flush_loop, daemon=True, name="crisp-bus-writer")
        _thread.start()


def _flush_loop() -> None:
    try:
        con = _writer_con()
    except Exception as exc:
        _log.warning("bus: could not open events db: %s", exc)
        return

    while True:
        try:
            threading.Event().wait(_FLUSH_INTERVAL)
            _flush_once(con)
        except Exception as exc:
            _log.debug("bus: flush error: %s", exc)


def _flush_once(con: sqlite3.Connection) -> None:
    if not _deque:
        return
    batch = []
    # drain without holding the GIL for long
    try:
        while True:
            batch.append(_deque.popleft())
    except IndexError:
        pass
    if not batch:
        return
    con.executemany(
        "INSERT INTO events(ts, event, session, project, payload) VALUES (?,?,?,?,?)",
        [
            (ts, event, _extract(payload, "session_id"), _extract(payload, "project"), payload)
            for ts, event, payload in batch
        ],
    )
    con.commit()


def _extract(payload_json: str, key: str) -> str | None:
    try:
        return json.loads(payload_json).get(key)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Internal: connections
# ---------------------------------------------------------------------------

def _apply_pragmas(con: sqlite3.Connection) -> None:
    con.executescript("""
        PRAGMA journal_mode    = WAL;
        PRAGMA synchronous     = NORMAL;
        PRAGMA cache_size      = -64000;
        PRAGMA temp_store      = MEMORY;
        PRAGMA mmap_size       = 30000000;
        PRAGMA busy_timeout    = 5000;
        PRAGMA wal_autocheckpoint = 5000;
    """)


def _writer_con() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    _apply_pragmas(con)
    con.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       TEXT    NOT NULL,
            event    TEXT    NOT NULL,
            session  TEXT,
            project  TEXT,
            payload  TEXT    NOT NULL DEFAULT '{}'
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_id ON events(id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session)")
    con.commit()
    _housekeep(con)
    return con


def _reader_con() -> sqlite3.Connection:
    if not _DB_PATH.exists():
        raise FileNotFoundError(_DB_PATH)
    con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    _apply_pragmas(con)
    return con


def _housekeep(con: sqlite3.Connection) -> None:
    """Delete rows beyond retention limits. Run once on writer startup."""
    try:
        cutoff_ts = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        # keep last 7 days
        con.execute("DELETE FROM events WHERE ts < datetime(?, '-7 days')", (cutoff_ts,))
        # keep last 50k rows
        con.execute("""
            DELETE FROM events WHERE id <= (
                SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET 50000
            )
        """)
        con.execute("PRAGMA optimize")
        con.commit()
    except Exception:
        pass
