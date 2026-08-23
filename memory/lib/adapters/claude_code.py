"""Claude Code adapter.

Maps Claude Code argv + JSON payload to NormalizedEvent.
Claude Code passes the event name as sys.argv[1] with a "claude-" prefix
and writes the JSON payload to stdin.
"""

from lib.adapters.base import NormalizedEvent

_EVENT_MAP = {
    "claude-session-start": "session_start",
    "claude-pre-tool":      "pre_tool",
    "claude-post-tool":     "post_tool",
    "claude-stop":          "stop",
    "claude-session-end":   "session_end",
    "claude-pre-compact":   "pre_compact",
}


class ClaudeCodeAdapter:
    agent_name = "claude_code"

    def can_handle(self, argv: list[str], payload: dict) -> bool:
        return len(argv) > 1 and argv[1] in _EVENT_MAP

    def normalize(self, argv: list[str], payload: dict) -> NormalizedEvent:
        event_type = _EVENT_MAP[argv[1]]

        tool_input = payload.get("tool_input", {}) or {}
        tool_output = payload.get("tool_response", payload.get("tool_output", {})) or {}
        if isinstance(tool_output, str):
            tool_output = {"stdout": tool_output}

        return NormalizedEvent(
            agent=self.agent_name,
            event_type=event_type,
            session_id=payload.get("session_id", "unknown"),
            project_root=payload.get("cwd") or payload.get("project_dir") or "",
            tool_name=payload.get("tool_name", ""),
            tool_input=tool_input,
            tool_output=tool_output,
            last_message=payload.get("message", ""),
            tool_outputs=payload.get("tool_outputs", []),
            transcript_path=payload.get("transcript_path", ""),
            raw=payload,
        )
