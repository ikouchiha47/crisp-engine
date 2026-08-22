"""Pruning service: Ebbinghaus decay, conflict detection, archival.

Implements memory management strategies for the layered knowledge base.
"""

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..store import MemoryEpisode, MemoryStore
from ..time_utils import now_utc, parse_ts


class PruningService:
    """Manages memory decay, conflict resolution, and archival.

    Implements:
    - Ebbinghaus forgetting curve for decay
    - Semantic conflict detection
    - Automatic archival of low-value memories
    - Staleness detection
    """

    def __init__(self, store: MemoryStore):
        self.store = store

    def compute_decay_score(
        self, episode: MemoryEpisode, now: datetime = None
    ) -> float:
        """Compute Ebbinghaus decay score for an episode.

        decay = 0.5 ^ (days_since / half_life)

        Access count provides a boost (frequently accessed memories
        decay slower).
        """
        if episode.is_permanent:
            return 1.0

        if now is None:
            now = now_utc()

        half_life_days = self.store.config["decay_half_life_days"].get(
            f"l{episode.layer}", 30
        )

        # Determine last accessed time
        if episode.last_accessed:
            try:
                last_accessed = parse_ts(episode.last_accessed)
            except:
                last_accessed = now
        else:
            try:
                last_accessed = parse_ts(episode.timestamp)
            except:
                last_accessed = now

        days_since = max(0, (now - last_accessed).days)

        # Ebbinghaus decay
        decay = 0.5 ** (days_since / half_life_days)

        # Access boost (frequently accessed memories decay slower)
        if episode.access_count > 0:
            decay *= min(2.0, 1.0 + 0.1 * episode.access_count)

        return max(0.0, min(1.0, decay))

    def update_all_decay_scores(self) -> int:
        """Update decay scores for all episodes.

        Returns:
            Number of episodes updated
        """
        now = now_utc()
        updated = 0

        for layer in range(4):
            episodes = self.store.list_episodes(layer=layer)
            for episode in episodes:
                new_decay = self.compute_decay_score(episode, now)
                if abs(episode.decay_score - new_decay) > 0.01:
                    episode.decay_score = new_decay
                    self.store._write_raw(episode)
                    updated += 1

        return updated

    def detect_conflicts(
        self, episode: MemoryEpisode, threshold: float = None
    ) -> List[Tuple[MemoryEpisode, float]]:
        """Detect semantic conflicts with existing episodes.

        Uses Jaccard similarity on content to find contradictions.
        Episodes with high similarity but conflicting signals
        (e.g., correction vs original) are flagged as conflicts.

        Args:
            episode: Episode to check for conflicts
            threshold: Similarity threshold (default from config)

        Returns:
            List of (conflicting_episode, similarity) tuples
        """
        if threshold is None:
            threshold = self.store.config.get("similarity_threshold", 0.92)

        conflicts = []

        # Find similar episodes
        similar = self.store.find_similar(episode.content, threshold=threshold)

        for ep_id, similarity in similar:
            other = self.store.get_episode(ep_id)
            if not other:
                continue

            # Check for conflict signals
            if self._is_conflict(episode, other):
                conflicts.append((other, similarity))

        return conflicts

    def _is_conflict(self, ep1: MemoryEpisode, ep2: MemoryEpisode) -> bool:
        """Determine if two episodes are in conflict.

        Conflict indicators:
        - One is a correction of the other
        - Contradictory lessons/root causes
        - Same source but different outcomes
        """
        # Direct correction link
        if ep1.correction_applied and ep2.id in ep1.linked_ids:
            return True
        if ep2.correction_applied and ep1.id in ep2.linked_ids:
            return True

        # Same source, different lessons
        if ep1.source_path and ep2.source_path and ep1.source_path == ep2.source_path:
            if ep1.lesson and ep2.lesson:
                # Different lessons from same source = potential conflict
                if ep1.lesson != ep2.lesson:
                    return True

        # Contradictory categories
        if ep1.category == "bug" and ep2.category == "fix":
            # This is actually complementary, not conflicting
            return False

        # One says it's correct, other says it's wrong
        if ep1.correction_applied != ep2.correction_applied:
            if ep1.source_path == ep2.source_path:
                return True

        return False

    def resolve_conflicts(
        self, conflicts: List[Tuple[MemoryEpisode, float]]
    ) -> Dict[str, Any]:
        """Resolve detected conflicts using timestamp/priority.

        Strategy:
        1. Keep newer episode
        2. If one is a correction, keep it
        3. If one has higher importance, keep it
        4. Archive the other
        """
        result = {"kept": [], "archived": []}

        for episode, similarity in conflicts:
            # Find linked conflicting episodes
            linked = self.store.get_links(episode.id)

            for link in linked:
                if link["link_type"] == "contradicts":
                    other = self.store.get_episode(link["target_id"])
                    if not other:
                        continue

                    # Determine which to keep
                    keep, archive = self._choose_conflict_winner(episode, other)

                    if keep and archive:
                        # Archive the loser
                        archive_id = archive.id
                        # Move to archived subdirectory
                        archive_path = (
                            self.store.layers_path / f"l{archive.layer}" / "archived"
                        )
                        archive_path.mkdir(exist_ok=True)
                        old_file = self.store._filepath(archive.id, archive.layer)
                        if old_file.exists():
                            new_file = archive_path / old_file.name
                            old_file.rename(new_file)

                        result["kept"].append(keep.id)
                        result["archived"].append(archive_id)

        return result

    def _choose_conflict_winner(
        self, ep1: MemoryEpisode, ep2: MemoryEpisode
    ) -> Tuple[Optional[MemoryEpisode], Optional[MemoryEpisode]]:
        """Choose which episode to keep in a conflict.

        Priority:
        1. Correction episodes win
        2. Newer episodes win
        3. Higher importance wins
        4. Higher decay score wins (more recently accessed)
        """
        # Correction wins
        if ep1.correction_applied and not ep2.correction_applied:
            return ep1, ep2
        if ep2.correction_applied and not ep1.correction_applied:
            return ep2, ep1

        # Newer wins
        try:
            ts1 = parse_ts(ep1.timestamp)
            ts2 = parse_ts(ep2.timestamp)
            if ts1 > ts2:
                return ep1, ep2
            if ts2 > ts1:
                return ep2, ep1
        except:
            pass

        # Higher importance wins
        if ep1.importance > ep2.importance:
            return ep1, ep2
        if ep2.importance > ep1.importance:
            return ep2, ep1

        # Higher decay (more recent access) wins
        if ep1.decay_score > ep2.decay_score:
            return ep1, ep2

        return ep2, ep1

    def archive_low_value(self, decay_threshold: float = None) -> Dict[str, Any]:
        """Archive episodes with decay below threshold.

        Args:
            decay_threshold: Minimum decay score to keep (default from config)

        Returns:
            Archive statistics
        """
        if decay_threshold is None:
            decay_threshold = 0.05  # Default: archive if decay < 5%

        result = {"archived": 0, "skipped_permanent": 0}

        for layer in range(4):
            # Only archive L0 and L1 (L2/L3 are more valuable)
            if layer > 1:
                continue

            episodes = self.store.list_episodes(layer=layer)
            for episode in episodes:
                if episode.is_permanent:
                    result["skipped_permanent"] += 1
                    continue

                if episode.decay_score < decay_threshold:
                    self._archive_episode(episode)
                    result["archived"] += 1

        return result

    def _archive_episode(self, episode: MemoryEpisode):
        """Move episode to archived directory."""
        archive_dir = self.store.layers_path / f"l{episode.layer}" / "archived"
        archive_dir.mkdir(exist_ok=True)

        old_path = self.store._filepath(episode.id, episode.layer)
        if old_path.exists():
            new_path = archive_dir / old_path.name
            old_path.rename(new_path)

    def delete_ancient_archives(self, days: int = 365) -> int:
        """Delete archived episodes older than specified days.

        Args:
            days: Age threshold in days

        Returns:
            Number of files deleted
        """
        now = now_utc()
        deleted = 0

        for layer in range(4):
            archive_dir = self.store.layers_path / f"l{layer}" / "archived"
            if not archive_dir.exists():
                continue

            for fp in archive_dir.glob("*.md"):
                try:
                    mtime = datetime.fromtimestamp(fp.stat().st_mtime, timezone.utc)
                    if (now - mtime).days > days:
                        fp.unlink()
                        deleted += 1
                except:
                    pass

        return deleted

    def prune(self) -> Dict[str, Any]:
        """Run full pruning pipeline.

        Steps:
        1. Update decay scores
        2. Detect and resolve conflicts
        3. Archive low-value episodes
        4. Delete ancient archives

        Returns:
            Pruning statistics
        """
        result = {
            "updated_decay": 0,
            "conflicts_resolved": 0,
            "archived": 0,
            "deleted": 0,
        }

        # Step 1: Update decay scores
        result["updated_decay"] = self.update_all_decay_scores()

        # Step 2: Detect conflicts (sample recent episodes)
        recent_episodes = []
        for layer in range(2):  # Only L0 and L1
            eps = self.store.list_episodes(layer=layer)
            recent_episodes.extend(eps[:50])  # Sample first 50

        all_conflicts = []
        for episode in recent_episodes:
            conflicts = self.detect_conflicts(episode)
            all_conflicts.extend(conflicts)

        # Step 3: Resolve conflicts
        if all_conflicts:
            resolution = self.resolve_conflicts(all_conflicts)
            result["conflicts_resolved"] = len(resolution.get("archived", []))

        # Step 4: Archive low-value
        archive_result = self.archive_low_value()
        result["archived"] = archive_result.get("archived", 0)

        # Step 5: Delete ancient archives
        result["deleted"] = self.delete_ancient_archives()

        return result
