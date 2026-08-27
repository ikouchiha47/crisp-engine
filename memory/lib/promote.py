"""Auto-promotion of recurring patterns straight into the hot path — the
fast lane discussed against L3 (see plans/ADR-004-hot-path-and-feedback-loops.md):
L3 needs the full L0->L1->L2->L3 cascade (hundreds of episodes) before a
standing law reaches identity.md. This runs on a handful of matching
`undelivered` episodes and writes directly to hot/user.md, no cascade wait.

DAG (all steps real, none stubbed):
  P1 fetch candidates   -> unclustered `undelivered` episodes, read-only
  P2 cluster by         -> reuse the same cosine-similarity grouping
     embedding             already used for L1->L2 (see reflector.py)
  P3 threshold check    -> cluster size >= MIN_CLUSTER, cheap, no LLM
  P4 synthesize rule    -> one small dspy.Signature call, scoped to just
                            this cluster's texts
  P5 dedup vs hot file  -> content-substring check against hot/user.md
                            (same mechanism injection.py already uses;
                            NOT the full LLM dedup from ADR-004 A2/A3 —
                            that's still separate, unbuilt work)
  P6 write + mark        -> preference_episode() + EpisodeWriter.save() +
     promoted               HotMemoryStore.apply_patch("user", ...) +
                            log_generation("promote", ...) + parent_id
                            marks the source episodes so they're never
                            re-promoted on the next consolidate() run
"""
from __future__ import annotations

import math
from typing import List, Optional

import dspy

from .affect import preference_episode
from .episode_writer import EpisodeWriter
from .generation_log import log_generation
from .hot_memory import HotMemoryStore
from .store import MemoryEpisode, MemoryStore

MIN_CLUSTER = 3


class PromoteRecurringPattern(dspy.Signature):
    """Several 'asked for, not delivered' notes from different sessions
    that a similarity check found related. If they really are the same
    underlying recurring problem, state it as one clean standing rule for
    future sessions to follow — not a summary of the individual notes.
    If they turn out NOT to actually be the same pattern on closer
    reading, say so via is_real_pattern=False rather than forcing a rule
    out of unrelated items."""
    undelivered_notes: str = dspy.InputField(desc="Several undelivered notes, newline-joined.")
    is_real_pattern: bool = dspy.OutputField(
        desc="True only if these genuinely describe the same recurring problem."
    )
    rule: str = dspy.OutputField(
        desc="One clean sentence: the standing rule this recurrence implies. "
        "Empty string if is_real_pattern is False."
    )


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else -1.0


def _cluster(candidates: List[MemoryEpisode], min_size: int) -> List[List[MemoryEpisode]]:
    remaining = [ep for ep in candidates if ep.embedding]
    groups: List[List[MemoryEpisode]] = []
    while len(remaining) >= min_size:
        seed = remaining[0]
        scored = sorted(remaining[1:], key=lambda e: _cosine(seed.embedding, e.embedding), reverse=True)
        group = [seed] + [e for e in scored[: min_size - 1] if _cosine(seed.embedding, e.embedding) > 0.75]
        if len(group) < min_size:
            remaining = remaining[1:]
            continue
        ids = {e.id for e in group}
        remaining = [e for e in remaining if e.id not in ids]
        groups.append(group)
    return groups


def promote_recurring_patterns(store: MemoryStore, lm: Optional[dspy.LM]) -> int:
    """P1-P6, run once per consolidate() call. Returns count promoted."""
    if lm is None:
        return 0

    candidates = [
        ep for ep in store.list_episodes(layer=1, include_embedding=True)
        if ep.category == "undelivered" and not ep.parent_id
    ]
    if len(candidates) < MIN_CLUSTER:
        return 0

    writer = EpisodeWriter(store)
    hot = HotMemoryStore(store)
    promoted = 0

    for group in _cluster(candidates, MIN_CLUSTER):
        joined = "\n".join(f"- {ep.content.strip()}" for ep in group)
        try:
            with dspy.context(lm=lm):
                out = dspy.Predict(PromoteRecurringPattern)(undelivered_notes=joined)
        except Exception:
            continue

        log_generation("promote", {
            "is_real_pattern": bool(out.is_real_pattern),
            "rule": out.rule,
            "source_episode_ids": [ep.id for ep in group],
        })

        if not out.is_real_pattern or not str(out.rule).strip():
            continue
        rule = str(out.rule).strip()

        # P5: dedup — same content-substring check injection.py already
        # uses. Real LLM-based dedup (ADR-004 A2/A3) is separate, unbuilt.
        existing = hot.read("user")
        if rule in existing:
            continue

        ep = preference_episode("auto-promoted", rule)
        ep.tags = list(ep.tags) + ["auto-promoted"]
        writer.save(ep)
        hot.apply_patch("user", rule, "auto-promoted", "-")

        for src in group:
            src.parent_id = ep.id
            store._write_raw(src)

        promoted += 1

    return promoted
