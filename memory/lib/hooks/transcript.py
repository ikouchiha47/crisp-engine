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

from lib import config as _cfg
from lib.affect import distill_to_episodes
from lib.bus import emit as _bus_emit, FrustrationSignal, InstinctAutoTrigger
from lib.consolidate import MemoryReflector
from lib.dspy_lm import get_dspy_lm
from lib.instincts import InstinctEngine
from lib.log import bind as _log_bind, get_logger as _get_logger
from lib.narrate import narrate_title
from lib.store import MemoryEpisode, MemoryStore

from ..episode_writer import EpisodeWriter
from ..hot_memory import HotMemoryStore

_log = _get_logger("hooks")


class TranscriptService:
    """Reads Claude Code JSONL transcripts and git history into episodes,
    and triggers the L0->L1->L2->L3 consolidation cascade."""

    def __init__(self, store: MemoryStore, writer: EpisodeWriter):
        self.store = store
        self.writer = writer
        self._lm = None  # lazy: built once, reused by title + distill calls

    def _get_lm(self):
        if self._lm is None:
            merged = _cfg.load()
            merged.update(self.store.config)
            self._lm = get_dspy_lm(merged)
        return self._lm

    def _parse_all_turns(self, path: Path) -> List[str]:
        """Parse every user/assistant turn in a JSONL transcript, in order.
        No truncation, no windowing — that happens in read_new_turns."""
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
            return []
        return turns

    def _cursor_key(self, session_id: str) -> str:
        return f"transcript:cursor:{session_id}"

    def read_new_turns(self, path: Path, session_id: str, max_turns: int = 30) -> List[tuple]:
        """Read every turn NEW since this session's last capture, paginated
        into chunks of up to max_turns each. Returns a list of
        (markdown_text, turn_count) — one entry per chunk, covering the
        FULL delta, not just a trailing window.

        Previously this method (read_transcript) re-read the whole file
        every call and kept only the last 30 turns, silently discarding
        everything before that — verified this session: a 1191-turn real
        session produced only 6 captured episodes across 2 weeks because
        of this. A turn-index cursor persisted via store.get_file_state
        (same delta-tracking pattern already used for git log ingestion's
        last_sha) fixes it: nothing captured once is ever re-read, and
        nothing new is ever silently dropped.
        """
        turns = self._parse_all_turns(path)
        if not turns:
            return []

        cursor_key = self._cursor_key(session_id)
        try:
            cursor = int(self.store.get_file_state(cursor_key) or "0")
        except (ValueError, TypeError):
            cursor = 0
        cursor = max(0, min(cursor, len(turns)))  # transcript could have been reset/rotated

        new_turns = turns[cursor:]
        if not new_turns:
            return []

        chunks: List[tuple] = []
        for i in range(0, len(new_turns), max_turns):
            page = new_turns[i:i + max_turns]
            text = "\n\n".join(page)
            if len(text) > 15000:
                # A single page is still bounded for an LLM call — but this
                # is a per-page cap on one already-small unit, not a
                # silent-drop of the rest of the session like before.
                text = text[:15000] + "\n\n_(truncated: this page alone exceeded 15000 chars)_"
            chunks.append((text, len(page)))

        self.store.set_file_state(cursor_key, str(len(turns)))
        return chunks

    def conversation_episode(self, session_id: str, context: str, turn_count: int) -> MemoryEpisode:
        """Create an L0 episode from a conversation transcript chunk.

        Title is a real LLM call (lib/narrate.py::narrate_title) — a
        heuristic "first meaningful line" version was tried and rejected:
        it just truncates real content into a fake-looking label instead of
        describing what the chunk was actually about. Falls back to the
        literal string "Conversation" (an honest null value, not a fake
        summary) only when no provider is reachable at all.
        """
        title = narrate_title(self._get_lm(), context) or "Conversation"
        episode_id = f"conv_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S%f')}"
        return MemoryEpisode(
            id=episode_id,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0,
            title=title,
            content=f"# Conversation Transcript\n\n{context}",
            source_type="conversation",
            category="conversation",
            importance=0.7,
            tags=["conversation", "transcript"],
            context_snapshot={"turns": turn_count},
        )

    def async_conv_snapshot(self, session_id: str, transcript_path: str, project_root: str) -> None:
        """Save conversation episodes for every new chunk in a background
        thread — never blocks the hook.

        Same daemon-thread-dies-with-the-process caveat as before applies
        here (PostToolUse is async by hook config, so the process may exit
        before this finishes) — not fixed in this pass, only the capture
        completeness (read_new_turns) was. Flagged, not silently ignored.
        """
        import threading

        def _run():
            try:
                path = Path(transcript_path)
                if not path.exists():
                    return
                for context, turn_count in self.read_new_turns(path, session_id):
                    if context and turn_count >= 3:
                        ep = self.conversation_episode(session_id, context, turn_count)
                        self.writer.save(ep)
                        _log.info("periodic conv snapshot saved: %s turns=%d", ep.id, turn_count,
                                  extra={"session_id": session_id, "project": project_root})
            except Exception as exc:
                _log.debug("periodic conv snapshot failed: %s", exc)
        threading.Thread(target=_run, daemon=True, name="crisp-conv-snapshot").start()

    def run_distill(self, session_id: str, context: str, project_root: str, prior_summary: str = "") -> str:
        """Extract preferences/corrections from a transcript chunk.

        Called inline, not backgrounded: crisp-hook is a short-lived CLI
        process, and a daemon thread started here gets killed the instant
        main() returns and the process exits — verified directly this
        session (a real SessionEnd run produced zero distilled episodes and
        zero log lines from a threaded version of this method, because the
        ~50s cold Ollama call never got to start before the process died).
        SessionEnd/PreCompact already run synchronously by hook-config
        design (see HOOKS_CONFIG.md), so blocking here is the correct fix,
        not a workaround — bounded by generate_timeout either way.

        prior_summary: the previous chunk's returned summary (same session),
        for continuity across read_new_turns() pages — see affect.py. This
        call's own summary is returned so the caller can thread it into the
        next chunk.
        """
        try:
            lm = self._get_lm()
            if lm is None:
                _log.debug("distill skipped: no dspy lm configured/reachable",
                           extra={"session_id": session_id, "project": project_root})
                return ""
            episodes, summary, frustration = distill_to_episodes(
                session_id, context, lm, prior_summary=prior_summary,
            )
            hot = HotMemoryStore(self.store)
            for ep in episodes:
                self.writer.save(ep)
                if ep.category == "preference":
                    hot.apply_patch("user", ep.content, session_id, project_root)
                elif ep.category == "correction":
                    hot.apply_patch("memory", ep.content, session_id, project_root)
                elif ep.category == "reversal":
                    # Separate hot file from corrections (ADR-004 Track A):
                    # a reversal is a settled decision that changed, not a
                    # this-turn mistake — different kind of fact, was
                    # incorrectly blended into "memory" before.
                    hot.apply_patch("reversal", ep.content, session_id, project_root)
                # "undelivered" stays deliberately out of hot inject — it's
                # an open item, not yet a settled standing fact.
            if episodes:
                _log.info(
                    "distilled %d episode(s) (%d preferences, %d corrections, "
                    "%d reversals, %d undelivered)",
                    len(episodes),
                    sum(1 for e in episodes if e.category == "preference"),
                    sum(1 for e in episodes if e.category == "correction"),
                    sum(1 for e in episodes if e.category == "reversal"),
                    sum(1 for e in episodes if e.category == "undelivered"),
                    extra={"session_id": session_id, "project": project_root},
                )
            if frustration["present"]:
                try:
                    _bus_emit(FrustrationSignal(
                        session_id=session_id, project=project_root or "-",
                        intensity=frustration["intensity"],
                        exit_type=frustration["exit_type"],
                        profanity_present=frustration["profanity_present"],
                        # Link to the actual substance extracted from this
                        # same chunk — see docs/transcript-audit-findings.md
                        # §5.1's frustration_signal.payload field. Without
                        # this the signal is a bare mood score with nothing
                        # for a future reader to act on.
                        payload_episode_ids=[ep.id for ep in episodes],
                    ))
                except Exception:
                    pass
            return summary
        except Exception as exc:
            _log.debug("distill failed: %s", exc, extra={"session_id": session_id, "project": project_root})
            return ""

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

        Reads EVERY turn new since this session's last capture (paginated
        into chunks, see read_new_turns — not just a trailing window), saves
        one L0 conversation episode + runs distill per chunk, then runs the
        L0->L1->L2->L3 cascade (L2/L3 gated off by default, see
        lib/consolidate/reflector.py).
        """
        session_id = data.get("session_id", "unknown")
        transcript_path = data.get("transcript_path", "")
        cwd = data.get("cwd") or data.get("project_dir") or ""

        log = _log_bind(session_id=session_id, project=cwd, name="hooks")
        log.info("%s transcript=%s", event, transcript_path or "(none)")

        result: Dict[str, Any] = {"event": event, "session_id": session_id}

        if transcript_path and Path(transcript_path).exists():
            episode_ids = []
            total_turns = 0
            prior_summary = ""
            for context, turn_count in self.read_new_turns(Path(transcript_path), session_id):
                if not context or turn_count < 3:
                    continue
                ep = self.conversation_episode(session_id, context, turn_count)
                self.writer.save(ep)
                episode_ids.append(ep.id)
                total_turns += turn_count
                log.info("conversation episode saved: %s turns=%d", ep.id, turn_count)
                prior_summary = self.run_distill(session_id, context, cwd, prior_summary=prior_summary)
            if episode_ids:
                result["conversation_episodes"] = episode_ids
                result["turns_captured"] = total_turns

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

        result["instincts_auto"] = self._auto_trigger_instincts(session_id, cwd)

        return result

    def _auto_trigger_instincts(self, session_id: str, project_root: str) -> Dict[str, Any]:
        """Call InstinctEngine.evolve()/promote() — both already implement
        their own gating (confidence threshold / distinct-project count),
        they were just never called automatically anywhere. This is that
        call site, nothing more."""
        try:
            ie = InstinctEngine(self.store)
            evolved_path = ie.evolve()
            evolved = 1 if evolved_path else 0

            promoted = 0
            for inst in ie.list_instincts(min_confidence=0.0):
                try:
                    if ie.promote(inst.id):
                        promoted += 1
                except Exception:
                    pass

            try:
                _bus_emit(InstinctAutoTrigger(
                    session_id=session_id, project=project_root or "-",
                    evolved=evolved, promoted=promoted,
                ))
            except Exception:
                pass

            return {"evolved": evolved, "promoted": promoted}
        except Exception as exc:
            _log.debug("instinct auto-trigger failed: %s", exc,
                       extra={"session_id": session_id, "project": project_root})
            return {"evolved": 0, "promoted": 0}

    def handle_session_end(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle SessionEnd (internal Crisp Engine format) — checkpoint + cascade."""
        session_id = event_data.get("session_id", "unknown")
        cwd = event_data.get("cwd", "")
        log = _log_bind(session_id=session_id, project=cwd, name="hooks")
        log.info("SessionEnd (internal)")
        all_episodes = self.store.list_episodes(layer=0)
        session_episodes = [ep for ep in all_episodes if ep.session_id == session_id]
        self.create_checkpoint(session_id, session_episodes)

        reflector = MemoryReflector(self.store)
        consolidation = reflector.consolidate()

        return {
            "event": "SessionEnd",
            "session_id": session_id,
            "l0_count": len(session_episodes),
            "consolidation": consolidation,
        }
