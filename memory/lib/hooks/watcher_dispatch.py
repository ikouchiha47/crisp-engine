"""Dispatches PostToolUse payloads through the watcher registry
(lib/watchers/) — file-change diffs, git commits, bash failures."""
from typing import Any, Dict

from lib.store import MemoryStore

from .episode_writer import EpisodeWriter


class WatcherDispatch:
    """Thin wrapper around lib.watchers.WatcherRegistry, lazily constructed."""

    def __init__(self, store: MemoryStore, writer: EpisodeWriter):
        self.store = store
        self.writer = writer
        self._registry = None

    def get_registry(self):
        if self._registry is None:
            from lib.watchers import WatcherRegistry
            self._registry = WatcherRegistry()
        return self._registry

    def run(self, tool: str, tool_input: Dict[str, Any], tool_output: Dict[str, Any],
            session_id: str, project_root: str) -> Dict[str, Any]:
        registry = self.get_registry()
        return registry.run(
            tool_name=tool,
            inp=tool_input,
            out=tool_output,
            session_id=session_id,
            project_root=project_root,
            save_fn=self.writer.save,
        )
