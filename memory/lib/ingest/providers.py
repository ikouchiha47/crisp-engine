"""SessionProvider implementations: Claude Code JSONL and OpenCode SQLite.

Each provider encapsulates its own on-disk format (Provider/Strategy
pattern). Both return the shared `SessionIR`. Providers are normalizers:
they must never mutate the source stores.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from lib.time_utils import UTC_TZ as UTC
from pathlib import Path

from .ids import turn_id
from .types import (
    ContinuationLink,
    IngestConfig,
    SessionIR,
    ToolCallIR,
    TurnIR,
)

log = logging.getLogger(__name__)

CLAUDE_CODE = "claude_code"
OPENCODE = "opencode"


class ClaudeJSONLProvider:
    """Parses ~/.claude/projects/<encoded-cwd>/<uuid>.jsonl transcripts."""

    name = CLAUDE_CODE

    def _root(self, config: IngestConfig) -> Path:
        return Path(config.claude_projects_root or "~/.claude/projects").expanduser()

    def discover(self, config: IngestConfig) -> Iterator[str]:
        root = self._root(config)
        if not root.exists():
            log.warning("claude projects root missing: %s", root)
            return
        for p in sorted(root.rglob("*.jsonl")):
            if p.stat().st_size == 0:
                continue
            yield str(p)

    def parse(self, config: IngestConfig, session_ref: str) -> SessionIR:
        path = Path(session_ref)
        session_id = path.stem
        events = _read_events(path)
        return build_claude_session(session_id, events)


# ---------------------------------------------------------------------------
# Pure helpers (testable without a provider instance)
# ---------------------------------------------------------------------------
def _read_events(path: Path) -> list[dict]:
    events: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("skipping unparseable jsonl line in %s", path)
    return events


def _flatten_content(content, is_meta: bool) -> tuple[str, list[ToolCallIR], str]:
    """Flatten Claude message.content into (text, tools, text_kind)."""
    if content is None:
        return "", [], "meta"
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.lower().startswith("<local-command"):
            return stripped, [], "command"
        return stripped, [], "meta" if is_meta else "human"
    texts: list[str] = []
    tools: list[ToolCallIR] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            texts.append(str(block.get("text") or ""))
        elif btype == "tool_use":
            tools.append(ToolCallIR(name=str(block.get("name") or "unknown"), ok=None))
        elif btype == "tool_result":
            err = block.get("is_error")
            tools.append(
                ToolCallIR(
                    name="tool_result",
                    ok=False if err else None,
                    error="error" if err else None,
                )
            )
    text = "\n".join(t for t in texts if t).strip()
    if tools and text:
        kind = "mixed"
    elif tools:
        kind = "tool"
    else:
        kind = "meta" if is_meta else "human"
    return text, tools, kind


_TAIL_TRACKED = re.compile(r"trackedFileBackups")


def build_claude_session(session_id: str, events: list[dict]) -> SessionIR:
    dialogue = [e for e in events if e.get("type") in ("user", "assistant")]
    main = [e for e in dialogue if not e.get("isSidechain")]
    main_sorted = sorted(
        main, key=lambda e: (e.get("timestamp") or "", e.get("uuid") or "")
    )

    turns: list[TurnIR] = []
    project_path = ""
    for e in main_sorted:
        msg = e.get("message") or {}
        role = "user" if e.get("type") == "user" else "assistant"
        content = msg.get("content")
        is_meta = bool(e.get("isMeta"))
        text, tools, kind = _flatten_content(content, is_meta)

        if not project_path and e.get("cwd"):
            project_path = str(e["cwd"])

        usage = msg.get("usage") or {}
        parent = e.get("parentUuid")
        parent_turn = turn_id(CLAUDE_CODE, session_id, parent) if parent else None

        turns.append(
            TurnIR(
                turn_id=turn_id(CLAUDE_CODE, session_id, e.get("uuid", "")),
                provider_msg_id=str(e.get("uuid", "")),
                parent_turn_id=parent_turn,
                role=role,  # type: ignore[assignment]
                ts=str(e.get("timestamp") or ""),
                text=text,
                text_kind=kind,  # type: ignore[assignment]
                tools=tuple(tools),
                model=msg.get("model"),
                usage_input=usage.get("input_tokens"),
                usage_output=usage.get("output_tokens"),
                usage_cache_read=usage.get("cache_read_input_tokens"),
                sidechain=bool(e.get("isSidechain")),
            )
        )

    work_landed = any(
        e.get("type") == "file-history-delta" for e in events
    ) or any(
        e.get("type") == "file-history-snapshot"
        and _TAIL_TRACKED.search(str(e.get("snapshot") or {}))
        for e in events
    )

    models = tuple(
        str(m)
        for m in dict.fromkeys(
            (e.get("message") or {}).get("model")
            for e in main_sorted
            if e.get("type") == "assistant"
            if (e.get("message") or {}).get("model")
        )
        if m
    )

    side_threads: tuple[tuple[TurnIR, ...], ...] = ()
    return SessionIR(
        provider=CLAUDE_CODE,  # type: ignore[assignment]
        session_id=session_id,
        project_path=project_path,
        turns=tuple(turns),
        title=_first_title(events),
        started_at=turns[0].ts if turns else "",
        ended_at=turns[-1].ts if turns else "",
        model_hints=models,
        links=tuple(_claude_links(events, session_id)),
        n_user=sum(1 for t in turns if t.role == "user"),
        n_assistant=sum(1 for t in turns if t.role == "assistant"),
        n_tool_calls=sum(len(t.tools) for t in turns),
        n_tool_errors=sum(1 for t in turns for x in t.tools if x.ok is False),
        work_landed=work_landed,
        side_threads=side_threads,
    )


def _first_title(events: list[dict]) -> str | None:
    for e in events:
        if e.get("type") == "ai-title" and e.get("title"):
            return str(e["title"])
    return None


_COMPACT_MARKERS = ("compact", "context summarized", "conversation summary", "summary")


def _claude_links(events: list[dict], session_id: str) -> list[ContinuationLink]:
    links: list[ContinuationLink] = []
    for e in events:
        if e.get("type") not in ("system", "user"):
            continue
        text = e.get("message", {}).get("content")
        if isinstance(text, str) and any(m in text.lower() for m in _COMPACT_MARKERS):
            links.append(
                ContinuationLink(
                    kind="compact_summary",
                    from_session=session_id,
                    note="compaction marker detected",
                )
            )
    return links


# ---------------------------------------------------------------------------
# OpenCode SQLite provider (read-only)
# ---------------------------------------------------------------------------
class OpenCodeSQLiteProvider:
    """Reads ~/.local/share/opencode/opencode.db (and JSON storage mirror)."""

    name = OPENCODE

    def _db(self, config: IngestConfig) -> Path:
        return Path(config.opencode_db or "~/.local/share/opencode/opencode.db").expanduser()

    def _connect(self, config: IngestConfig) -> sqlite3.Connection:
        db = self._db(config)
        if not db.exists():
            raise FileNotFoundError(f"opencode db not found: {db}")
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def discover(self, config: IngestConfig) -> Iterator[str]:
        conn = self._connect(config)
        try:
            q = "SELECT id FROM session"
            args: tuple = ()
            if config.project_path_filter:
                q += " WHERE directory LIKE ?"
                args = (f"%{config.project_path_filter}%",)
            q += " ORDER BY time_updated DESC"
            for row in conn.execute(q, args):
                yield str(row["id"])
        finally:
            conn.close()

    def parse(self, config: IngestConfig, session_ref: str) -> SessionIR:
        conn = self._connect(config)
        try:
            return build_opencode_session(conn, session_ref)
        finally:
            conn.close()


def build_opencode_session(conn: sqlite3.Connection, session_id: str) -> SessionIR:
    sess = conn.execute("SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()
    if sess is None:
        raise ValueError(f"session not found: {session_id}")
    sess = dict(sess)

    messages = conn.execute(
        "SELECT id, time_created, data FROM message "
        "WHERE session_id = ? ORDER BY time_created ASC, id ASC",
        (session_id,),
    ).fetchall()

    parts = conn.execute(
        "SELECT message_id, data FROM part WHERE session_id = ? "
        "ORDER BY time_created ASC, id ASC",
        (session_id,),
    ).fetchall()

    parts_by_msg: dict[str, list[dict]] = defaultdict(list)
    for pr in parts:
        pr = dict(pr)
        parts_by_msg[pr["message_id"]].append(json.loads(pr["data"]))
    if not parts_by_msg and (sess.get("agent") or ""):
        pass  # some schema versions store text in session_message; tolerated

    turns: list[TurnIR] = []
    for m in messages:
        m = dict(m)
        data = json.loads(m["data"])
        role = data.get("role") or "assistant"
        text, tools, kind = _flatten_opencode_parts(parts_by_msg.get(m["id"], []))

        md = data.get("model")
        model = md.get("modelID") or md.get("id") if isinstance(md, dict) else (md if isinstance(md, str) else None)

        parent = data.get("parentID")
        parent_turn = turn_id(OPENCODE, session_id, parent) if parent else None

        turns.append(
            TurnIR(
                turn_id=turn_id(OPENCODE, session_id, m["id"]),
                provider_msg_id=m["id"],
                parent_turn_id=parent_turn,
                role=role if role in ("user", "assistant") else "system",  # type: ignore[assignment]
                ts=_ms_to_iso(m["time_created"]),
                text=text,
                text_kind=kind,  # type: ignore[assignment]
                tools=tuple(tools),
                model=model,
            )
        )
    turns.sort(key=lambda t: (t.ts, t.provider_msg_id))

    todos = conn.execute(
        "SELECT status FROM todo WHERE session_id = ?", (session_id,)
    ).fetchall()
    todos_all_done = None
    if todos:
        todos_all_done = all(dict(r)["status"] == "completed" for r in todos)

    children = conn.execute(
        "SELECT id FROM session WHERE parent_id = ?", (session_id,)
    ).fetchall()
    links: list[ContinuationLink] = [
        ContinuationLink(
            kind="subagent", from_session=session_id, to_session=dict(r)["id"]
        )
        for r in children
    ]
    if sess.get("parent_id"):
        links.append(
            ContinuationLink(
                kind="parent_session",
                from_session=sess["parent_id"],
                to_session=session_id,
            )
        )

    return SessionIR(
        provider=OPENCODE,  # type: ignore[assignment]
        session_id=session_id,
        parent_session_id=sess.get("parent_id"),
        project_path=sess.get("directory") or "",
        title=sess.get("title"),
        started_at=_ms_to_iso(sess.get("time_created")) if sess.get("time_created") else "",
        ended_at=_ms_to_iso(sess.get("time_updated")) if sess.get("time_updated") else "",
        model_hints=_parse_session_model(sess.get("model")),
        agent_hints=tuple(filter(None, [sess.get("agent")])),
        links=tuple(links),
        turns=tuple(turns),
        n_user=sum(1 for t in turns if t.role == "user"),
        n_assistant=sum(1 for t in turns if t.role == "assistant"),
        n_tool_calls=sum(len(t.tools) for t in turns),
        n_tool_errors=sum(1 for t in turns for x in t.tools if x.ok is False),
        total_cost=sess.get("cost") if sess.get("cost") else None,
        tokens_in=sess.get("tokens_input") if sess.get("tokens_input") else None,
        tokens_out=sess.get("tokens_output") if sess.get("tokens_output") else None,
        work_landed=bool((sess.get("summary_additions") or 0) > 0),
        todos_all_done=todos_all_done,
        summary_additions=sess.get("summary_additions"),
        summary_deletions=sess.get("summary_deletions"),
    )


def _flatten_opencode_parts(parts: list[dict]) -> tuple[str, list[ToolCallIR], str]:
    texts: list[str] = []
    tools: list[ToolCallIR] = []
    saw_reasoning = False
    for d in parts:
        ptype = d.get("type")
        if ptype == "text":
            texts.append(str(d.get("text") or ""))
        elif ptype == "reasoning":
            saw_reasoning = True
        elif ptype == "tool":
            state = d.get("state") or {}
            status = state.get("status")
            ok = status == "completed" if status else None
            tools.append(
                ToolCallIR(name=str(d.get("tool") or "tool"), ok=ok, error=None if ok else status)
            )
    text = "\n".join(t for t in texts if t).strip()
    if tools and text:
        kind = "mixed"
    elif tools:
        kind = "tool"
    elif saw_reasoning:
        kind = "reasoning"
    else:
        kind = "human"
    return text, tools, kind


def _ms_to_iso(ms: object) -> str:
    from datetime import datetime

    try:
        if ms is None:
            return ""
        return datetime.fromtimestamp(int(str(ms)) / 1000, tz=UTC).isoformat()
    except (TypeError, ValueError):
        return ""


def _parse_session_model(raw: object) -> tuple[str, ...]:
    if not raw:
        return ()
    if isinstance(raw, dict):
        parts = (raw.get("modelID") or raw.get("id") or raw.get("providerID") or "")
        return tuple(filter(None, [str(parts)]))
    if isinstance(raw, str):
        try:
            d = json.loads(raw)
            return _parse_session_model(d)
        except json.JSONDecodeError:
            return (raw,)
    return ()


def default_providers() -> dict[str, object]:
    return {
        ClaudeJSONLProvider.name: ClaudeJSONLProvider(),
        OpenCodeSQLiteProvider.name: OpenCodeSQLiteProvider(),
    }