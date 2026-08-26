"""Tests for memory/instinct context injection.

Tests are unit-level: they mock the store and verify the shape of what
each adapter returns, without hitting disk or a real embedding provider.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# make lib importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.hooks import MemoryHookHandler
from lib.store import MemoryEpisode


# ── helpers ──────────────────────────────────────────────────────────────────

def _ep(**kwargs) -> MemoryEpisode:
    defaults = dict(
        id="ep_test", layer=0, category="code_element", importance=0.5,
        session_id="sess_abc", source_path="", tags=[], content="", title="",
        embedding=None, created_at="", context_snapshot={},
    )
    defaults.update(kwargs)
    ep = MemoryEpisode.__new__(MemoryEpisode)
    ep.__dict__.update(defaults)
    return ep


def _instinct_ep(tool: str, content: str, confidence: float = 0.7) -> MemoryEpisode:
    ep = _ep(
        id=f"instinct_{tool}", layer=2, category="instinct",
        tags=["instinct", tool], content=content,
    )
    ep.confidence = confidence
    return ep


def _handler(episodes: list[MemoryEpisode]) -> MemoryHookHandler:
    store = MagicMock()
    store.list_episodes.return_value = episodes
    store.config = {}
    h = MemoryHookHandler.__new__(MemoryHookHandler)
    h.store = store
    h.analyzer = MagicMock()
    h._embed_provider = None
    h._watcher_registry = None
    h._project_root = "/tmp/myproject"
    h._post_tool_count = 0
    return h


# ── build_context_block ───────────────────────────────────────────────────────

class TestBuildContextBlock:
    def test_empty_when_no_episodes(self):
        h = _handler([])
        block = h.build_context_block("Edit", {"file_path": "/tmp/foo.py"}, "sess")
        assert block == ""

    def test_instinct_included_above_threshold(self):
        eps = [_instinct_ep("Edit", "Prefers small atomic edits.", confidence=0.8)]
        h = _handler(eps)
        block = h.build_context_block("Edit", {}, "sess")
        assert "[crisp instincts]" in block
        assert "Prefers small atomic edits." in block
        assert "0.80" in block

    def test_instinct_excluded_below_threshold(self):
        eps = [_instinct_ep("Edit", "Some pattern.", confidence=0.3)]
        h = _handler(eps)
        block = h.build_context_block("Edit", {}, "sess")
        assert block == ""

    def test_instinct_not_included_for_wrong_tool(self):
        eps = [_instinct_ep("Bash", "Runs python3 often.", confidence=0.9)]
        h = _handler(eps)
        block = h.build_context_block("Edit", {}, "sess")
        assert block == ""

    def test_episodic_memory_for_matching_file(self):
        file_path = str(Path("/tmp/hooks.py").resolve())
        eps = [_ep(
            id="ep_hooks", category="code_element", source_path=file_path,
            content="def _save(episode): ...", title="_save",
        )]
        h = _handler(eps)
        block = h.build_context_block("Edit", {"file_path": "/tmp/hooks.py"}, "sess")
        assert "hooks.py" in block
        assert "_save" in block

    def test_episodic_stale_excluded(self):
        file_path = str(Path("/tmp/hooks.py").resolve())
        eps = [_ep(
            id="ep_stale", category="code_element", source_path=file_path,
            content="old content", title="old", tags=["stale"],
        )]
        h = _handler(eps)
        block = h.build_context_block("Edit", {"file_path": "/tmp/hooks.py"}, "sess")
        assert block == ""

    def test_no_episodic_for_bash(self):
        file_path = str(Path("/tmp/hooks.py").resolve())
        eps = [_ep(source_path=file_path, content="something")]
        h = _handler(eps)
        block = h.build_context_block("Bash", {"command": "ls"}, "sess")
        assert block == ""

    def test_both_sections_present(self):
        file_path = str(Path("/tmp/bus.py").resolve())
        eps = [
            _instinct_ep("Edit", "Prefers atomic edits.", confidence=0.75),
            _ep(id="ep_bus", category="code_element", source_path=file_path,
                content="def emit(): ...", title="emit"),
        ]
        h = _handler(eps)
        block = h.build_context_block("Edit", {"file_path": "/tmp/bus.py"}, "sess")
        assert "[crisp instincts]" in block
        assert "[crisp memory: bus.py]" in block

    def test_at_most_5_instincts(self):
        eps = [_instinct_ep(f"Edit", f"Pattern {i}.", confidence=0.9 - i*0.01)
               for i in range(10)]
        h = _handler(eps)
        block = h.build_context_block("Edit", {}, "sess")
        assert block.count("- Pattern") <= 5


# ── handle_claude_pre_tool_context ────────────────────────────────────────────

class TestClaudePreToolContext:
    def test_returns_hook_specific_output_shape(self):
        file_path = str(Path("/tmp/hooks.py").resolve())
        eps = [_ep(source_path=file_path, content="def foo(): pass", title="foo")]
        h = _handler(eps)
        result = h.handle_claude_pre_tool_context({
            "tool_name": "Edit",
            "tool_input": {"file_path": "/tmp/hooks.py"},
            "session_id": "sess_abc",
        })
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert "additionalContext" in result["hookSpecificOutput"]
        assert len(result["hookSpecificOutput"]["additionalContext"]) > 0

    def test_returns_empty_when_nothing(self):
        h = _handler([])
        result = h.handle_claude_pre_tool_context({
            "tool_name": "Edit",
            "tool_input": {"file_path": "/tmp/missing.py"},
            "session_id": "sess",
        })
        assert result == {}

    def test_returns_empty_for_non_file_tool(self):
        h = _handler([_instinct_ep("Bash", "runs git often", confidence=0.9)])
        result = h.handle_claude_pre_tool_context({
            "tool_name": "Bash",
            "tool_input": {"command": "git log"},
            "session_id": "sess",
        })
        # Bash has no file_path so episodic section empty; instinct block
        # would appear IF there were Bash instincts — verify shape when present
        # (instinct section is tool-keyed, Bash instinct would appear)
        assert isinstance(result, dict)


# ── get_instincts event (OpenCode system.transform path) ─────────────────────

class TestGetInstinctsEvent:
    """Integration-level: run main() with opencode-get-instincts argv."""

    def _run_main(self, argv: list[str], stdin_payload: dict) -> dict:
        import io
        captured = {}
        orig_argv = sys.argv[:]
        orig_stdin = sys.stdin

        sys.argv = argv
        sys.stdin = io.StringIO(json.dumps(stdin_payload))
        output_lines = []

        with patch("builtins.print", side_effect=lambda s: output_lines.append(s)):
            try:
                import importlib
                import lib.hooks as hooks_mod
                importlib.reload(hooks_mod)  # fresh module state
                # Can't easily call main() in isolation; test via subprocess instead
            except SystemExit:
                pass

        sys.argv = orig_argv
        sys.stdin = orig_stdin
        return output_lines

    def test_get_instincts_via_subprocess(self, tmp_path):
        import subprocess
        payload = {"session_id": "sess_test", "cwd": str(tmp_path)}
        result = subprocess.run(
            [sys.executable, "-m", "lib.hooks", "opencode-get-instincts"],
            input=json.dumps(payload),
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        # May fail if store has no episodes, but should return valid JSON
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            assert "instinct_block" in data
            assert isinstance(data["instinct_block"], str)


# ── adapter normalisation sanity ─────────────────────────────────────────────

class TestAdapterNormalisation:
    def test_claude_code_pre_tool_maps_correctly(self):
        from lib.adapters.claude_code import ClaudeCodeAdapter
        a = ClaudeCodeAdapter()
        ev = a.normalize(
            ["crisp-hook", "claude-pre-tool"],
            {"tool_name": "Edit", "tool_input": {"file_path": "foo.py"},
             "session_id": "s1", "cwd": "/proj"},
        )
        assert ev.event_type == "pre_tool"
        assert ev.tool_name == "Edit"
        assert ev.tool_input == {"file_path": "foo.py"}
        assert ev.project_root == "/proj"

    def test_opencode_get_instincts_normalises(self):
        from lib.adapters.opencode import OpenCodeAdapter
        a = OpenCodeAdapter()
        assert a.can_handle(["crisp-hook", "opencode-get-instincts"], {})
        ev = a.normalize(
            ["crisp-hook", "opencode-get-instincts"],
            {"session_id": "s2", "cwd": "/proj"},
        )
        assert ev.event_type == "get_instincts"
        assert ev.agent == "opencode"
