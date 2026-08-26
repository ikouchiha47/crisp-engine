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


def test_code_element_episode_is_never_injected():
    # Phase 1.6 (docs/next-steps-sequence.md): code_element is a structural/
    # searchable category (see lib/memory_policy.py), never injected as raw
    # context — that's crisp graph show's job now, not blind PreToolUse dump.
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
    assert result == {}


def test_correction_episode_is_injected_in_real_schema_shape():
    handler = _handler()
    file_path = str(Path(tempfile.mkdtemp()) / "auth.py")

    ep = MemoryEpisode(
        id="correction_001",
        session_id="sess_test",
        timestamp="2026-01-01T00:00:00Z",
        layer=1,
        title="Correction applied",
        content="User correction: never store JWT secrets in plaintext config.",
        category="correction",
        correction_applied=True,
    )
    handler.store.save_episode(ep)

    result = handler.handle_claude_pre_tool_context({
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
    })

    assert "hookSpecificOutput" in result
    assert result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    context = result["hookSpecificOutput"]["additionalContext"]
    assert "never store JWT secrets" in context
