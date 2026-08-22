"""Segmentation strategy: token-budgeted sliding window with overlap.

Segments carry a `context_only` prefix of the last N user turns so mood
series are scored on core turns only while judges still see context.
(ADR-0001 §9, research/session-outcome-signals.md "Overlap recipe".)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from .ids import segment_id
from .protocols import TokenEstimator
from .tokens import CharTokenEstimator
from .types import Segment, SessionIR


class SlidingWindowSegmenter:
    """Segmenter strategy: max tokens per core, overlap last K user turns."""

    def __init__(
        self,
        max_tokens: int,
        overlap_user_turns: int,
        estimator: TokenEstimator | None = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.overlap_user_turns = overlap_user_turns
        self._estimator = estimator or CharTokenEstimator()

    def segment(self, session: SessionIR) -> Sequence[Segment]:
        turns = list(session.turns)
        if not turns:
            return []
        toks = [
            (t, self._token_turn(t)) for t in turns
        ]

        segments: list[Segment] = []
        i = 0
        n = len(turns)

        while i < n:
            j = i
            budget = 0
            while j < n:
                cost = toks[j][1]
                if budget + cost > self.max_tokens:
                    break
                budget += cost
                j += 1
            if j == i:
                j = i + 1  # force at least one turn if it exceeds budget

            overlap_idx = self._find_overlap_start(turns, i)
            window = turns[overlap_idx:j]
            marked = []
            for k, t in enumerate(window):
                global_idx = overlap_idx + k
                marked.append(replace(t, context_only=(global_idx < i)))

            segments.append(
                Segment(
                    session_id=session.session_id,
                    segment_id=segment_id(session.session_id, i, j - 1),
                    turns=tuple(marked),
                    core_start_idx=i - overlap_idx,
                    approx_tokens=sum(
                        toks[g][1] for g in range(overlap_idx, j)
                    ),
                    overlap_user_turns=self.overlap_user_turns,
                )
            )

            if j >= n:
                break
            i = j

        return segments

    def _token_turn(self, turn: object) -> int:
        est = self._estimator
        text = getattr(turn, "text", "") or ""
        tools = getattr(turn, "tools", ()) or ()
        if hasattr(est, "estimate_turn"):
            return est.estimate_turn(turn)  # type: ignore[union-attr]
        base = est.estimate(text)
        return base + 8 * len(tools)  # type: ignore[arg-type]

    def _find_overlap_start(self, turns, core_start: int) -> int:
        if core_start <= 0 or self.overlap_user_turns <= 0:
            return core_start
        seen = 0
        start = core_start
        idx = core_start - 1
        while idx >= 0 and seen < self.overlap_user_turns:
            if turns[idx].role == "user" and turns[idx].text_kind in ("human", "mixed"):
                seen += 1
                start = idx
            idx -= 1
        return start