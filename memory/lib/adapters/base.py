"""Canonical event schema and AgentAdapter protocol."""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class NormalizedEvent:
    """Agent-agnostic representation of a hook event.

    All handlers in hooks.py receive this type. No handler should import
    or reference agent-specific field names.
    """

    # Who fired this and what happened
    agent: str        # "claude_code" | "opencode" | "pi" | "internal"
    event_type: str   # "session_start" | "post_tool" | "pre_tool" | "stop"
                      # | "session_end" | "pre_compact"
    session_id: str
    project_root: str  # absolute path to cwd / project root

    # Tool call fields (populated on post_tool / pre_tool only)
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    tool_output: dict = field(default_factory=dict)
    # tool_output canonical keys:
    #   stdout: str, stderr: str, exit_code: int

    # Stop / session-end fields
    last_message: str = ""         # last user message before stop
    tool_outputs: list = field(default_factory=list)

    # Transcript
    transcript_path: str = ""      # path to JSONL conversation transcript

    # Internal Crisp Engine event name (legacy path only)
    internal_event: str = ""

    # Original payload for debugging / passthrough
    raw: dict = field(default_factory=dict)


@runtime_checkable
class AgentAdapter(Protocol):
    """Protocol every adapter must satisfy."""

    agent_name: str  # identifier stored on NormalizedEvent.agent

    def can_handle(self, argv: list[str], payload: dict) -> bool:
        """Return True if this adapter owns this invocation."""
        ...

    def normalize(self, argv: list[str], payload: dict) -> NormalizedEvent:
        """Map raw argv + payload to NormalizedEvent. Must not raise."""
        ...
