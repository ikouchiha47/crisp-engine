"""Pi (pi.dev) adapter.

Pi plugins are TypeScript extensions. A thin shim (docs/agent-shims/pi.ts)
calls crisp-hook with a "pi-" prefix and JSON on stdin.

Pi event → our event_type:
  pi-session-start   ← session_start
  pi-post-tool       ← tool_result  (fires after tool execution, has input+output)
  pi-stop            ← agent_end    (agent turn complete)
  pi-session-end     ← session_shutdown
  pi-pre-compact     ← session_before_compact

Pi tool_result payload:
  toolName: str
  toolCallId: str
  input: dict          -- tool arguments (maps to tool_input)
  content: ContentBlock[]  -- output as typed blocks (we extract text)
  isError: bool
  details: dict

ContentBlock: { type: "text" | "image" | ..., text?: str }
"""

from lib.adapters.base import NormalizedEvent

_EVENT_MAP = {
    "pi-session-start": "session_start",
    "pi-post-tool":     "post_tool",
    "pi-stop":          "stop",
    "pi-session-end":   "session_end",
    "pi-pre-compact":   "pre_compact",
}

_TOOL_NAME_MAP = {
    "bash":      "Bash",
    "write":     "Write",
    "edit":      "Edit",
    "multiedit": "MultiEdit",
    "read":      "Read",
}


def _normalise_tool_name(raw: str) -> str:
    return _TOOL_NAME_MAP.get(raw.lower(), raw)


def _extract_content(blocks: list) -> str:
    """Flatten Pi ContentBlock[] into a plain string."""
    if not blocks:
        return ""
    if isinstance(blocks, str):
        return blocks
    parts = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
    return "\n".join(parts)


def _normalise_tool_output(payload: dict) -> dict:
    content = _extract_content(payload.get("content", []))
    is_error = payload.get("isError", False)
    # Pi doesn't expose exit_code directly; infer from isError
    return {
        "stdout": content if not is_error else "",
        "stderr": content if is_error else "",
        "exit_code": 1 if is_error else 0,
    }


class PiAdapter:
    agent_name = "pi"

    def can_handle(self, argv: list[str], payload: dict) -> bool:
        return len(argv) > 1 and argv[1] in _EVENT_MAP

    def normalize(self, argv: list[str], payload: dict) -> NormalizedEvent:
        event_type = _EVENT_MAP[argv[1]]
        return NormalizedEvent(
            agent=self.agent_name,
            event_type=event_type,
            session_id=payload.get("session_id", "unknown"),
            project_root=payload.get("cwd", ""),
            tool_name=_normalise_tool_name(payload.get("toolName", payload.get("tool_name", ""))),
            tool_input=payload.get("input", payload.get("tool_input", {})) or {},
            tool_output=_normalise_tool_output(payload),
            last_message=payload.get("prompt", payload.get("message", "")),
            tool_outputs=payload.get("tool_outputs", []),
            transcript_path=payload.get("transcript_path", ""),
            raw=payload,
        )
