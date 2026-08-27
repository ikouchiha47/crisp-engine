"""Persists the FULL structured output of every narrate_*/distill_transcript
call — not just the flattened string(s) that end up in a MemoryEpisode.

Real bug this fixes: narrate_l1 returns only `narrative: str`, narrate_l2
only `(topic, synthesis)`, narrate_l3 only `(arc_name, meta_lessons)`,
distill_transcript only the 9 individual fields split across episode files
and the bus — the full structured dspy.Predict/Refine output (every typed
field together, as dspy actually returned it) was never persisted as one
record anywhere. Confirmed same pattern repeated across every layer built
this session, not an isolated mistake in one module.

Separate SQLite table (own file, not the bus events.db — different
retention/query shape, this is a debug/audit trail keyed by generation,
the bus is a live event stream) so it composes without touching bus.py.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_DB_PATH = Path.home() / ".cache" / "crisp" / "generations.db"


def _con() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_DB_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS generations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL,
            layer       TEXT    NOT NULL,
            episode_id  TEXT,
            session_id  TEXT,
            project     TEXT,
            output_json TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_gen_episode ON generations(episode_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_gen_session ON generations(session_id)")
    con.commit()
    return con


def log_generation(
    layer: str,
    output: Dict[str, Any],
    episode_id: Optional[str] = None,
    session_id: str = "",
    project: str = "",
) -> None:
    """Persist one generation's full structured output. Never raises —
    a logging failure must not break the real pipeline it's observing."""
    try:
        con = _con()
        con.execute(
            "INSERT INTO generations(ts, layer, episode_id, session_id, project, output_json) "
            "VALUES (?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                layer, episode_id, session_id, project,
                json.dumps(output, default=str),
            ),
        )
        con.commit()
        con.close()
    except Exception:
        pass


def generations_for_episode(episode_id: str) -> list:
    """Real lookup: given an episode id, return the full structured output
    that actually produced it — recovers exactly what was previously lost."""
    try:
        con = _con()
        rows = con.execute(
            "SELECT id, ts, layer, output_json FROM generations WHERE episode_id = ? ORDER BY id",
            (episode_id,),
        ).fetchall()
        con.close()
        return [
            {"id": r[0], "ts": r[1], "layer": r[2], "output": json.loads(r[3])}
            for r in rows
        ]
    except Exception:
        return []
