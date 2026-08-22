"""Token approximation strategies (ADR-0001 §4)."""

from __future__ import annotations

from dataclasses import dataclass

from .types import TurnIR


@dataclass(frozen=True)
class CharTokenEstimator:
    """Conservative char/4 heuristic, no external deps."""

    chars_per_token: int = 4
    per_tool_overhead: int = 8

    def estimate(self, text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // self.chars_per_token)

    def estimate_turn(self, turn: TurnIR) -> int:
        base = self.estimate(turn.text)
        if turn.tools:
            base += self.per_tool_overhead * len(turn.tools)
        return base