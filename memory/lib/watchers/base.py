"""ToolWatcher protocol and shared types."""

from typing import Protocol, runtime_checkable
from lib.store import MemoryEpisode


@runtime_checkable
class ToolWatcher(Protocol):
    name: str
    tool_names: list[str]

    def matches(self, tool_name: str, inp: dict, out: dict) -> bool: ...

    def episodes(
        self,
        tool_name: str,
        inp: dict,
        out: dict,
        session_id: str,
        project_root: str,
    ) -> list[MemoryEpisode]: ...
