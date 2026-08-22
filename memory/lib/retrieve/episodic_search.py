"""5-layer zoom retrieval system for episodic memory.

Implements storage-agnostic retrieval that works with any IMemoryStore.
"""

import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from ..embeddings import get_provider
from ..store import MemoryEpisode, MemoryStore
from ..time_utils import now_utc, parse_ts


class RetrievalOrchestrator:
    """Orchestrates 5-layer zoom search across memory hierarchy.

    Query flow:
    L3 (arcs) → L2 (clusters) → L1 (summaries) → L0 (episodes) → Graph expansion

    Composite reranking:
    score = vector_sim×0.4 + recency×0.3 + importance×0.2 + access_freq×0.1
    """

    def __init__(self, store: MemoryStore, config: dict = None):
        self.store = store
        self._config = config or {}
        self._embedding_provider = None  # built lazily on first search

    def _get_embedding_provider(self):
        if self._embedding_provider is None:
            try:
                self._embedding_provider = get_provider(self._config)
            except (ImportError, ValueError):
                self._embedding_provider = False  # sentinel: don't retry
        return self._embedding_provider if self._embedding_provider is not False else None

    def search(
        self, query: str, limit: int = 50, include_layers: List[int] = None
    ) -> List[Tuple[MemoryEpisode, float]]:
        """Execute 5-layer zoom search.

        Args:
            query: Search query (keyword-based)
            limit: Maximum number of results
            include_layers: Layers to include (defaults to all)

        Returns:
            List of (episode, score) tuples sorted by score descending
        """
        if include_layers is None:
            include_layers = [3, 2, 1, 0]

        all_results: Dict[str, Tuple[MemoryEpisode, float]] = {}

        # --- Keyword path ---
        for layer, lim in [(3, 10), (2, 15), (1, 20), (0, limit)]:
            if layer in include_layers:
                for ep, score in self._search_layer(query, layer=layer, limit=lim):
                    if ep.id not in all_results or score > all_results[ep.id][1]:
                        all_results[ep.id] = (ep, score)

        # --- Vector path (no-op when no provider available or store has no vecs) ---
        try:
            provider = self._get_embedding_provider()
            if provider is None:
                raise RuntimeError("no embedding provider")
            query_vec = provider.embed(query)
            vec_hits = self.store.search_by_embedding(query_vec, limit=limit)
            for ep_id, sim in vec_hits:
                if ep_id not in all_results:
                    ep = self.store.get_episode(ep_id)
                    if ep and ep.layer in include_layers:
                        all_results[ep_id] = (ep, sim * self._layer_boost(ep.layer))
                else:
                    # Merge: take the higher of keyword and vector scores
                    ep, kw_score = all_results[ep_id]
                    merged = max(kw_score, sim * self._layer_boost(ep.layer))
                    all_results[ep_id] = (ep, merged)
        except Exception:
            pass  # embedding provider unavailable — keyword-only is fine

        # Graph expansion: follow links from retrieved episodes
        expanded = self._expand_via_graph(list(all_results.values()), limit)

        # Composite reranking
        reranked = self._composite_rerank(expanded, limit)

        return reranked

    def _search_layer(
        self, query: str, layer: int, limit: int
    ) -> List[Tuple[MemoryEpisode, float]]:
        """Search within a specific layer."""
        # Use keyword search from store
        keyword_results = self.store.search_by_keyword(query, limit=limit)

        results = []
        for ep_id, score in keyword_results:
            episode = self.store.get_episode(ep_id)
            if episode and episode.layer == layer:
                # Apply layer-specific score boost
                layer_boost = self._layer_boost(layer)
                adjusted_score = score * layer_boost
                results.append((episode, adjusted_score))

        return results

    def _layer_boost(self, layer: int) -> float:
        """Get score boost for a layer (higher layers = more abstract = higher boost)."""
        boosts = {3: 2.0, 2: 1.5, 1: 1.2, 0: 1.0}
        return boosts.get(layer, 1.0)

    def _expand_via_graph(
        self, results: List[Tuple[MemoryEpisode, float]], limit: int
    ) -> List[Tuple[MemoryEpisode, float]]:
        """Expand results by following A-MEM graph links.

        Adds contextually related episodes that weren't caught by vector search.
        """
        expanded = {}  # episode_id -> (episode, score)
        for episode, base_score in results:
            expanded[episode.id] = (episode, base_score)

        for episode, base_score in results:
            # Follow outgoing links
            links = self.store.get_links(episode.id)
            for link in links:
                # get_links returns (target_id, link_type, strength) tuples
                target_id, link_type, strength = link

                if strength > 0.7 and target_id not in expanded:
                    target = self.store.get_episode(target_id)
                    if target:
                        # Boost based on link type
                        type_boost = self._link_type_boost(link_type)
                        linked_score = base_score * strength * type_boost
                        expanded[target_id] = (target, linked_score)

        return list(expanded.values())

    def _link_type_boost(self, link_type: str) -> float:
        """Get score boost for a link type."""
        boosts = {
            "similar": 1.0,
            "caused": 1.5,
            "contradicts": 1.3,
            "corrected_by": 1.8,
            "related": 0.8,
        }
        return boosts.get(link_type, 1.0)

    def _composite_rerank(
        self, results: List[Tuple[MemoryEpisode, float]], limit: int
    ) -> List[Tuple[MemoryEpisode, float]]:
        """Apply composite scoring and rerank.

        score = vector_sim×0.4 + recency×0.3 + importance×0.2 + access_freq×0.1

        Since we're using keyword search (no vector), we use the keyword score
        as the base and apply recency/importance/access boosts.
        """
        now = now_utc()
        reranked = []

        for episode, base_score in results:
            ts = parse_ts(episode.timestamp)
            age_days = max(0, (now - ts).days)
            recency = math.exp(-age_days / 30)  # Half-life ~30 days

            # Access frequency factor
            access_factor = min(2.0, 1.0 + 0.1 * episode.access_count)

            # Composite score
            composite = (
                base_score * 0.4  # keyword/vector similarity
                + recency * 0.3 * 10  # recency (scaled)
                + episode.importance * 0.2 * 10  # importance (scaled)
                + min(1.0, episode.access_count / 10) * 0.1 * 10  # access freq (scaled)
            ) * access_factor

            # Always include frustration/correction episodes
            if episode.frustration_score > 0.5 or episode.correction_applied:
                composite *= 2.0

            reranked.append((episode, composite))

        # Sort by composite score
        reranked.sort(key=lambda x: x[1], reverse=True)

        # Apply decay filter
        reranked = [(ep, score) for ep, score in reranked if ep.decay_score > 0.1]

        return reranked[:limit]

    def get_context_string(
        self, results: List[Tuple[MemoryEpisode, float]], max_tokens: int = 10000
    ) -> str:
        """Convert search results to context string for LLM injection.

        Truncates to stay within token limit.
        """
        lines = []
        total_tokens = 0

        for episode, score in results:
            # Estimate tokens (rough: 1 token ~ 4 chars)
            episode_text = self._format_episode_for_context(episode, score)
            episode_tokens = len(episode_text) // 4

            if total_tokens + episode_tokens > max_tokens:
                break

            lines.append(episode_text)
            total_tokens += episode_tokens

        return "\n\n".join(lines)

    def _format_episode_for_context(self, episode: MemoryEpisode, score: float) -> str:
        """Format a single episode for context injection."""
        lines = [
            f"--- Episode (Layer {episode.layer}, Score: {score:.2f}) ---",
            f"Title: {episode.title}",
            f"Category: {episode.category}",
            f"Tags: {', '.join(episode.tags) if episode.tags else 'none'}",
            f"Importance: {episode.importance:.2f}",
            f"Decay: {episode.decay_score:.2f}",
        ]

        if episode.correction_applied:
            lines.append(f"⚠️ CORRECTION: {episode.correction_delta}")
        if episode.frustration_score > 0.5:
            lines.append(f"⚠️ FRUSTRATION: {episode.frustration_score:.2f}")

        if episode.lesson:
            lines.append(f"Lesson: {episode.lesson}")
        if episode.root_cause:
            lines.append(f"Root cause: {episode.root_cause}")

        lines.append("")
        lines.append(
            episode.content[:500] + ("..." if len(episode.content) > 500 else "")
        )

        return "\n".join(lines)
