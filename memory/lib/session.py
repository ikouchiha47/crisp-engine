"""Session registry — persistent, idempotent record of every agent session.

Written at session_start by the hook (upsert_session). Queried by the monitor
(/api/sessions) to populate the sidebar without scanning episode YAML files or
relying on the event stream.

Uses the same SQLite DB as the event bus (WAL, same pragmas) but in a separate
'sessions' table so it survives event housekeeping (events are pruned to 50k
rows; sessions are kept forever).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger("crisp.session")

_DB_PATH = Path.home() / ".cache" / "crisp" / "events.db"


def _pragmas(con: sqlite3.Connection) -> None:
    con.executescript("""
        PRAGMA journal_mode    = WAL;
        PRAGMA synchronous     = NORMAL;
        PRAGMA busy_timeout    = 5000;
    """)


def _ensure_table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id  TEXT PRIMARY KEY,
            project     TEXT    NOT NULL DEFAULT '',
            agent       TEXT    NOT NULL DEFAULT '',
            first_seen  TEXT    NOT NULL,
            last_seen   TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_sessions_last ON sessions(last_seen)")
    con.commit()


def upsert(session_id: str, project: str = "", agent: str = "") -> None:
    """Idempotently register a session. Safe to call multiple times per session."""
    if not session_id:
        return
    ts = datetime.now(timezone.utc).isoformat()
    try:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _pragmas(con)
        _ensure_table(con)
        con.execute(
            """INSERT INTO sessions(session_id, project, agent, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                 last_seen = excluded.last_seen,
                 project   = CASE WHEN excluded.project != '' THEN excluded.project ELSE sessions.project END,
                 agent     = CASE WHEN excluded.agent   != '' THEN excluded.agent   ELSE sessions.agent   END
            """,
            (session_id, project or "", agent or "", ts, ts),
        )
        con.commit()
        con.close()
    except Exception as exc:
        _log.debug("upsert failed: %s", exc)


def all_sessions() -> list[dict]:
    """Return all registered sessions, most recently seen first."""
    try:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _pragmas(con)
        _ensure_table(con)
        rows = con.execute(
            "SELECT session_id, project, agent, first_seen, last_seen "
            "FROM sessions ORDER BY last_seen DESC"
        ).fetchall()
        con.close()
        return [
            {"session": r[0], "project": r[1], "agent": r[2],
             "first_seen": r[3], "last_seen": r[4]}
            for r in rows
        ]
    except Exception as exc:
        _log.debug("all_sessions failed: %s", exc)
        return []
