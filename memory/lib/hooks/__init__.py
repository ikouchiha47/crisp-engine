"""Claude Code hook handlers for automatic memory capture.

Handles two call styles:

  Internal (Crisp Engine format) — hook_event_name field in JSON:
    FileChange, Stop, SessionEnd, ToolFailure

  Claude Code native — routed via sys.argv[1]:
    claude-session-start ← SessionStart (eager whole-repo code index)
    claude-post-tool   ← PostToolUse (Write/Edit/MultiEdit)
    claude-stop        ← Stop
    claude-session-end ← SessionEnd
    claude-pre-compact ← PreCompact

Install as `crisp-hook` via pyproject.toml entry point, then wire in
.claude/settings.json using "command": "crisp-hook".

`MemoryHookHandler` is a thin composition root: it builds one shared
`EpisodeWriter` plus five single-responsibility collaborators and delegates
every `handle_claude_*`/`build_context_block` call to the matching one.
Each collaborator is independently constructible and testable — see
lib/hooks/{structural_index,injection,watcher_dispatch,transcript,signals}.py.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.bus import emit as _bus_emit, HookFired
from lib.log import bind as _log_bind, get_logger as _get_logger
from lib.session import upsert as _session_upsert
from lib.store import MemoryStore

from .episode_writer import EpisodeWriter
from .injection import ContextInjector
from .signals import SignalDetector
from .structural_index import StructuralIndexer
from .transcript import TranscriptService
from .watcher_dispatch import WatcherDispatch

_log = _get_logger("hooks")


class MemoryHookHandler:
    """Handles Claude Code hook events for automatic memory capture."""

    _CONV_INTERVAL = 30  # save conversation episode every N post_tool calls

    def __init__(self, store: MemoryStore):
        self.store = store
        self._project_root = ""
        self._post_tool_count = 0

        self.writer = EpisodeWriter(store)
        self.structural_index = StructuralIndexer(store, self.writer)
        self.injector = ContextInjector(store)
        self.watcher_dispatch = WatcherDispatch(store, self.writer)
        self.transcript = TranscriptService(store, self.writer)
        self.signals = SignalDetector(store, self.writer)

    def _set_project_root(self, root: str) -> None:
        if not root:
            return
        self._project_root = root
        self.writer.set_project_root(root)
        self.injector.set_project_root(root)

    # ── Claude Code native translators ────────────────────────────────────

    def build_context_block(self, tool: str, tool_input: Dict[str, Any], session_id: str) -> str:
        return self.injector.build_context_block(tool, tool_input, session_id)

    def handle_claude_pre_tool_context(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self.injector.handle_claude_pre_tool_context(data)

    def handle_claude_post_read(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self.structural_index.handle_claude_post_read(data)

    def handle_claude_post_tool(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch PostToolUse: Read goes through the lazy indexer; every
        other tool marks the touched file's index stale, runs the watcher
        registry, and (every _CONV_INTERVAL calls) snapshots the transcript."""
        tool = data.get("tool_name", "")

        if tool == "Read":
            return self.handle_claude_post_read(data)

        tool_input = data.get("tool_input", {})
        tool_output = data.get("tool_response", data.get("tool_output", {})) or {}
        if isinstance(tool_output, str):
            tool_output = {"stdout": tool_output}

        session_id = data.get("session_id", "unknown")
        project_root = data.get("cwd", "") or data.get("project_root", "")
        self._set_project_root(project_root)

        file_path = tool_input.get("file_path", "")
        if tool in ("Write", "Edit", "MultiEdit") and file_path:
            self.structural_index.mark_index_stale(file_path)

        result = self.watcher_dispatch.run(
            tool=tool, tool_input=tool_input, tool_output=tool_output,
            session_id=session_id, project_root=project_root,
        )

        self._post_tool_count += 1
        if self._post_tool_count % self._CONV_INTERVAL == 0:
            transcript_path = data.get("transcript_path", "")
            if transcript_path:
                self.transcript.async_conv_snapshot(session_id, transcript_path, self._project_root)

        if not result["watchers_matched"]:
            return {"status": "ignored", "reason": f"no watcher matched tool={tool}"}

        result["status"] = "ok"
        result["tool"] = tool
        return result

    def handle_claude_session_start(self, data: Dict[str, Any], max_files: int = 500) -> Dict[str, Any]:
        """SessionStart — eager, whole-repo structural index + git ingest.

        Orchestrates two collaborators (structural_index for the file walk,
        transcript for git log ingestion) since this event spans both.
        """
        cwd = data.get("cwd") or data.get("project_dir")
        project_root = Path(cwd).resolve() if cwd else Path.cwd()
        self._set_project_root(str(project_root))
        session_id = data.get("session_id", "unknown")

        log = _log_bind(session_id=session_id, project=str(project_root), name="hooks")
        log.info("SessionStart project=%s", project_root)

        try:
            _session_upsert(session_id, project=str(project_root), agent="claude_code")
        except Exception:
            pass

        # Warm episode cache now (session-start has budget; pre-tool calls must be fast)
        self.injector.warm_cache()

        walk_result = self.structural_index.run_session_start_walk(project_root, session_id, max_files)
        git_result = self.transcript.ingest_git_log(project_root, session_id)

        log.info(
            "SessionStart done: indexed=%d skipped_fresh=%d errors=%d capped=%s git_commits=%d",
            walk_result["indexed"], walk_result["skipped_fresh"],
            walk_result["error_count"], walk_result["capped"],
            git_result.get("git_commits", 0),
        )

        return {
            "status": "ok",
            "indexed": walk_result["indexed"],
            "skipped_fresh": walk_result["skipped_fresh"],
            "errors": walk_result["errors"],
            "error_count": walk_result["error_count"],
            "capped": walk_result["capped"],
            "git_commits": git_result.get("git_commits", 0),
        }

    def handle_claude_stop(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self.signals.handle_claude_stop(data)

    def handle_claude_transcript(self, data: Dict[str, Any], event: str) -> Dict[str, Any]:
        return self.transcript.handle_claude_transcript(data, event)

    # ── Internal Crisp Engine handlers ────────────────────────────────────

    def handle_session_end(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        return self.transcript.handle_session_end(event_data)

    def handle_stop(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        return self.signals.handle_stop(event_data)

    def handle_file_change(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        return self.signals.handle_file_change(event_data)

    def handle_tool_failure(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        return self.signals.handle_tool_failure(event_data)


def main():
    """Entry point for crisp-hook <event-name>.

    Supported prefixes:
      claude-*     Claude Code (native shell hooks)
      opencode-*   OpenCode (via docs/agent-shims/opencode.ts)
      pi-*         Pi (via docs/agent-shims/pi.ts)
      (none)       Internal Crisp Engine format (hook_event_name field)

    All routing is handled by lib.adapters. Adding a new agent requires
    only a new adapter class -- nothing here changes.
    """
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, EOFError, ValueError):
        print(json.dumps({"error": "invalid stdin"}))
        return

    from lib.adapters import resolve
    from lib.store import get_memory_store

    try:
        event = resolve(sys.argv, data)
    except ValueError as e:
        print(json.dumps({"status": "ignored", "reason": str(e)}))
        return

    try:
        _bus_emit(HookFired(
            session_id=event.session_id,
            project=event.project_root,
            event_type=event.event_type,
            tool_name=event.tool_name or "",
            agent=event.agent,
        ))
    except Exception:
        pass

    try:
        store = get_memory_store(event.project_root) if event.project_root else get_memory_store()
    except Exception:
        store = MemoryStore(str(Path.home() / ".claude" / "memory"))

    handler = MemoryHookHandler(store)
    result: Dict[str, Any] = {"status": "ok", "agent": event.agent}

    def _instincts():
        from lib.instincts import InstinctEngine
        return InstinctEngine(store)

    def _observe(phase: str):
        try:
            _instincts().observe({**data, "phase": phase})
        except Exception:
            pass

    def _distill():
        try:
            return _instincts().analyze()
        except Exception:
            return None

    try:
        et = event.event_type

        if et == "session_start":
            result.update(handler.handle_claude_session_start(data))

        elif et == "pre_tool":
            _observe("pre")
            result["observed"] = "pre"
            result.update(handler.handle_claude_pre_tool_context(data))

        elif et == "post_tool":
            _observe("post")
            result.update(handler.handle_claude_post_tool(data))

        elif et == "stop":
            distilled = _distill()
            if distilled:
                result["instincts"] = distilled
            result.update(handler.handle_claude_stop(data))

        elif et == "session_end":
            distilled = _distill()
            if distilled:
                result["instincts"] = distilled
            result.update(handler.handle_claude_transcript(data, "SessionEnd"))

        elif et == "pre_compact":
            result.update(handler.handle_claude_transcript(data, "PreCompact"))

        # OpenCode system.transform query — return instinct block as JSON
        elif et == "get_instincts":
            block = handler.build_context_block(
                tool=data.get("tool_name", ""),
                tool_input=data.get("tool_input", {}),
                session_id=data.get("session_id", ""),
            )
            result["instinct_block"] = block
            result["status"] = "ok"

        # Internal-only event types
        elif et == "file_change":
            result.update(handler.handle_file_change(data))

        elif et == "tool_failure":
            result.update(handler.handle_tool_failure(data))

        else:
            result["status"] = "ignored"
            result["reason"] = f"unhandled event_type={et}"

    except Exception as e:
        import traceback
        result["status"] = "error"
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
