"""Memory reflection and consolidation (L0 → L1 → L2 → L3)."""

import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import dspy

from .. import config as _cfg
from ..bus import (
    emit as _bus_emit,
    frustration_signals_for_session as _frustration_signals_for_session,
    NarrateResult,
    ReflectRan,
)
from ..code_index import CodeAnalyzer
from ..dspy_lm import get_dspy_lm
from ..episode_writer import EpisodeWriter
from ..hot_memory import HotMemoryStore
from ..memory_policy import is_episodic
from ..narrate import narrate_l1, narrate_l2, narrate_l3
from ..promote import promote_recurring_patterns
from ..store import MemoryEpisode, MemoryStore
from ..time_utils import now_iso, parse_ts


class MemoryReflector:
    """Generates higher-level summaries from raw memory episodes."""

    def __init__(self, store: MemoryStore, lm: Optional[dspy.LM] = None):
        self.store = store
        self.analyzer = CodeAnalyzer()
        self.lm = lm  # built once per consolidate() call, not per method
        # L1s need real embeddings for _cluster_l1_by_embedding — reuse the
        # same embed-then-save path every other hook collaborator uses,
        # rather than a second embedding call site.
        self._writer = EpisodeWriter(store)
        self._hot = HotMemoryStore(store)

    def generate_l1_summary(self, episode_ids: List[str]) -> Optional[MemoryEpisode]:
        """Generate L1 summary from a group of L0 episodes.

        The narrative ("Lessons / observations") is real LLM output
        (lib/narrate.py) — no template fallback. If no provider is
        configured/reachable, this returns None rather than writing a
        signature-dump or first-sentence-scrape "summary": quiet beats
        actively wrong (same rule build_context_block follows).
        Commits/corrections/friction-points sections stay — those are real
        derived data, not narrative, nothing templated about them.
        """
        episodes = []
        for eid in episode_ids:
            ep = self.store.get_episode(eid)
            if ep and ep.layer == 0 and is_episodic(ep.category):
                episodes.append(ep)

        if not episodes:
            return None

        # Sort chronologically so date range is meaningful
        episodes.sort(key=lambda e: parse_ts(e.timestamp))

        categories = defaultdict(list)
        all_tags: set = set()
        total_importance = 0.0

        for ep in episodes:
            categories[ep.category or "uncategorized"].append(ep)
            all_tags.update(ep.tags)
            total_importance += ep.importance

        ts_start = parse_ts(episodes[0].timestamp).strftime("%Y-%m-%d")
        ts_end   = parse_ts(episodes[-1].timestamp).strftime("%Y-%m-%d")

        lines = ["# Session Summary", "",
                 f"**Episodes:** {len(episodes)}  |  "
                 f"**Date range:** {ts_start} → {ts_end}", ""]

        # ── Git commits ────────────────────────────────────────────────────
        commits = [ep for ep in episodes if ep.category == "git_commit"]
        if commits:
            lines.append("## Commits")
            for ep in commits[:20]:
                lines.append(f"- {ep.title}")
            if len(commits) > 20:
                lines.append(f"  _(+{len(commits) - 20} more)_")
            lines.append("")

        # ── Corrections ────────────────────────────────────────────────────
        corrections = [ep for ep in episodes if ep.correction_applied]
        if corrections:
            lines.append("## Corrections")
            for ep in corrections[:10]:
                delta = ep.correction_delta or ep.lesson or ep.title
                lines.append(f"- {delta}")
            lines.append("")

        # ── Frustration signals ────────────────────────────────────────────
        frustrations = [ep for ep in episodes if ep.frustration_score > 0.5]
        if frustrations:
            lines.append(f"## Friction points ({len(frustrations)})")
            for ep in frustrations[:5]:
                lines.append(f"- [{ep.frustration_score:.1f}] {ep.title}")
            lines.append("")

        # ── Lessons: real LLM narrative only, no template fallback ─────────
        narrative_input = [ep.content for ep in episodes if ep.category != "git_commit" and ep.content]
        narrative = narrate_l1(self.lm, narrative_input)
        used_llm = narrative is not None
        try:
            _bus_emit(NarrateResult(
                session_id=episodes[0].session_id, project="-", layer=1, used_llm=used_llm,
            ))
        except Exception:
            pass
        if not narrative:
            return None  # quiet beats actively wrong — no L1 this round

        lines.append("## Lessons / observations")
        lines.append(narrative)
        lines.append("")

        # ── Code elements touched ──────────────────────────────────────────
        seen_files: set = set()
        code_elements = []
        for ep in episodes:
            if ep.source_type == "file" and ep.source_path and ep.source_path not in seen_files:
                seen_files.add(ep.source_path)
                try:
                    code_elements.extend(self.analyzer.analyze_file(ep.source_path))
                except Exception:
                    pass

        if code_elements:
            lines.append("## Code touched")
            for elem in code_elements[:15]:
                lines.append(f"- `{elem.signature}` ({elem.type}) — {Path(elem.file_path).name}")
            lines.append("")

        # ── Per-category breakdown (skip git_commit, already shown) ────────
        other_cats = {k: v for k, v in categories.items() if k != "git_commit"}
        if other_cats:
            lines.append("## By category")
            for cat, eps in sorted(other_cats.items(), key=lambda x: -len(x[1])):
                lines.append(f"- **{cat}**: {len(eps)} episodes")
            lines.append("")

        content = "\n".join(lines)
        ts_now = now_iso()

        # Derive a meaningful title from commits or dominant category
        if commits:
            title_hint = commits[0].title.split(" ", 1)[1] if " " in commits[0].title else commits[0].title
            title = f"Summary: {title_hint[:60]}"
        else:
            dominant = max(categories.items(), key=lambda x: len(x[1]))[0]
            title = f"Summary: {dominant} ({ts_start})"

        l1 = MemoryEpisode(
            id=f"l1_{ts_now.replace(':', '').replace('-', '')[:15]}_{hash(content) & 0xFFFFFF:06x}",
            session_id=episodes[0].session_id,
            timestamp=ts_now,
            layer=1,
            title=title,
            content=content,
            tags=list(all_tags),
            category="summary",
            importance=min(1.0, total_importance / max(len(episodes), 1)),
            linked_ids=episode_ids,
            context_snapshot={
                "source_episodes": episode_ids,
                "categories": list(categories.keys()),
                "commit_count": len(commits),
            },
        )
        return l1

    def _common_tags(self, episodes: List[MemoryEpisode]) -> List[str]:
        """Find common tags across episodes."""
        if not episodes:
            return []
        tag_counts = defaultdict(int)
        for ep in episodes:
            for tag in ep.tags:
                tag_counts[tag] += 1
        # Tags appearing in >50% of episodes
        threshold = len(episodes) / 2
        return [tag for tag, count in tag_counts.items() if count >= threshold]

    def _l1_text_with_frustration(self, l1: MemoryEpisode) -> str:
        """L1 content plus a compact summary of that session's real
        FrustrationSignal bus events (ADR-004 item 4: these were
        persisted and never read back into anything — this is that read).
        Appended as plain text so narrate_l2's existing "RECURRING AGENT
        FAILURES"/process-rule categories can pick up on a pattern like
        repeated high-intensity/compact_under_fire exits across sessions,
        without a schema change to narrate_l2 itself."""
        signals = _frustration_signals_for_session(l1.session_id)
        if not signals:
            return l1.content
        high = sum(1 for s in signals if s.get("intensity") in ("high", "extreme"))
        exits = [s.get("exit_type") for s in signals if s.get("exit_type") not in (None, "none")]
        if not high and not exits:
            return l1.content
        note = f"[This session had {len(signals)} frustration signal(s)"
        if high:
            note += f", {high} high/extreme intensity"
        if exits:
            note += f", exit types: {', '.join(sorted(set(exits)))}"
        note += "]"
        return l1.content + "\n\n" + note

    def generate_l2_cluster(
        self, l1_ids: List[str]
    ) -> Optional[MemoryEpisode]:
        """Generate L2 topic cluster from L1 summaries."""
        l1_summaries = []
        for sid in l1_ids:
            ep = self.store.get_episode(sid)
            if ep and ep.layer == 1:
                l1_summaries.append(ep)

        if not l1_summaries:
            return None

        # Sort chronologically — the source of the AUDIT.md-flagged reversed
        # date-range bug was taking [0]/[-1] on an unsorted list.
        l1_summaries.sort(key=lambda e: parse_ts(e.timestamp))

        l2_input = [self._l1_text_with_frustration(l1) for l1 in l1_summaries]
        narrated = narrate_l2(self.lm, l2_input)
        used_llm = narrated is not None
        try:
            _bus_emit(NarrateResult(session_id="cluster", project="-", layer=2, used_llm=used_llm))
        except Exception:
            pass
        if not narrated:
            return None  # quiet beats actively wrong — no L2 this round
        real_topic, synthesis = narrated

        lines = [f"# Topic Cluster: {real_topic}", ""]
        lines.append(f"**{len(l1_summaries)} session summaries**")
        lines.append(f"**First session:** {l1_summaries[0].timestamp[:10]}")
        lines.append(f"**Last session:** {l1_summaries[-1].timestamp[:10]}")
        lines.append("")
        lines.append("## Synthesis")
        lines.append(synthesis)

        content = "\n".join(lines)

        l2 = MemoryEpisode(
            id=f"l2_{parse_ts(now_iso()).strftime('%Y%m%d_%H%M%S')}_{hash(content) & 0xFFFFFF:06x}",
            session_id="cluster",
            timestamp=now_iso(),
            layer=2,
            title=f"Cluster: {real_topic}",
            content=content,
            tags=[real_topic, "cluster"],
            category="cluster",
            importance=0.8,
            parent_id="",
            linked_ids=l1_ids,
            context_snapshot={"source_summaries": l1_ids, "topic": real_topic},
            is_permanent=True,
        )

        return l2

    def generate_l3_arc(self, l2_ids: List[str]) -> Optional[MemoryEpisode]:
        """Generate L3 life-arc from L2 clusters. Arc name is derived by the
        LLM from actual cluster content — no caller-supplied name anymore
        (was always "Personal Development" in practice, ignoring content)."""
        l2_clusters = []
        for cid in l2_ids:
            ep = self.store.get_episode(cid)
            if ep and ep.layer == 2:
                l2_clusters.append(ep)

        if not l2_clusters:
            return None

        l2_clusters.sort(key=lambda e: parse_ts(e.timestamp))

        narrated = narrate_l3(self.lm, [l2.content for l2 in l2_clusters])
        used_llm = narrated is not None
        try:
            _bus_emit(NarrateResult(session_id="arc", project="-", layer=3, used_llm=used_llm))
        except Exception:
            pass
        if not narrated:
            return None  # quiet beats actively wrong — no L3 this round
        real_arc_name, meta_lessons = narrated

        # ADR-004 Track A: full-replace hot/identity.md with the latest L3
        # arc's standing laws — this is the fix for L3 being write-only
        # (confirmed this session: nothing previously read L3 back into
        # anything). Read at SessionStart, not per-tool-call — see
        # lib/hooks/injection.py and lib/hooks/__init__.py.
        try:
            self._hot.write_identity(meta_lessons)
        except Exception:
            pass

        lines = [f"# Life Arc: {real_arc_name}", ""]
        lines.append(f"**{len(l2_clusters)} topic clusters**")
        lines.append(
            f"**Time span:** {l2_clusters[0].timestamp[:10]} to {l2_clusters[-1].timestamp[:10]}"
        )
        lines.append("")
        lines.append("## Meta-Lessons")
        for lesson in meta_lessons:
            lines.append(f"- {lesson}")

        content = "\n".join(lines)

        l3 = MemoryEpisode(
            id=f"l3_{parse_ts(now_iso()).strftime('%Y%m%d_%H%M%S')}_{hash(content) & 0xFFFFFF:06x}",
            session_id="arc",
            timestamp=now_iso(),
            layer=3,
            title=f"Arc: {real_arc_name}",
            content=content,
            tags=[real_arc_name, "arc", "meta"],
            category="arc",
            importance=1.0,
            parent_id="",
            linked_ids=l2_ids,
            context_snapshot={"source_clusters": l2_ids, "arc_name": real_arc_name},
            is_permanent=True,
        )

        return l3

    def _cluster_l1_by_embedding(
        self, l1_episodes: List[MemoryEpisode], min_size: int
    ) -> List[List[MemoryEpisode]]:
        """Group L1 summaries by real semantic similarity instead of the old
        `context_snapshot["categories"][0]` string bucket — that bucket is
        almost always "conversation" for every L1 (is_episodic() collapses
        most real categories into it), so it produced one giant, arbitrarily
        ordered pool rather than anything resembling a topic. This groups by
        cosine similarity on the same embeddings every other episode already
        gets (see EpisodeWriter.embed), greedily: pick an unclustered seed,
        take its `min_size - 1` nearest unclustered neighbors, repeat.
        Episodes with no embedding (embed provider unreachable) are dropped
        from clustering entirely rather than grouped arbitrarily — quiet
        beats actively wrong.
        """
        def cosine(a: List[float], b: List[float]) -> float:
            if not a or not b or len(a) != len(b):
                return -1.0
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(y * y for y in b))
            if na == 0 or nb == 0:
                return -1.0
            return dot / (na * nb)

        remaining = [ep for ep in l1_episodes if ep.embedding]
        groups: List[List[MemoryEpisode]] = []
        while len(remaining) >= min_size:
            seed = remaining[0]
            scored = sorted(
                remaining[1:], key=lambda ep: cosine(seed.embedding, ep.embedding), reverse=True,
            )
            group = [seed] + scored[: min_size - 1]
            group_ids = {ep.id for ep in group}
            remaining = [ep for ep in remaining if ep.id not in group_ids]
            groups.append(group)
        return groups

    def consolidate(self, max_l0_per_batch: int = 20, force_l2l3: bool = False) -> Dict[str, Any]:
        """Run consolidation: L0 → L1 always; L1 → L2 → L3 only when
        `consolidation_l2l3_auto` is enabled in config, or force_l2l3=True
        (e.g. `crisp reflect --force-l2l3`) for a manual run.

        L2/L3 are gated off by default: they cluster/arc whatever L1s exist
        with no quality check, and previously ran on placeholder-derived and
        code_element-poisoned L1s. Off by default until Phase 3 (local
        distill) gives L1 real content worth clustering.
        """
        result = {"l1_created": 0, "l2_created": 0, "l3_created": 0}

        if self.lm is None:
            merged = _cfg.load()
            merged.update(self.store.config)
            self.lm = get_dspy_lm(merged)

        # Get all L0 episodes not yet summarized — episodic categories only.
        # code_element/code_index_dir are structural (see memory_policy.py),
        # never consolidatable into a session summary.
        l0_episodes = [ep for ep in self.store.list_episodes(layer=0) if is_episodic(ep.category)]
        l0_episodes.sort(key=lambda e: e.timestamp)

        # Group by session and create L1 summaries
        sessions = defaultdict(list)
        for ep in l0_episodes:
            sessions[ep.session_id].append(ep)

        for session_id, eps in sessions.items():
            # Check if already summarized (has parent L1)
            unsummarized = [ep for ep in eps if not ep.parent_id]
            if len(unsummarized) >= max_l0_per_batch:
                # Create L1 summary
                batch = unsummarized[:max_l0_per_batch]
                l1 = self.generate_l1_summary([ep.id for ep in batch])
                if l1:
                    # embed-then-save so _cluster_l1_by_embedding has a
                    # real vector to work with once this L1 is eligible
                    self._writer.save(l1)
                    # Update parent links
                    for ep in batch:
                        ep.parent_id = l1.id
                        self.store._write_raw(ep)
                    result["l1_created"] += 1

        l2l3_auto = str(_cfg.load().get("consolidation_l2l3_auto", "")).lower() in ("1", "true", "yes")
        if l2l3_auto or force_l2l3:
            # L1 → L2 clustering, embedding-similarity based (see
            # _cluster_l1_by_embedding). Only L1s not already folded into an
            # L2 are eligible (parent_id unset) — the old version had no
            # idempotency marker and would keep re-clustering the same
            # ids[:10] slice forever.
            unclustered_l1 = [
                ep for ep in self.store.list_episodes(layer=1, include_embedding=True)
                if not ep.parent_id
            ]
            if len(unclustered_l1) >= 10:
                for group in self._cluster_l1_by_embedding(unclustered_l1, min_size=10):
                    l2 = self.generate_l2_cluster([ep.id for ep in group])
                    if l2:
                        self.store.save_episode(l2)
                        for ep in group:
                            ep.parent_id = l2.id
                            self.store._write_raw(ep)
                        result["l2_created"] += 1

            # L2 → L3: same idempotency fix — only L2s not already folded
            # into an L3 (parent_id unset), oldest first (arcs accumulate
            # chronologically, not by topic-similarity).
            unclustered_l2 = [ep for ep in self.store.list_episodes(layer=2) if not ep.parent_id]
            unclustered_l2.sort(key=lambda e: parse_ts(e.timestamp))
            for i in range(0, len(unclustered_l2) - len(unclustered_l2) % 3, 3):
                batch = unclustered_l2[i:i + 3]
                l3 = self.generate_l3_arc([ep.id for ep in batch])
                if l3:
                    self.store.save_episode(l3)
                    for ep in batch:
                        ep.parent_id = l3.id
                        self.store._write_raw(ep)
                    result["l3_created"] += 1

        # Fast lane, independent of the L1->L2->L3 cascade above (see
        # lib/promote.py, plans/ADR-004-hot-path-and-feedback-loops.md):
        # a handful of recurring `undelivered` episodes writes straight
        # into the hot path, without waiting for full cascade volume.
        try:
            result["promoted"] = promote_recurring_patterns(self.store, self.lm)
        except Exception:
            result["promoted"] = 0

        try:
            _bus_emit(ReflectRan(
                l0_in=len(l0_episodes),
                l1_created=result["l1_created"],
                l2_created=result["l2_created"],
                l3_created=result["l3_created"],
            ))
        except Exception:
            pass

        return result
