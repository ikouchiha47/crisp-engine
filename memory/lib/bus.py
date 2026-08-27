"""Crisp observability event bus.

Fire-and-forget: emit() appends to an in-process ring buffer (deque) and
returns immediately -- no disk I/O on the hot hook path. A daemon background
thread drains the deque every 200ms and batch-writes to a WAL SQLite file.

If the writer thread fails or the db is unavailable, events are silently
dropped from the full deque -- same behaviour as statsd UDP loss. The hook
path is never blocked.

Usage:
    from lib.bus import emit, HookFired, EpisodeSaved
    emit(HookFired(session_id=sid, project=cwd, event_type="post_tool", tool_name="Edit", agent="claude_code"))

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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger("crisp.bus")

_RING_SIZE = 500
_FLUSH_INTERVAL = 0.2   # seconds
_DB_PATH = Path.home() / ".cache" / "crisp" / "events.db"
_KEEP_ROWS = 50_000
_KEEP_DAYS = 7

_deque: collections.deque = collections.deque(maxlen=_RING_SIZE)
_lock = threading.Lock()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Event types  (session_id + project are required on every event)
# ---------------------------------------------------------------------------

@dataclass
class HookFired:
    session_id: str
    project: str
    event_type: str       # pre_tool / post_tool / session_start / etc.
    tool_name: str = ""
    agent: str = ""
    _event: str = field(default="hook_fired", init=False, repr=False)


@dataclass
class WatcherMatched:
    session_id: str
    project: str
    watcher_name: str
    episode_count: int = 0
    _event: str = field(default="watcher_matched", init=False, repr=False)


@dataclass
class WatcherSkipped:
    session_id: str
    project: str
    watcher_name: str
    _event: str = field(default="watcher_skipped", init=False, repr=False)


@dataclass
class EpisodeSaved:
    session_id: str
    project: str
    id: str
    layer: int
    category: str
    importance: float
    embedded: bool = False
    _event: str = field(default="episode_saved", init=False, repr=False)


@dataclass
class EmbedResult:
    session_id: str
    project: str
    episode_id: str
    provider: str
    success: bool
    fallback_used: bool = False
    _event: str = field(default="embed_result", init=False, repr=False)


@dataclass
class ContextInjected:
    session_id: str
    project: str
    tool: str
    char_count: int
    _event: str = field(default="context_injected", init=False, repr=False)


@dataclass
class NarrateResult:
    """One L1/L2/L3 generation call — whether it used real LLM narrative or
    fell back to the template. Lets `crisp monitor`/logs show the real vs
    template ratio instead of that being invisible."""
    session_id: str
    project: str
    layer: int
    used_llm: bool
    _event: str = field(default="narrate_result", init=False, repr=False)


@dataclass
class HotMemoryPatched:
    session_id: str
    project: str
    kind: str  # "user" | "memory"
    char_count: int
    _event: str = field(default="hot_memory_patched", init=False, repr=False)


@dataclass
class InstinctAutoTrigger:
    session_id: str
    project: str
    evolved: int
    promoted: int
    _event: str = field(default="instinct_auto_trigger", init=False, repr=False)


@dataclass
class ReflectRan:
    l0_in: int
    session_id: str = ""
    project: str = ""
    l1_created: int = 0
    l2_created: int = 0
    l3_created: int = 0
    _event: str = field(default="reflect_ran", init=False, repr=False)


@dataclass
class FrustrationSignal:
    """One distill chunk's frustration score, LINKED to the substantive
    episodes extracted from the same chunk via payload_episode_ids — per
    docs/transcript-audit-findings.md §5.1's frustration_signal.payload
    field. Without this link the signal is structural/analytics-only and
    functionally inert (confirmed this session: persisted to the bus DB,
    never read back by anything) — payload_episode_ids is what lets a
    future consumer (e.g. reflector.py surfacing recurring frustration
    exits in L2) actually reach the preference/correction/reversal/
    undelivered episodes that rode in on this frustration, instead of a
    bare mood score with nothing to point to."""
    session_id: str
    project: str
    intensity: str  # none | low | med | high | extreme
    exit_type: str  # none | compact_under_fire | disagreement | delete_threat | session_abandon
    profanity_present: bool
    payload_episode_ids: list = field(default_factory=list)
    _event: str = field(default="frustration_signal", init=False, repr=False)


BusEvent = (
    HookFired | WatcherMatched | WatcherSkipped | EpisodeSaved | EmbedResult
    | ContextInjected | NarrateResult | HotMemoryPatched | InstinctAutoTrigger
    | ReflectRan | FrustrationSignal
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def emit(ev: BusEvent) -> None:
    """Append a typed event to the ring buffer. Never blocks, never raises."""
    try:
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        d = asdict(ev)
        d.pop("_event", None)
        event_name = ev._event  # type: ignore[attr-defined]
        _deque.append((ts, event_name, json.dumps(d, default=str)))
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


def frustration_signals_for_session(session_id: str, limit: int = 50) -> list[dict]:
    """Real, session-scoped read (not tail()'s since_id scan — the events
    table already has 10k+ real rows in this project, tail(0, huge_limit)
    would be a genuine perf problem, not a hypothetical one) — feeds
    reflector.py's L2 recurring-failure detection (ADR-004 item 4)."""
    try:
        con = _reader_con()
        rows = con.execute(
            "SELECT id, ts, payload FROM events "
            "WHERE event = 'frustration_signal' AND session = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        con.close()
        return [{"id": r[0], "ts": r[1], **json.loads(r[2])} for r in rows]
    except Exception:
        return []


def distinct_sessions(limit: int = 500) -> list[dict]:
    """Return distinct (session, project) pairs seen in the bus, most recent first."""
    try:
        con = _reader_con()
        rows = con.execute(
            "SELECT session, project, MAX(id) as last_id "
            "FROM events WHERE session IS NOT NULL AND session != '' "
            "GROUP BY session, project ORDER BY last_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        con.close()
        return [{"session": r[0], "project": r[1]} for r in rows]
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
        con.execute("DELETE FROM events WHERE ts < datetime(?, '-7 days')", (cutoff_ts,))
        con.execute("""
            DELETE FROM events WHERE id <= (
                SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET 50000
            )
        """)
        con.execute("PRAGMA optimize")
        con.commit()
    except Exception:
        pass
