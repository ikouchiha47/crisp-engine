"""Internal Crisp Engine adapter.

Handles the legacy internal format where events are sent directly via
hook_event_name field (no argv prefix). Used by the internal Crisp Engine
event bus and tests.

Internal event names: SessionEnd, Stop, FileChange, ToolFailure
"""

from lib.adapters.base import NormalizedEvent

_EVENT_MAP = {
    "SessionEnd":   "session_end",
    "Stop":         "stop",
    "FileChange":   "file_change",   # internal-only, not a standard hook event
    "ToolFailure":  "tool_failure",  # internal-only
}


class InternalAdapter:
    agent_name = "internal"

    def can_handle(self, argv: list[str], payload: dict) -> bool:
        # Matches when no known agent prefix in argv, but hook_event_name is set
        event = payload.get("hook_event_name", "")
        return event in _EVENT_MAP

    def normalize(self, argv: list[str], payload: dict) -> NormalizedEvent:
        internal_event = payload.get("hook_event_name", "")
        return NormalizedEvent(
            agent=self.agent_name,
            event_type=_EVENT_MAP.get(internal_event, "unknown"),
            session_id=payload.get("session_id", "unknown"),
            project_root=payload.get("cwd", payload.get("project_root", "")),
            tool_name=payload.get("tool_name", ""),
            tool_input=payload.get("tool_input", {}),
            tool_output={},
            last_message=payload.get("message", ""),
            tool_outputs=payload.get("tool_outputs", []),
            transcript_path=payload.get("transcript_path", ""),
            internal_event=internal_event,
            raw=payload,
        )
