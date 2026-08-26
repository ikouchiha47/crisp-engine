"""OpenCode adapter.

OpenCode plugins are TypeScript. A thin shim (docs/agent-shims/opencode.ts)
calls crisp-hook with an "opencode-" prefix and JSON on stdin.

OpenCode event → our event_type:
  opencode-session-start  ← session.created
  opencode-post-tool      ← tool.execute.after
  opencode-stop           ← session.idle  (agent turn complete)
  opencode-session-end    ← session.deleted / session.compacted
  opencode-pre-compact    ← (session.compacted used as pre-compact signal)

OpenCode tool names use their own casing; we normalise to Claude Code names
since the watcher layer matches on "Bash", "Write", etc.
"""

from lib.adapters.base import NormalizedEvent

_EVENT_MAP = {
    "opencode-session-start":  "session_start",
    "opencode-pre-tool":       "pre_tool",
    "opencode-post-tool":      "post_tool",
    "opencode-stop":           "stop",
    "opencode-session-end":    "session_end",
    "opencode-pre-compact":    "pre_compact",
    "opencode-get-instincts":  "get_instincts",  # OpenCode system.transform query
}

_TOOL_NAME_MAP = {
    "bash":      "Bash",
    "write":     "Write",
    "edit":      "Edit",
    "multiedit": "MultiEdit",
    "read":      "Read",
    "patch":     "Edit",   # opencode alias
}


def _normalise_tool_name(raw: str) -> str:
    return _TOOL_NAME_MAP.get(raw.lower(), raw)


def _normalise_tool_output(payload: dict) -> dict:
    # opencode wraps output as { output: { stdout, stderr, exitCode } }
    out = payload.get("output", {}) or {}
    if isinstance(out, str):
        return {"stdout": out}
    return {
        "stdout": out.get("stdout", ""),
        "stderr": out.get("stderr", ""),
        "exit_code": out.get("exitCode", out.get("exit_code", 0)),
    }


class OpenCodeAdapter:
    agent_name = "opencode"

    def can_handle(self, argv: list[str], payload: dict) -> bool:
        return len(argv) > 1 and argv[1] in _EVENT_MAP

    def normalize(self, argv: list[str], payload: dict) -> NormalizedEvent:
        event_type = _EVENT_MAP[argv[1]]
        return NormalizedEvent(
            agent=self.agent_name,
            event_type=event_type,
            session_id=payload.get("session_id", payload.get("sessionId", "unknown")),
            project_root=payload.get("cwd", ""),
            tool_name=_normalise_tool_name(payload.get("tool_name", payload.get("toolName", ""))),
            tool_input=payload.get("tool_input", payload.get("input", {})) or {},
            tool_output=_normalise_tool_output(payload),
            last_message=payload.get("message", ""),
            tool_outputs=payload.get("tool_outputs", []),
            transcript_path=payload.get("transcript_path", ""),
            raw=payload,
        )
