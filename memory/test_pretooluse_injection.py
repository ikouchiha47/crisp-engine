"""Real assertions against MemoryHookHandler.handle_claude_pre_tool_context —
the PreToolUse context-injection hook, checked against Claude Code's actual
verified hookSpecificOutput.additionalContext schema, not guessed.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from lib.hooks import MemoryHookHandler
from lib.store import MemoryEpisode, MemoryStore


def _handler():
    return MemoryHookHandler(MemoryStore(tempfile.mkdtemp()))


def test_no_episodes_for_file_returns_empty_dict():
    handler = _handler()
    result = handler.handle_claude_pre_tool_context({
        "tool_name": "Read",
        "tool_input": {"file_path": "/nonexistent/file.py"},
    })
    assert result == {}


def test_non_file_tool_returns_empty_dict():
    handler = _handler()
    result = handler.handle_claude_pre_tool_context({
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    })
    assert result == {}


def test_matching_code_element_episode_is_injected_in_real_schema_shape():
    handler = _handler()
    file_path = str(Path(tempfile.mkdtemp()) / "auth.py")

    ep = MemoryEpisode(
        id="code_abc123_verify_token",
        session_id="sess_test",
        timestamp="2026-01-01T00:00:00Z",
        title="Function: verify_token",
        content="```python\ndef verify_token(token: str) -> bool:\n```\nChecks JWT validity.",
        category="code_element",
        source_path=str(Path(file_path).resolve()),
        tags=["function", "python"],
    )
    handler.store.save_episode(ep)

    result = handler.handle_claude_pre_tool_context({
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
    })

    assert "hookSpecificOutput" in result
    assert result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    context = result["hookSpecificOutput"]["additionalContext"]
    assert "verify_token" in context
    assert "auth.py" in context


def test_stale_episode_is_not_injected():
    handler = _handler()
    file_path = str(Path(tempfile.mkdtemp()) / "auth.py")

    ep = MemoryEpisode(
        id="code_abc123_verify_token",
        session_id="sess_test",
        timestamp="2026-01-01T00:00:00Z",
        title="Function: verify_token",
        content="stale content",
        category="code_element",
        source_path=str(Path(file_path).resolve()),
        tags=["function", "python", "stale"],
    )
    handler.store.save_episode(ep)

    result = handler.handle_claude_pre_tool_context({
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
    })
    assert result == {}
