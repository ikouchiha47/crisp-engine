"""Shared fixtures: anonymized Claude JSONL + OpenCode SQLite built at test time."""

from __future__ import annotations

import json
import sqlite3

import pytest

CLAUDE_SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def make_claude_jsonl(path):
    """Write an anonymized minimal claude session transcript."""
    events = [
        {"type": "mode", "mode": "normal", "sessionId": CLAUDE_SESSION_ID},
        {
            "type": "user",
            "uuid": "u100",
            "parentUuid": None,
            "sessionId": CLAUDE_SESSION_ID,
            "timestamp": "2026-07-12T12:21:30.000Z",
            "cwd": "/example/project",
            "isMeta": False,
            "isSidechain": False,
            "message": {"role": "user", "content": "check the code for logic errors"},
        },
        {
            "type": "assistant",
            "uuid": "a100",
            "parentUuid": "u100",
            "sessionId": CLAUDE_SESSION_ID,
            "timestamp": "2026-07-12T12:22:00.000Z",
            "isSidechain": False,
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "I found a logic error."}],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        },
        {
            "type": "user",
            "uuid": "u200",
            "parentUuid": "a100",
            "sessionId": CLAUDE_SESSION_ID,
            "timestamp": "2026-07-12T12:22:10.000Z",
            "cwd": "/example/project",
            "isMeta": False,
            "isSidechain": False,
            "message": {"role": "user", "content": "still broken, wrong file"},
        },
        {
            "type": "assistant",
            "uuid": "a200",
            "parentUuid": "u200",
            "sessionId": CLAUDE_SESSION_ID,
            "timestamp": "2026-07-12T12:22:40.000Z",
            "isSidechain": False,
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "Fixed the file under tests."}],
                "usage": {"input_tokens": 120, "output_tokens": 40},
            },
        },
        {
            "type": "user",
            "uuid": "u300",
            "parentUuid": "a200",
            "sessionId": CLAUDE_SESSION_ID,
            "timestamp": "2026-07-12T12:23:00.000Z",
            "cwd": "/example/project",
            "isMeta": False,
            "isSidechain": False,
            "message": {"role": "user", "content": "thanks, that fixed it"},
        },
        {
            "type": "file-history-delta",
            "messageId": "a200",
            "sessionId": CLAUDE_SESSION_ID,
            "delta": {"trackedFileBackups": {"src/foo.py": {}}},
        },
        {"type": "ai-title", "title": "fix logic error", "sessionId": CLAUDE_SESSION_ID},
    ]
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    return path


def make_opencode_db(path):
    """Build a minimal opencode.db schema + one session in memory/temp file."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE project (id TEXT PRIMARY KEY, worktree TEXT, name TEXT);
        CREATE TABLE session (
            id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, slug TEXT,
            directory TEXT, title TEXT, version TEXT, agent TEXT, model TEXT,
            cost REAL, tokens_input INTEGER, tokens_output INTEGER,
            summary_additions INTEGER, summary_deletions INTEGER, summary_files INTEGER,
            time_created INTEGER, time_updated INTEGER
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,
            time_updated INTEGER, data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
            time_created INTEGER, time_updated INTEGER, data TEXT
        );
        CREATE TABLE todo (
            session_id TEXT, content TEXT, status TEXT, priority TEXT,
            position INTEGER, time_created INTEGER, time_updated INTEGER
        );
        """
    )
    now = 1_752_000_000_000
    conn.execute(
        "INSERT INTO project (id, worktree, name) VALUES (?, ?, ?)",
        ("proj1", "/example/project", "example"),
    )
    conn.execute(
        """INSERT INTO session (id, project_id, parent_id, slug, directory, title,
           version, agent, model, cost, tokens_input, tokens_output,
           summary_additions, time_created, time_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "ses_abc", "proj1", None, "slug", "/example/project", "session title",
            "1.0", "build",
            json.dumps({"providerID": "xai", "modelID": "grok-4.5", "variant": "medium"}),
            0.5, 1000, 200, 10, now, now + 5000,
        ),
    )
    # user message + parts
    m1, m2 = "msg_user1", "msg_asst1"
    for mid, session, data, t in [
        (m1, "ses_abc", {"role": "user"}, now + 100),
        (m2, "ses_abc", {"role": "assistant", "model": {"modelID": "grok-4.5"}}, now + 200),
    ]:
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
            (mid, session, t, t, json.dumps(data)),
        )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
        ("p1", m1, "ses_abc", now + 100, now + 100, json.dumps({"type": "text", "text": "make it faster"})),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
        ("p2", m2, "ses_abc", now + 200, now + 200, json.dumps({"type": "text", "text": "Done, sped up 2x."})),
    )
    conn.execute(
        "INSERT INTO todo (session_id, content, status, position) VALUES (?,?,?,?)",
        ("ses_abc", "optimize", "completed", 0),
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def claude_fixture(tmp_path):
    return make_claude_jsonl(tmp_path / "tiny.jsonl")


@pytest.fixture
def opencode_fixture(tmp_path):
    return make_opencode_db(tmp_path / "opencode.db")