"""Transcript/git ingestion and the SessionEnd/PreCompact consolidation
trigger.

Note: the old hooks.py had a `_git_diff` method here that was never called
from anywhere (verified via grep) — dropped rather than carried forward as
dead code.
"""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from lib.affect import distill_to_episodes
from lib.log import bind as _log_bind, get_logger as _get_logger
from lib.store import MemoryEpisode, MemoryStore

from .episode_writer import EpisodeWriter

_log = _get_logger("hooks")


class TranscriptService:
    """Reads Claude Code JSONL transcripts and git history into episodes,
    and triggers the L0->L1->L2->L3 consolidation cascade."""

    def __init__(self, store: MemoryStore, writer: EpisodeWriter):
        self.store = store
        self.writer = writer

    def read_transcript(self, path: Path, max_turns: int = 30) -> tuple:
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

    def conversation_episode(self, session_id: str, context: str, turn_count: int) -> MemoryEpisode:
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

    def async_conv_snapshot(self, session_id: str, transcript_path: str, project_root: str) -> None:
        """Save a conversation episode in a background thread — never blocks the hook."""
        import threading

        def _run():
            try:
                path = Path(transcript_path)
                if not path.exists():
                    return
                context, turn_count = self.read_transcript(path)
                if context and turn_count >= 3:
                    ep = self.conversation_episode(session_id, context, turn_count)
                    self.writer.save(ep)
                    _log.info("periodic conv snapshot saved: %s turns=%d", ep.id, turn_count,
                              extra={"session_id": session_id, "project": project_root})
            except Exception as exc:
                _log.debug("periodic conv snapshot failed: %s", exc)
        threading.Thread(target=_run, daemon=True, name="crisp-conv-snapshot").start()

    def run_distill(self, session_id: str, context: str, project_root: str) -> None:
        """Extract preferences/corrections from a transcript.

        Called inline, not backgrounded: crisp-hook is a short-lived CLI
        process, and a daemon thread started here gets killed the instant
        main() returns and the process exits — verified directly this
        session (a real SessionEnd run produced zero distilled episodes and
        zero log lines from a threaded version of this method, because the
        ~50s cold Ollama call never got to start before the process died).
        SessionEnd/PreCompact already run synchronously by hook-config
        design (see HOOKS_CONFIG.md), so blocking here is the correct fix,
        not a workaround — bounded by generate_timeout either way.
        """
        try:
            from lib import config as _cfg
            from lib.generate import get_generate_provider
            merged = _cfg.load()
            merged.update(self.store.config)
            provider = get_generate_provider(merged)
            if provider is None:
                _log.debug("distill skipped: no generate provider configured/reachable",
                           extra={"session_id": session_id, "project": project_root})
                return
            episodes = distill_to_episodes(session_id, context, provider)
            for ep in episodes:
                self.writer.save(ep)
            if episodes:
                _log.info("distilled %d episode(s) (%d preferences, %d corrections)",
                          len(episodes),
                          sum(1 for e in episodes if e.category == "preference"),
                          sum(1 for e in episodes if e.category == "correction"),
                          extra={"session_id": session_id, "project": project_root})
        except Exception as exc:
            _log.debug("distill failed: %s", exc, extra={"session_id": session_id, "project": project_root})

    def ingest_git_log(self, project_root: Path, session_id: str, max_commits: int = 1000) -> Dict[str, Any]:
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
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            raw = result.stdout.strip()
        except Exception as exc:
            log.warning("git log failed: %s", exc)
            return {"git_commits": 0}

        if not raw:
            return {"git_commits": 0}

        ingested = 0
        newest_sha = ""

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
            self.writer.save(ep)
            ingested += 1

        if newest_sha:
            self.store.set_file_state(state_key, newest_sha)

        log.info("git log ingest done: %d commits ingested newest=%s", ingested, newest_sha[:12] if newest_sha else "-")
        return {"git_commits": ingested}

    def create_checkpoint(self, session_id: str, episodes: list):
        categories: Dict[str, int] = {}
        for ep in episodes:
            cat = ep.category or "uncategorized"
            categories[cat] = categories.get(cat, 0) + 1

        content = f"Session checkpoint: {session_id}\nTotal: {len(episodes)}\n\n"
        content += "\n".join(f"- {c}: {n}" for c, n in categories.items())

        self.writer.save(MemoryEpisode(
            id=f"checkpoint_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0, title=f"Checkpoint: {session_id}",
            content=content, category="checkpoint", importance=0.5,
            tags=["checkpoint"],
            context_snapshot={"episode_count": len(episodes), "categories": categories},
        ))

    def handle_claude_transcript(self, data: Dict[str, Any], event: str) -> Dict[str, Any]:
        """Handle SessionEnd or PreCompact — capture transcript then cascade consolidation.

        Reads the JSONL transcript, saves last N turns as an L0 conversation
        episode, then runs the L0->L1->L2->L3 cascade (L2/L3 gated off by
        default, see lib/consolidate/reflector.py).
        """
        session_id = data.get("session_id", "unknown")
        transcript_path = data.get("transcript_path", "")
        cwd = data.get("cwd") or data.get("project_dir") or ""

        log = _log_bind(session_id=session_id, project=cwd, name="hooks")
        log.info("%s transcript=%s", event, transcript_path or "(none)")

        result: Dict[str, Any] = {"event": event, "session_id": session_id}

        if transcript_path and Path(transcript_path).exists():
            context, turn_count = self.read_transcript(Path(transcript_path))
            if context and turn_count >= 3:
                ep = self.conversation_episode(session_id, context, turn_count)
                self.writer.save(ep)
                result["conversation_episode"] = ep.id
                result["turns_captured"] = turn_count
                log.info("conversation episode saved: %s turns=%d", ep.id, turn_count)
                self.run_distill(session_id, context, cwd)

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

    def handle_session_end(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle SessionEnd (internal Crisp Engine format) — checkpoint + cascade."""
        session_id = event_data.get("session_id", "unknown")
        cwd = event_data.get("cwd", "")
        log = _log_bind(session_id=session_id, project=cwd, name="hooks")
        log.info("SessionEnd (internal)")
        all_episodes = self.store.list_episodes(layer=0)
        session_episodes = [ep for ep in all_episodes if ep.session_id == session_id]
        self.create_checkpoint(session_id, session_episodes)

        from lib.consolidate import MemoryReflector
        reflector = MemoryReflector(self.store)
        consolidation = reflector.consolidate()

        return {
            "event": "SessionEnd",
            "session_id": session_id,
            "l0_count": len(session_episodes),
            "consolidation": consolidation,
        }
