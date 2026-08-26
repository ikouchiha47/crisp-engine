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
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.bus import emit as _bus_emit, EpisodeSaved, EmbedResult, HookFired
from lib.code_index import CodeAnalyzer
from lib.store import MemoryEpisode, MemoryStore, is_code_index_category
from lib.lang_detect import is_source_extension
from lib.log import bind as _log_bind, get_logger as _get_logger

_log = _get_logger("hooks")


class MemoryHookHandler:
    """Handles Claude Code hook events for automatic memory capture."""

    _CONV_INTERVAL = 30  # save conversation episode every N post_tool calls

    def __init__(self, store: MemoryStore):
        self.store = store
        self.analyzer = CodeAnalyzer()
        self._embed_provider = None  # lazy: built on first episode save
        self._watcher_registry = None  # lazy: built on first PostToolUse
        self._project_root = ""  # set on first resolved event, used by _save()
        self._post_tool_count = 0   # incremented each PostToolUse; triggers periodic conv save

    def _save(self, episode: MemoryEpisode) -> bool:
        """Embed then save — single call site so embed is never forgotten."""
        self._embed(episode)
        ok = self.store.save_episode(episode)
        _log.info(
            "saved episode %s layer=%d cat=%s importance=%.2f embedded=%s",
            episode.id, episode.layer, episode.category, episode.importance,
            bool(episode.embedding),
            extra={"session_id": episode.session_id, "project": "-"},
        )
        try:
            _bus_emit(EpisodeSaved(
                session_id=episode.session_id,
                project=self._project_root or "-",
                id=episode.id,
                layer=episode.layer,
                category=episode.category,
                importance=round(episode.importance, 3),
                embedded=bool(episode.embedding),
            ))
        except Exception:
            pass
        return ok

    def _embed(self, episode: MemoryEpisode) -> None:
        """Attach embedding to episode in-place before saving.

        Uses the store's configured embedding_provider. Skips silently on any
        error so a missing Ollama / uninstalled package never breaks a hook.
        """
        if episode.embedding:
            return  # already embedded
        try:
            if self._embed_provider is None:
                from lib.embeddings import get_provider
                from lib import config as _cfg
                merged = _cfg.load()
                merged.update(self.store.config)
                self._embed_provider = get_provider(merged)
                _log.info(
                    "embedding provider initialised: %s",
                    merged.get("embedding_provider", "?"),
                    extra={"session_id": episode.session_id, "project": "-"},
                )
            text = f"{episode.title}\n{episode.content}".strip()
            if text:
                try:
                    episode.embedding = self._embed_provider.embed(text)
                    try:
                        _bus_emit(EmbedResult(
                            session_id=episode.session_id,
                            project=self._project_root or "-",
                            episode_id=episode.id,
                            provider=type(self._embed_provider).__name__,
                            success=True, fallback_used=False,
                        ))
                    except Exception:
                        pass
                except Exception as per_call_exc:
                    # Per-call failure (e.g. Ollama 500): fall back to HF or word2vec.
                    _log.warning(
                        "embed per-call failure for %s (%s), trying fallback",
                        episode.id, per_call_exc,
                        extra={"session_id": episode.session_id, "project": "-"},
                    )
                    from lib.embeddings import _hf_then_w2v
                    from lib import config as _cfg
                    merged = _cfg.load()
                    merged.update(self.store.config)
                    try:
                        fallback = _hf_then_w2v(merged)
                        episode.embedding = fallback.embed(text)
                        self._embed_provider = fallback  # promote so next call uses it
                        try:
                            _bus_emit(EmbedResult(
                                session_id=episode.session_id,
                                project=self._project_root or "-",
                                episode_id=episode.id,
                                provider=type(fallback).__name__,
                                success=True, fallback_used=True,
                            ))
                        except Exception:
                            pass
                    except Exception as fb_exc:
                        _log.warning(
                            "embed fallback also failed for %s: %s", episode.id, fb_exc,
                            extra={"session_id": episode.session_id, "project": "-"},
                        )
        except Exception as exc:
            _log.warning(
                "embed failed for %s: %s", episode.id, exc,
                extra={"session_id": episode.session_id, "project": "-"},
            )

    # ── Claude Code native translators ────────────────────────────────────────

    def build_context_block(
        self,
        tool: str,
        tool_input: Dict[str, Any],
        session_id: str,
    ) -> str:
        """Build a memory context string for injection into any agent.

        Returns empty string when nothing is worth injecting.
        Two sections:
          1. Instincts — L2 behavioural patterns relevant to this tool call,
             always included when present (confidence >= 0.5).
          2. Episodic memory — L0 code/conversation episodes for the file
             being touched (Edit/Write/Read/MultiEdit only).
        """
        sections: list[str] = []

        # ── 1. Instincts relevant to this tool call ────────────────────────
        try:
            all_eps = self.store.list_episodes()
            tool_instincts = [
                ep for ep in all_eps
                if ep.layer == 2
                and ep.category == "instinct"
                and tool in (ep.tags or [])
                and getattr(ep, "confidence", 0) >= 0.5
            ]
            if tool_instincts:
                tool_instincts.sort(key=lambda e: getattr(e, "confidence", 0), reverse=True)
                lines = ["[crisp instincts]"]
                for ep in tool_instincts[:5]:
                    conf = getattr(ep, "confidence", 0)
                    lines.append(f"- {ep.content.strip()} (confidence {conf:.2f})")
                sections.append("\n".join(lines))
        except Exception:
            pass

        # ── 2. Episodic memory for the file being touched ──────────────────
        file_path = tool_input.get("file_path", "")
        if tool in ("Read", "Edit", "Write", "MultiEdit") and file_path:
            try:
                file_path_str = str(Path(file_path).resolve())
                file_eps = [
                    ep for ep in (all_eps if "all_eps" in dir() else self.store.list_episodes())
                    if ep.source_path == file_path_str
                    and is_code_index_category(ep.category)
                    and "stale" not in (ep.tags or [])
                ]
                if file_eps:
                    lines = [f"[crisp memory: {Path(file_path).name}]"]
                    for ep in file_eps[:15]:
                        first = ep.content.splitlines()[0] if ep.content else ""
                        lines.append(f"- {ep.title or ep.id}: {first}"[:200])
                    sections.append("\n".join(lines))
            except Exception:
                pass

        return "\n\n".join(sections)

    def handle_claude_pre_tool_context(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """PreToolUse for Claude Code — returns hookSpecificOutput.additionalContext.

        Claude Code reads this field and prepends it to the tool context
        visible to the model. Returns {} when nothing to inject.
        """
        tool = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})
        session_id = data.get("session_id", "")

        block = self.build_context_block(tool, tool_input, session_id)
        if not block:
            return {}

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": block,
            }
        }

    def _get_watcher_registry(self):
        if self._watcher_registry is None:
            from lib.watchers import WatcherRegistry
            self._watcher_registry = WatcherRegistry()
        return self._watcher_registry

    def handle_claude_post_tool(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch PostToolUse through WatcherRegistry; also handle Read lazily."""
        tool = data.get("tool_name", "")

        if tool == "Read":
            return self.handle_claude_post_read(data)

        tool_input = data.get("tool_input", {})
        tool_output = data.get("tool_response", data.get("tool_output", {})) or {}
        if isinstance(tool_output, str):
            tool_output = {"stdout": tool_output}

        session_id = data.get("session_id", "unknown")
        project_root = data.get("cwd", "") or data.get("project_root", "")
        if project_root:
            self._project_root = project_root

        # Mark code_index stale for file edits (still needed for index freshness)
        file_path = tool_input.get("file_path", "")
        if tool in ("Write", "Edit", "MultiEdit") and file_path:
            self._mark_index_stale(file_path)

        registry = self._get_watcher_registry()
        result = registry.run(
            tool_name=tool,
            inp=tool_input,
            out=tool_output,
            session_id=session_id,
            project_root=project_root,
            save_fn=self._save,
        )

        # periodic async conversation snapshot every N tool calls
        self._post_tool_count += 1
        if self._post_tool_count % self._CONV_INTERVAL == 0:
            transcript_path = data.get("transcript_path", "")
            if transcript_path:
                self._async_conv_snapshot(session_id, transcript_path)

        if not result["watchers_matched"]:
            return {"status": "ignored", "reason": f"no watcher matched tool={tool}"}

        result["status"] = "ok"
        result["tool"] = tool
        return result

    def _async_conv_snapshot(self, session_id: str, transcript_path: str) -> None:
        """Save a conversation episode in a background thread — never blocks the hook."""
        import threading
        def _run():
            try:
                path = Path(transcript_path)
                if not path.exists():
                    return
                context, turn_count = self._read_transcript(path)
                if context and turn_count >= 3:
                    ep = self._conversation_episode(session_id, context, turn_count)
                    self._save(ep)
                    _log.info("periodic conv snapshot saved: %s turns=%d", ep.id, turn_count,
                              extra={"session_id": session_id, "project": self._project_root})
            except Exception as exc:
                _log.debug("periodic conv snapshot failed: %s", exc)
        threading.Thread(target=_run, daemon=True, name="crisp-conv-snapshot").start()

    def _is_indexed_fresh(self, file_path_str: str) -> bool:
        """True if file already has a non-stale code-index episode."""
        return any(
            ep.source_path == file_path_str
            and is_code_index_category(ep.category)
            and "stale" not in (ep.tags or [])
            for ep in self.store.list_episodes()
        )

    def _index_file(self, file_path: Path, session_id: str, registry=None) -> Dict[str, Any]:
        """Structural index of a single file: symbols only, no semantic summary.

        Shared by the lazy PostToolUse(Read) path and the eager SessionStart walk
        so there is exactly one code path that turns a file into code_index episodes.
        """
        if registry is None:
            from lib.indexers import IndexerRegistry
            registry = IndexerRegistry()

        indexer = registry.get_indexer(file_path)
        if indexer is None:
            return {"status": "ignored", "reason": "no indexer"}

        result = indexer.index(file_path)
        episodes = indexer.extract_episodes(result)
        saved = 0
        for ep_data in episodes:
            ep = MemoryEpisode(session_id=session_id, **ep_data)
            if self._save(ep):
                saved += 1

        file_path_str = str(file_path.resolve())
        self._ensure_dir_entries(file_path_str, session_id)

        return {"status": "indexed", "file": str(file_path), "episodes": saved}

    def handle_claude_post_read(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """PostToolUse(Read) — lazy structural index if file not yet indexed."""
        tool_input = data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")
        if not file_path or not is_source_extension(Path(file_path).suffix):
            return {"status": "ignored", "reason": "not a source file"}

        file_path_str = str(Path(file_path).resolve())
        if self._is_indexed_fresh(file_path_str):
            return {"status": "ignored", "reason": "already indexed"}

        try:
            return self._index_file(Path(file_path), data.get("session_id", "unknown"))
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # Directories skipped during a repo-wide walk (SessionStart eager index).
    IGNORE_DIRS = {".git", "node_modules", "android", "ios", "build", "dist",
                   ".venv", "venv", "__pycache__", ".next", "target"}

    def handle_claude_session_start(self, data: Dict[str, Any], max_files: int = 500) -> Dict[str, Any]:
        """SessionStart — eager, whole-repo structural index.

        Walks the project once so retrieval has L0 code_index episodes before
        Claude reads anything, instead of only building them up reactively as
        files happen to get Read/Edited during the session. Bounded by
        max_files per invocation so a huge repo can't stall session start;
        re-running (e.g. next session) picks up where it left off since
        already-fresh files are skipped via _is_indexed_fresh.
        """
        cwd = data.get("cwd") or data.get("project_dir")
        project_root = Path(cwd).resolve() if cwd else Path.cwd()
        self._project_root = str(project_root)
        session_id = data.get("session_id", "unknown")

        log = _log_bind(session_id=session_id, project=str(project_root), name="hooks")
        log.info("SessionStart project=%s", project_root)

        from lib.indexers import IndexerRegistry
        registry = IndexerRegistry()

        indexed = 0
        skipped_fresh = 0
        errors: List[str] = []
        capped = False

        for file_path in project_root.rglob("*"):
            if not file_path.is_file() or not is_source_extension(file_path.suffix):
                continue
            if any(part in self.IGNORE_DIRS for part in file_path.parts):
                continue

            if self._is_indexed_fresh(str(file_path.resolve())):
                skipped_fresh += 1
                continue

            if indexed >= max_files:
                capped = True
                break

            try:
                result = self._index_file(file_path, session_id, registry=registry)
                if result.get("status") == "indexed":
                    indexed += 1
            except Exception as e:
                errors.append(f"{file_path}: {e}")

        git_result = self._ingest_git_log(project_root, session_id)

        log.info(
            "SessionStart done: indexed=%d skipped_fresh=%d errors=%d capped=%s git_commits=%d",
            indexed, skipped_fresh, len(errors), capped,
            git_result.get("git_commits", 0),
        )

        return {
            "status": "ok",
            "indexed": indexed,
            "skipped_fresh": skipped_fresh,
            "errors": errors[:10],
            "error_count": len(errors),
            "capped": capped,
            "git_commits": git_result.get("git_commits", 0),
        }

    def handle_claude_stop(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Translate Stop payload → Stop handler."""
        return self.handle_stop({
            "session_id": data.get("session_id", "unknown"),
            "cwd": data.get("cwd") or data.get("project_dir") or "",
            "message": data.get("message", ""),
            "tool_outputs": data.get("tool_outputs", []),
        })

    def handle_claude_transcript(self, data: Dict[str, Any], event: str) -> Dict[str, Any]:
        """Handle SessionEnd or PreCompact — capture transcript then cascade consolidation.

        Reads the JSONL transcript, saves last N turns as an L0 conversation
        episode, then runs the full L0→L1→L2→L3 cascade.
        """
        session_id = data.get("session_id", "unknown")
        transcript_path = data.get("transcript_path", "")
        cwd = data.get("cwd") or data.get("project_dir") or ""

        log = _log_bind(session_id=session_id, project=cwd, name="hooks")
        log.info("%s transcript=%s", event, transcript_path or "(none)")

        result: Dict[str, Any] = {"event": event, "session_id": session_id}

        if transcript_path and Path(transcript_path).exists():
            context, turn_count = self._read_transcript(Path(transcript_path))
            if context and turn_count >= 3:
                ep = self._conversation_episode(session_id, context, turn_count)
                self._save(ep)
                result["conversation_episode"] = ep.id
                result["turns_captured"] = turn_count
                log.info("conversation episode saved: %s turns=%d", ep.id, turn_count)

        # Full cascade: L0→L1→L2→L3
        from lib.consolidate import MemoryReflector
        reflector = MemoryReflector(self.store)
        consolidation = reflector.consolidate()
        result["consolidation"] = consolidation
        log.info(
            "%s cascade done: l1=%d l2=%d l3=%d",
            event,
            consolidation.get("l1_created", 0),
            consolidation.get("l2_created", 0),
            consolidation.get("l3_created", 0),
        )

        return result

    # ── Internal Crisp Engine handlers ────────────────────────────────────────

    def handle_session_end(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle SessionEnd (internal format) — checkpoint + full cascade."""
        session_id = event_data.get("session_id", "unknown")
        cwd = event_data.get("cwd", "")
        log = _log_bind(session_id=session_id, project=cwd, name="hooks")
        log.info("SessionEnd (internal)")
        all_episodes = self.store.list_episodes(layer=0)
        session_episodes = [ep for ep in all_episodes if ep.session_id == session_id]
        self._create_checkpoint(session_id, session_episodes)

        from lib.consolidate import MemoryReflector
        reflector = MemoryReflector(self.store)
        consolidation = reflector.consolidate()

        return {
            "event": "SessionEnd",
            "session_id": session_id,
            "l0_count": len(session_episodes),
            "consolidation": consolidation,
        }

    def handle_stop(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle Stop — detect corrections and frustration, save as L0."""
        session_id = event_data.get("session_id", "unknown")
        cwd = event_data.get("cwd", "")
        message = event_data.get("message", "")
        tool_outputs = event_data.get("tool_outputs", [])
        log = _log_bind(session_id=session_id, project=cwd, name="hooks")
        log.debug("Stop message_len=%d tool_outputs=%d", len(message), len(tool_outputs))
        result: Dict[str, Any] = {"event": "Stop"}

        correction = self._detect_correction(message, tool_outputs)
        if correction:
            ep = self._create_correction_episode(correction, event_data)
            self._save(ep)
            result["correction"] = {"episode_id": ep.id}

        frustration = self._detect_frustration(message)
        if frustration:
            ep = self._create_frustration_episode(frustration, event_data)
            self._save(ep)
            result["frustration"] = {"episode_id": ep.id}

        failures = self._detect_tool_failures(tool_outputs)
        for failure in failures:
            ep = self._create_failure_episode(failure, event_data)
            self._save(ep)
        if failures:
            result["failures"] = len(failures)

        return result

    def handle_file_change(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle FileChange — capture diff + symbols as L0 episode."""
        file_path = event_data.get("file_path", "")
        if not file_path:
            return {"event": "FileChange", "error": "no file_path"}

        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="replace")
            content_hash = self.store.compute_hash(content)
        except Exception as e:
            return {"event": "FileChange", "error": str(e)}

        if self.store.get_file_state(file_path) == content_hash:
            return {"event": "FileChange", "unchanged": True}

        diff = event_data.get("diff", "")
        change_type = event_data.get("change_type", "edit")
        session_id = event_data.get("session_id", "unknown")

        code_elements = []
        if is_source_extension(Path(file_path).suffix):
            try:
                code_elements = self.analyzer.analyze_file(file_path)
            except Exception:
                pass

        lines = [f"File changed: {file_path}", f"Change type: {change_type}", ""]
        if diff:
            lines += ["## Diff", f"```diff\n{diff[:2000]}\n```", ""]
        if code_elements:
            lines += ["## Code Elements"] + [
                f"- `{e.signature}` ({e.type})" for e in code_elements[:15]
            ] + [""]

        episode_id = f"file_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        episode = MemoryEpisode(
            id=episode_id,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0,
            title=f"File change: {Path(file_path).name}",
            content="\n".join(lines),
            source_type="file",
            source_path=file_path,
            source_hash=content_hash,
            category="code",
            importance=0.6,
            tags=["file_change", Path(file_path).suffix.lstrip(".")],
            context_snapshot={
                "change_type": change_type,
                "code_elements": len(code_elements),
                "diff_size": len(diff),
            },
        )

        saved = self._save(episode)
        self.store.set_file_state(file_path, content_hash)
        return {"event": "FileChange", "episode_id": episode_id if saved else None, "duplicate": not saved}

    def handle_tool_failure(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle ToolFailure — save as high-importance L0 episode."""
        tool_name = event_data.get("tool_name", "unknown")
        error = event_data.get("error", "")
        tool_input = event_data.get("tool_input", {})

        content = f"Tool failure: {tool_name}\n\nError: {error}\n"
        if tool_input:
            content += f"\nInput: {json.dumps(tool_input, indent=2)}\n"

        episode_id = f"failure_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        episode = MemoryEpisode(
            id=episode_id,
            session_id=event_data.get("session_id", "unknown"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0,
            title=f"Tool failure: {tool_name}",
            content=content,
            source_type="tool",
            category="failure",
            importance=0.8,
            tags=["failure", tool_name],
            trigger_type="error_recovery",
            frustration_score=0.7,
            context_snapshot={"tool_name": tool_name, "error": error},
        )
        self._save(episode)
        return {"event": "ToolFailure", "episode_id": episode_id}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _ingest_git_log(
        self,
        project_root: Path,
        session_id: str,
        max_commits: int = 1000,
    ) -> Dict[str, Any]:
        """Ingest git commit history as L0 episodes (delta-only after first run).

        The SHA of the most-recently-ingested commit is stored in file_states
        under the key  git:last_sha:<project_root>  so subsequent SessionStarts
        only fetch new commits — O(delta) not O(all).

        Each commit becomes one L0 episode:
          category : git_commit
          title    : "<sha[:8]> <subject>"
          content  : subject + body + stat (files changed summary)
          lesson   : the commit subject (human-written "why")
          tags     : ["git", "commit"] + changed file extensions
          source_path : project_root
        """
        state_key = f"git:last_sha:{project_root}"
        last_sha = self.store.get_file_state(state_key) or ""

        # Build git log command — stop at last_sha if we have one
        fmt = "%x00".join(["%H", "%s", "%b", "%ai", "%an"])
        cmd = [
            "git", "-C", str(project_root),
            "log", f"--max-count={max_commits}",
            "--stat", "--stat-width=120",
            f"--format=COMMIT_START%n{fmt}%nCOMMIT_META_END",
        ]
        if last_sha:
            cmd.append(f"{last_sha}..HEAD")

        log = _log_bind(session_id=session_id, project=str(project_root), name="hooks")
        log.info("git log ingest: last_sha=%s", last_sha[:12] if last_sha else "(all)")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )
            raw = result.stdout.strip()
        except Exception as exc:
            log.warning("git log failed: %s", exc)
            return {"git_commits": 0}

        if not raw:
            return {"git_commits": 0}

        ingested = 0
        newest_sha = ""

        # Split on our sentinel
        blocks = raw.split("COMMIT_START\n")
        for block in blocks:
            if not block.strip():
                continue

            parts = block.split("\nCOMMIT_META_END\n", 1)
            if len(parts) < 2:
                continue

            meta_raw, stat_raw = parts
            meta_fields = meta_raw.split("\x00")
            if len(meta_fields) < 5:
                continue

            sha, subject, body, authored_at, author = meta_fields[:5]
            sha = sha.strip()
            subject = subject.strip()
            if not sha or not subject:
                continue

            if not newest_sha:
                newest_sha = sha

            # Parse stat block for changed extensions
            exts = set()
            for line in stat_raw.splitlines():
                line = line.strip()
                if "|" in line:
                    fname = line.split("|")[0].strip()
                    ext = Path(fname).suffix
                    if ext:
                        exts.add(ext.lstrip("."))

            stat_summary = stat_raw.strip()[-500:] if stat_raw.strip() else ""

            content_parts = [f"## {subject}"]
            if body.strip():
                content_parts.append(body.strip())
            if stat_summary:
                content_parts.append(f"### Files changed\n```\n{stat_summary}\n```")

            content = "\n\n".join(content_parts)
            content_hash = self.store.compute_hash(content)

            if self.store.get_by_content_hash(content_hash):
                continue  # already ingested this exact commit

            from lib.time_utils import now_iso
            ep = MemoryEpisode(
                id=f"git_{sha[:12]}",
                session_id=session_id,
                timestamp=authored_at or now_iso(),
                layer=0,
                title=f"{sha[:8]} {subject}",
                content=content,
                content_hash=content_hash,
                source_type="git",
                source_path=str(project_root),
                category="git_commit",
                importance=0.6,
                lesson=subject,  # commit subject IS the lesson
                tags=["git", "commit"] + list(exts)[:5],
            )
            self._save(ep)
            ingested += 1

        if newest_sha:
            self.store.set_file_state(state_key, newest_sha)

        log.info("git log ingest done: %d commits ingested newest=%s", ingested, newest_sha[:12] if newest_sha else "-")
        return {"git_commits": ingested}

    def _git_diff(self, file_path: str) -> str:
        """Get git diff for a file against HEAD."""
        try:
            result = subprocess.run(
                ["git", "diff", "HEAD", "--", file_path],
                capture_output=True, text=True, timeout=5,
                cwd=str(Path(file_path).parent),
            )
            diff = result.stdout.strip()
            if not diff:
                # Unstaged new file
                result = subprocess.run(
                    ["git", "diff", "--", file_path],
                    capture_output=True, text=True, timeout=5,
                    cwd=str(Path(file_path).parent),
                )
                diff = result.stdout.strip()
            return diff
        except Exception:
            return ""

    def _read_transcript(self, path: Path, max_turns: int = 30) -> tuple:
        """Read JSONL transcript, return (markdown_text, turn_count)."""
        turns: List[str] = []
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg = entry.get("message", {})
                    role = msg.get("role", "") if isinstance(msg, dict) else entry.get("role", "")
                    content = msg.get("content", "") if isinstance(msg, dict) else entry.get("content", "")

                    if role not in ("user", "assistant"):
                        continue

                    if isinstance(content, list):
                        content = "\n".join(
                            b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        )

                    if isinstance(content, str) and content.strip():
                        label = "User" if role == "user" else "Assistant"
                        turns.append(f"**{label}:** {content.strip()}")
        except Exception:
            return "", 0

        recent = turns[-max_turns:]
        text = "\n\n".join(recent)
        if len(text) > 15000:
            text = text[-15000:]
        return text, len(recent)

    def _conversation_episode(self, session_id: str, context: str, turn_count: int) -> MemoryEpisode:
        """Create an L0 episode from a conversation transcript."""
        episode_id = f"conv_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        return MemoryEpisode(
            id=episode_id,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0,
            title=f"Conversation: {session_id[:12]}",
            content=f"# Conversation Transcript\n\n{context}",
            source_type="conversation",
            category="conversation",
            importance=0.7,
            tags=["conversation", "transcript"],
            context_snapshot={"turns": turn_count},
        )

    def _detect_correction(self, message: str, tool_outputs: list) -> Optional[Dict]:
        msg = message.lower()
        patterns = [
            r"\bno\b", r"\bwrong\b", r"\bdon'?t\b", r"\bincorrect\b",
            r"\bthat'?s wrong\b", r"\bnot right\b", r"\bfix\b",
        ]
        for p in patterns:
            if re.search(p, msg):
                return {"type": "explicit", "message": message, "pattern": p}
        for output in tool_outputs:
            if isinstance(output, dict) and output.get("error"):
                return {"type": "tool_error", "error": output["error"]}
        return None

    def _detect_frustration(self, message: str) -> Optional[Dict]:
        msg = message.lower()
        patterns = [r"\bugh\b", r"\bagain\b", r"\bstill\b", r"\bnot working\b",
                    r"\bdoesn'?t work\b", r"\bargh\b", r"\bfrustrat\b", r"\bannoying\b"]
        matches = [p for p in patterns if re.search(p, msg)]
        if matches:
            return {"patterns": matches, "message": message, "score": min(1.0, len(matches) * 0.3)}
        return None

    def _detect_tool_failures(self, tool_outputs: list) -> list:
        return [o for o in tool_outputs
                if isinstance(o, dict) and (o.get("error") or o.get("status") == "error")]

    def _create_correction_episode(self, correction: Dict, event_data: Dict) -> MemoryEpisode:
        delta = correction.get("message") or correction.get("error", "")
        return MemoryEpisode(
            id=f"correction_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            session_id=event_data.get("session_id", "unknown"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0, title="Correction applied",
            content=f"User correction: {delta}",
            category="correction", importance=1.0,
            tags=["correction", "learning"],
            correction_applied=True, correction_delta=delta,
            is_permanent=True, trigger_type="reaction",
            user_sentiment="negative",
            lesson=f"Learned from correction: {delta[:100]}",
        )

    def _create_frustration_episode(self, frustration: Dict, event_data: Dict) -> MemoryEpisode:
        return MemoryEpisode(
            id=f"frustration_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            session_id=event_data.get("session_id", "unknown"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0, title="User frustration detected",
            content=f"Signals: {', '.join(frustration['patterns'])}\n\n{frustration['message']}",
            category="frustration", importance=0.7,
            tags=["frustration"], frustration_score=frustration["score"],
            user_sentiment="negative", trigger_type="reaction",
            lesson="User experienced frustration — consider different approach",
        )

    def _create_failure_episode(self, failure: Dict, event_data: Dict) -> MemoryEpisode:
        tool_name = failure.get("tool_name", "unknown")
        error = failure.get("error", "")
        return MemoryEpisode(
            id=f"failure_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            session_id=event_data.get("session_id", "unknown"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0, title=f"Tool failure: {tool_name}",
            content=f"Error: {error}\nTool: {tool_name}",
            category="failure", importance=0.8,
            tags=["failure", tool_name],
            trigger_type="error_recovery", root_cause=error,
            lesson=f"Tool {tool_name} failed — investigate root cause",
        )

    def _mark_index_stale(self, file_path: str) -> None:
        """Mark all code_index episodes for this file as stale."""
        file_path_str = str(Path(file_path).resolve())
        for ep in self.store.list_episodes():
            if ep.source_path == file_path_str and is_code_index_category(ep.category):
                if "stale" not in (ep.tags or []) and not ep.is_permanent:
                    ep.tags = list(ep.tags or []) + ["stale"]
                    self.store.delete_episode(ep.id)
                    self._save(ep)

    def _ensure_dir_entries(self, file_path: str, session_id: str) -> None:
        """Create placeholder dir index entries for up to 3 levels above the file.

        Uses file_states cache (O(1) per dir) to avoid scanning all episodes.
        """
        p = Path(file_path).resolve()

        # Detect project root (git root or cwd)
        root = p
        for ancestor in p.parents:
            if (ancestor / ".git").exists():
                root = ancestor
                break

        # Walk from file's parent up, capped at 3 levels from root
        dirs_to_ensure = []
        current = p.parent
        for _ in range(3):
            if current == root or current == current.parent:
                break
            try:
                depth = len(current.relative_to(root).parts)
            except ValueError:
                break
            if depth > 3:
                break
            dirs_to_ensure.append(current)
            current = current.parent

        for d in dirs_to_ensure:
            d_str = str(d)
            try:
                rel = str(d.relative_to(root))
            except ValueError:
                rel = d_str
            cache_key = f"dir_indexed:{rel}"
            # Fast O(1) check via file_states cache
            if self.store.get_file_state(cache_key):
                continue

            ep = MemoryEpisode(
                id=f"idx_dir_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{d.name}",
                session_id=session_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                layer=1,
                title=f"[dir] {d.name}",
                content=f"DIR: {d_str}\n(placeholder — awaiting semantic summary from /index skill)",
                source_type="code_index",
                source_path=d_str,
                category="code_index_dir",
                importance=0.6,
                tags=["code_index", "dir", "placeholder"],
            )
            self._save(ep)
            self.store.set_file_state(cache_key, "1")

    def _create_checkpoint(self, session_id: str, episodes: list):
        categories: Dict[str, int] = {}
        for ep in episodes:
            cat = ep.category or "uncategorized"
            categories[cat] = categories.get(cat, 0) + 1

        content = f"Session checkpoint: {session_id}\nTotal: {len(episodes)}\n\n"
        content += "\n".join(f"- {c}: {n}" for c, n in categories.items())

        self._save(MemoryEpisode(
            id=f"checkpoint_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0, title=f"Checkpoint: {session_id}",
            content=content, category="checkpoint", importance=0.5,
            tags=["checkpoint"],
            context_snapshot={"episode_count": len(episodes), "categories": categories},
        ))


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