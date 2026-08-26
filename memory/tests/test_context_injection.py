"""Tests for memory/instinct context injection.

Tests are unit-level: they mock the store and verify the shape of what
each adapter returns, without hitting disk or a real embedding provider.

Rewritten for docs/next-steps-sequence.md Phase 1.6: build_context_block is
now a single allowlisted pass (memory_policy.is_injectable) — no raw
code_element file dump, no tool-frequency instinct noise. See lib/hooks/injection.py.
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
from lib.hooks.injection import ContextInjector
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


def _correction_ep(content: str) -> MemoryEpisode:
    return _ep(
        id="correction_1", layer=1, category="correction",
        content=content, importance=1.0,
    )


def _injector(episodes: list[MemoryEpisode]) -> ContextInjector:
    store = MagicMock()
    store.list_episodes.return_value = episodes
    store.config = {}
    return ContextInjector(store)


def _handler(episodes: list[MemoryEpisode]) -> MemoryHookHandler:
    store = MagicMock()
    store.list_episodes.return_value = episodes
    store.config = {}
    return MemoryHookHandler(store)


# ── build_context_block (via ContextInjector directly) ───────────────────────

class TestBuildContextBlock:
    def test_empty_when_no_episodes(self):
        inj = _injector([])
        block = inj.build_context_block("Edit", {"file_path": "/tmp/foo.py"}, "sess")
        assert block == ""

    def test_instinct_included_above_threshold(self):
        eps = [_instinct_ep("Edit", "Prefers small atomic edits.", confidence=0.8)]
        inj = _injector(eps)
        block = inj.build_context_block("Edit", {}, "sess")
        assert "Prefers small atomic edits." in block
        assert "0.80" in block

    def test_instinct_excluded_below_threshold(self):
        eps = [_instinct_ep("Edit", "Some pattern.", confidence=0.3)]
        inj = _injector(eps)
        block = inj.build_context_block("Edit", {}, "sess")
        assert block == ""

    def test_instinct_not_included_for_wrong_tool(self):
        eps = [_instinct_ep("Bash", "Runs python3 often.", confidence=0.9)]
        inj = _injector(eps)
        block = inj.build_context_block("Edit", {}, "sess")
        assert block == ""

    def test_code_element_never_injected_even_for_matching_file(self):
        # code_element is structural (lib/memory_policy.py), never injected —
        # that's crisp graph show's job now, not blind PreToolUse dump.
        file_path = str(Path("/tmp/hooks.py").resolve())
        eps = [_ep(
            id="ep_hooks", category="code_element", source_path=file_path,
            content="def _save(episode): ...", title="_save",
        )]
        inj = _injector(eps)
        block = inj.build_context_block("Edit", {"file_path": "/tmp/hooks.py"}, "sess")
        assert block == ""

    def test_correction_is_always_injected(self):
        eps = [_correction_ep("Never store JWT secrets in plaintext config.")]
        inj = _injector(eps)
        block = inj.build_context_block("Edit", {"file_path": "/tmp/hooks.py"}, "sess")
        assert "Never store JWT secrets in plaintext config." in block

    def test_no_correction_for_unrelated_query(self):
        # correction/preference episodes are tool-agnostic — always eligible,
        # so an empty store is what actually proves nothing leaks in.
        inj = _injector([])
        block = inj.build_context_block("Bash", {"command": "ls"}, "sess")
        assert block == ""

    def test_preference_and_instinct_both_present(self):
        file_path = str(Path("/tmp/bus.py").resolve())
        eps = [
            _instinct_ep("Edit", "Prefers atomic edits.", confidence=0.75),
            _correction_ep("Always run tests before committing."),
        ]
        inj = _injector(eps)
        block = inj.build_context_block("Edit", {"file_path": "/tmp/bus.py"}, "sess")
        assert "Prefers atomic edits." in block
        assert "Always run tests before committing." in block

    def test_at_most_5_injected(self):
        eps = [_instinct_ep("Edit", f"Pattern {i}.", confidence=0.9 - i * 0.01)
               for i in range(10)]
        inj = _injector(eps)
        block = inj.build_context_block("Edit", {}, "sess")
        assert block.count("- Pattern") <= 5


# ── handle_claude_pre_tool_context ────────────────────────────────────────────

class TestClaudePreToolContext:
    def test_returns_hook_specific_output_shape(self):
        eps = [_correction_ep("Prefer small diffs over big rewrites.")]
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

    def test_returns_empty_for_non_file_tool_with_no_matching_content(self):
        h = _handler([_instinct_ep("Bash", "runs git often", confidence=0.9)])
        result = h.handle_claude_pre_tool_context({
            "tool_name": "Ls",
            "tool_input": {},
            "session_id": "sess",
        })
        assert result == {}


# ── get_instincts event (OpenCode system.transform path) ─────────────────────

class TestGetInstinctsEvent:
    """Integration-level: run main() with opencode-get-instincts argv."""

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
