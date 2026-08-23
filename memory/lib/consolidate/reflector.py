"""Memory reflection and consolidation (L0 → L1 → L2 → L3)."""

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..code_index import CodeAnalyzer
from ..store import MemoryEpisode, MemoryStore
from ..time_utils import now_iso, parse_ts


def _dedup(items: list) -> list:
    """Return items with duplicates removed, preserving order."""
    seen: set = set()
    out = []
    for item in items:
        key = item.strip().lower()[:80]
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


class MemoryReflector:
    """Generates higher-level summaries from raw memory episodes."""

    def __init__(self, store: MemoryStore):
        self.store = store
        self.analyzer = CodeAnalyzer()

    def generate_l1_summary(self, episode_ids: List[str]) -> Optional[MemoryEpisode]:
        """Generate L1 summary from a group of L0 episodes.

        Content priority for lessons (highest → lowest):
          1. git_commit episodes  — subject line is a human-written lesson
          2. ep.lesson field      — set by hooks on correction/frustration detection
          3. ep.correction_delta  — what specifically changed
          4. first sentence of ep.content — better than nothing
        """
        episodes = []
        for eid in episode_ids:
            ep = self.store.get_episode(eid)
            if ep and ep.layer == 0:
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

        # ── Lessons from all non-commit episodes ───────────────────────────
        lessons = []
        for ep in episodes:
            if ep.category == "git_commit":
                continue
            lesson = (
                ep.lesson
                or ep.correction_delta
                or self._first_sentence(ep.content)
            )
            if lesson:
                lessons.append(lesson)

        if lessons:
            lines.append("## Lessons / observations")
            for l in _dedup(lessons)[:10]:
                lines.append(f"- {l}")
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

    @staticmethod
    def _first_sentence(text: str) -> str:
        """Extract first meaningful sentence from content, skip markdown headers."""
        for line in text.splitlines():
            line = line.strip().lstrip("#").strip()
            if len(line) > 20 and not line.startswith("```"):
                # Truncate at first sentence boundary
                m = re.search(r"[.!?]", line)
                return line[: m.end()].strip() if m else line[:120]
        return ""

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

    def generate_l2_cluster(
        self, l1_ids: List[str], topic: str
    ) -> Optional[MemoryEpisode]:
        """Generate L2 topic cluster from L1 summaries."""
        l1_summaries = []
        for sid in l1_ids:
            ep = self.store.get_episode(sid)
            if ep and ep.layer == 1:
                l1_summaries.append(ep)

        if not l1_summaries:
            return None

        lines = [f"# Topic Cluster: {topic}", ""]
        lines.append(f"**{len(l1_summaries)} session summaries**")
        lines.append(f"**First session:** {l1_summaries[0].timestamp[:10]}")
        lines.append(f"**Last session:** {l1_summaries[-1].timestamp[:10]}")
        lines.append("")

        # Extract evolution
        lines.append("## Evolution")
        for l1 in l1_summaries:
            date = l1.timestamp[:10]
            # Extract first paragraph
            first_para = (
                l1.content.split("\n\n")[0]
                if "\n\n" in l1.content
                else l1.content[:200]
            )
            lines.append(f"- **{date}**: {first_para[:150]}...")
        lines.append("")

        # Common patterns
        all_lessons = []
        for l1 in l1_summaries:
            # Extract lessons from content
            lessons = re.findall(
                r"- (?:Key lessons?:|Lesson:|Learned:)\s*(.+)", l1.content
            )
            all_lessons.extend(lessons)

        if all_lessons:
            lines.append("## Recurring Patterns")
            for lesson in all_lessons[:10]:
                lines.append(f"- {lesson}")
            lines.append("")

        content = "\n".join(lines)

        l2 = MemoryEpisode(
            id=f"l2_{parse_ts(now_iso()).strftime('%Y%m%d_%H%M%S')}_{hash(content) & 0xFFFFFF:06x}",
            session_id="cluster",
            timestamp=now_iso(),
            layer=2,
            title=f"Cluster: {topic}",
            content=content,
            tags=[topic, "cluster"],
            category="cluster",
            importance=0.8,
            parent_id="",
            linked_ids=l1_ids,
            context_snapshot={"source_summaries": l1_ids, "topic": topic},
            is_permanent=True,
        )

        return l2

    def generate_l3_arc(
        self, l2_ids: List[str], arc_name: str
    ) -> Optional[MemoryEpisode]:
        """Generate L3 life-arc from L2 clusters."""
        l2_clusters = []
        for cid in l2_ids:
            ep = self.store.get_episode(cid)
            if ep and ep.layer == 2:
                l2_clusters.append(ep)

        if not l2_clusters:
            return None

        lines = [f"# Life Arc: {arc_name}", ""]
        lines.append(f"**{len(l2_clusters)} topic clusters**")
        lines.append(
            f"**Time span:** {l2_clusters[0].timestamp[:10]} to {l2_clusters[-1].timestamp[:10]}"
        )
        lines.append("")

        lines.append("## Arc Overview")
        for l2 in l2_clusters:
            lines.append(f"### {l2.title}")
            # First meaningful paragraph
            paras = [
                p
                for p in l2.content.split("\n\n")
                if p.strip() and not p.startswith("#")
            ]
            if paras:
                lines.append(paras[0][:300] + "...")
            lines.append("")

        lines.append("## Meta-Lessons")
        lines.append("Patterns that span across all clusters:")
        lines.append("- User preferences and working style evolution")
        lines.append("- Recurring challenges and solutions")
        lines.append("- Skill development trajectory")
        lines.append("- Decision-making patterns")

        content = "\n".join(lines)

        l3 = MemoryEpisode(
            id=f"l3_{parse_ts(now_iso()).strftime('%Y%m%d_%H%M%S')}_{hash(content) & 0xFFFFFF:06x}",
            session_id="arc",
            timestamp=now_iso(),
            layer=3,
            title=f"Arc: {arc_name}",
            content=content,
            tags=[arc_name, "arc", "meta"],
            category="arc",
            importance=1.0,
            parent_id="",
            linked_ids=l2_ids,
            context_snapshot={"source_clusters": l2_ids, "arc_name": arc_name},
            is_permanent=True,
        )

        return l3

    def consolidate(self, max_l0_per_batch: int = 20) -> Dict[str, Any]:
        """Run consolidation: L0 → L1, L1 → L2, L2 → L3."""
        result = {"l1_created": 0, "l2_created": 0, "l3_created": 0}

        # Get all L0 episodes not yet summarized
        l0_episodes = self.store.list_episodes(layer=0)
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
                    self.store.save_episode(l1)
                    # Update parent links
                    for ep in batch:
                        ep.parent_id = l1.id
                        self.store._write_raw(ep)
                    result["l1_created"] += 1

        # L1 → L2 clustering (simplified: group by common tags)
        l1_episodes = self.store.list_episodes(layer=1)
        if len(l1_episodes) >= 10:
            # Group by common categories
            categories = defaultdict(list)
            for ep in l1_episodes:
                cat = (
                    ep.context_snapshot.get("categories", ["general"])[0]
                    if ep.context_snapshot
                    else "general"
                )
                categories[cat].append(ep.id)

            for topic, ids in categories.items():
                if len(ids) >= 10:
                    l2 = self.generate_l2_cluster(ids[:10], topic)
                    if l2:
                        self.store.save_episode(l2)
                        result["l2_created"] += 1

        # L2 → L3 (if we have multiple clusters)
        l2_episodes = self.store.list_episodes(layer=2)
        if len(l2_episodes) >= 3:
            l3 = self.generate_l3_arc(
                [ep.id for ep in l2_episodes[:3]], "Personal Development"
            )
            if l3:
                self.store.save_episode(l3)
                result["l3_created"] += 1

        return result
