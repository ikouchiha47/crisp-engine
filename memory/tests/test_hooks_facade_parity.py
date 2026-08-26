"""Proves the lib/hooks.py -> lib/hooks/ package split didn't silently
change MemoryHookHandler's external contract — the only thing anything
outside this package is allowed to depend on.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.hooks import MemoryHookHandler

PRE_REFACTOR_PUBLIC_METHODS = {
    "build_context_block": ["tool", "tool_input", "session_id"],
    "handle_claude_pre_tool_context": ["data"],
    "handle_claude_post_read": ["data"],
    "handle_claude_post_tool": ["data"],
    "handle_claude_session_start": ["data", "max_files"],
    "handle_claude_stop": ["data"],
    "handle_claude_transcript": ["data", "event"],
    "handle_session_end": ["event_data"],
    "handle_stop": ["event_data"],
    "handle_file_change": ["event_data"],
    "handle_tool_failure": ["event_data"],
}


def test_all_pre_refactor_public_methods_still_exist():
    for name in PRE_REFACTOR_PUBLIC_METHODS:
        assert hasattr(MemoryHookHandler, name), f"missing method: {name}"


def test_method_signatures_unchanged():
    for name, expected_params in PRE_REFACTOR_PUBLIC_METHODS.items():
        method = getattr(MemoryHookHandler, name)
        params = list(inspect.signature(method).parameters)
        params.remove("self")
        assert params == expected_params, f"{name} signature changed: {params} != {expected_params}"


def test_constructor_takes_only_store():
    params = list(inspect.signature(MemoryHookHandler.__init__).parameters)
    assert params == ["self", "store"]
