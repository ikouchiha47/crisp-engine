"""Smoke test for lib.hooks.main() itself — the real crisp-hook entry point
wired into .claude/settings.json. Every other hooks test exercises
MemoryHookHandler's methods directly, which is why a stale import inside
main()'s function body (lib.project_memory, moved during the code_index/
store/consolidate restructuring) went undetected until this was added.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def _run_hook(cmd: str, payload: dict, cwd: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "lib.hooks", cmd],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent),
        env={"PYTHONPATH": str(Path(__file__).parent)},
    )
    assert proc.returncode == 0, f"main() exited non-zero: stderr={proc.stderr!r}"
    return json.loads(proc.stdout)


def test_claude_session_start_runs_without_crashing():
    project = tempfile.mkdtemp()
    result = _run_hook("claude-session-start", {"cwd": project, "session_id": "smoke_test"}, project)
    assert result.get("status") == "ok", f"unexpected result: {result}"


def test_claude_pre_tool_runs_without_crashing():
    project = tempfile.mkdtemp()
    result = _run_hook(
        "claude-pre-tool",
        {"cwd": project, "session_id": "smoke_test", "tool_name": "Read", "tool_input": {}},
        project,
    )
    assert "error" not in result or result.get("status") != "error", f"unexpected result: {result}"
