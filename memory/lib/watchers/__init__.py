"""WatcherRegistry — discovers and runs ToolWatcher instances."""

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Callable

from lib.bus import emit as _bus_emit
from lib.log import get_logger
from lib.store import MemoryEpisode, MemoryStore
from lib.watchers.base import ToolWatcher

_log = get_logger("watchers")

_USER_WATCHER_DIR = Path.home() / ".config" / "crisp" / "watchers"


def _find_watcher_class(module) -> type | None:
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue
        if isinstance(obj(), ToolWatcher):
            return obj
    return None


class WatcherRegistry:
    """Loads built-in and user watchers; dispatches PostToolUse events."""

    def __init__(self):
        self._watchers: list[ToolWatcher] = []
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        self._load_builtin()
        self._load_user()

    def _load_builtin(self):
        from lib.watchers.file_change_watcher import FileChangeWatcher
        from lib.watchers.git_watcher import GitWatcher
        from lib.watchers.bash_watcher import BashWatcher
        for cls in (FileChangeWatcher, GitWatcher, BashWatcher):
            try:
                self._watchers.append(cls())
                _log.debug("loaded builtin watcher: %s", cls.__name__, extra={})
            except Exception as e:
                _log.warning("failed to load builtin watcher %s: %s", cls.__name__, e, extra={})

    def _load_user(self):
        if not _USER_WATCHER_DIR.exists():
            return
        for py_file in sorted(_USER_WATCHER_DIR.glob("*.py")):
            try:
                spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                cls = _find_watcher_class(mod)
                if cls is None:
                    _log.warning("no ToolWatcher class found in %s", py_file, extra={})
                    continue
                self._watchers.append(cls())
                _log.info("loaded user watcher: %s from %s", cls.__name__, py_file, extra={})
            except Exception as e:
                _log.warning("failed to load user watcher %s: %s", py_file.name, e, extra={})

    def run(
        self,
        tool_name: str,
        inp: dict,
        out: dict,
        session_id: str,
        project_root: str,
        save_fn: Callable[[MemoryEpisode], bool],
    ) -> dict:
        """Run all matching watchers, save their episodes. Returns summary dict."""
        self._ensure_loaded()
        total_eps = 0
        matched = []

        for watcher in self._watchers:
            if tool_name not in watcher.tool_names:
                continue
            try:
                if not watcher.matches(tool_name, inp, out):
                    _bus_emit("watcher_skipped", {"watcher_name": watcher.name, "tool_name": tool_name,
                                              "session_id": session_id, "project": project_root})
                    continue
            except Exception as e:
                _log.warning("watcher %s.matches() failed: %s", watcher.name, e, extra={})
                continue

            try:
                eps = watcher.episodes(tool_name, inp, out, session_id, project_root)
            except Exception as e:
                _log.warning("watcher %s.episodes() failed: %s", watcher.name, e, extra={})
                eps = []

            saved = 0
            for ep in eps:
                try:
                    if save_fn(ep):
                        saved += 1
                except Exception as e:
                    _log.warning("watcher %s save failed: %s", watcher.name, e, extra={})

            if eps:
                matched.append(watcher.name)
                total_eps += saved
                _log.debug(
                    "watcher %s: tool=%s episodes=%d saved=%d",
                    watcher.name, tool_name, len(eps), saved,
                    extra={"session_id": session_id, "project": project_root},
                )
                _bus_emit("watcher_matched", {
                    "watcher_name": watcher.name, "tool_name": tool_name,
                    "episode_count": len(eps), "saved": saved,
                    "session_id": session_id, "project": project_root,
                })

        return {"watchers_matched": matched, "episodes_saved": total_eps}
