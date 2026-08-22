"""Rule-based outcome scoring (ADR-0001 §12).

Stateless strategy — given SessionIR + Segment + SegmentFeatures,
produce OutcomeScores. This is the component a future LLM judge or
trained classifier swaps in without touching the pipeline.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from .types import OutcomeScores, Resolved, Segment, SegmentFeatures, SessionIR


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def mean(xs: Sequence[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def linreg_slope(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(xs) / n
    num = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(xs))
    den = sum((i - mean_x) ** 2 for i in range(n))
    return num / den if den else 0.0


class RuleScorer:
    """Scorer strategy: deterministic rules + VADER/domain mood series."""

    def score(
        self,
        session: SessionIR,
        segment: Segment,
        features: SegmentFeatures,
    ) -> OutcomeScores:
        f_series = list(features.f_series)
        s_series = list(features.s_series)

        frustration = mean(f_series)
        satisfaction_raw = mean(
            [s - f for s, f in zip(s_series, f_series)]
        ) if s_series else 0.0
        satisfaction = clamp(satisfaction_raw, -1.0, 1.0)
        slope = linreg_slope(f_series)

        resolved = self._resolve(
            session, features, satisfaction, frustration
        )
        confidence = self._confidence(features, resolved)

        evidence = self._evidence(features)

        return OutcomeScores(
            satisfaction=satisfaction,
            frustration=clamp(frustration, 0.0, 1.0),
            frustration_slope=slope,
            resolved=resolved,
            explicit_thanks=features.explicit_thanks,
            explicit_anger=features.explicit_anger,
            correction_rate=features.correction_rate,
            confidence=confidence,
            evidence=evidence,
        )

    @staticmethod
    def _resolve(
        session: SessionIR,
        features: SegmentFeatures,
        satisfaction: float,
        frustration: float,
    ) -> Resolved:
        work = session.work_landed or (session.todos_all_done is True)

        if features.explicit_anger and not work:
            return "no"
        if features.explicit_thanks and not features.explicit_anger:
            return "yes"
        if work and frustration < 0.35:
            return "yes"
        if work and 0.35 <= frustration < 0.6:
            return "partial"
        if not work and frustration >= 0.6:
            return "no"
        if satisfaction >= 0.15 and work:
            return "yes"
        return "unknown" if not (work or features.explicit_anger) else "partial"

    @staticmethod
    def _confidence(features: SegmentFeatures, resolved: Resolved) -> float:
        c = 0.8 if (features.explicit_thanks or features.explicit_anger) else 0.5
        if not features.s_series:
            c = 0.25
        if resolved == "unknown":
            c *= 0.8
        if len(features.f_series) >= 3:
            c = min(1.0, c + 0.1)
        return clamp(c, 0.0, 1.0)

    @staticmethod
    def _evidence(features: SegmentFeatures) -> tuple[str, ...]:
        ev: list[str] = []
        if features.explicit_thanks:
            ev.append("explicit_thanks")
        if features.explicit_anger:
            ev.append("explicit_anger")
        if features.correction_rate > 0:
            ev.append(f"corr_rate={features.correction_rate:.2f}")
        if features.tool_error_rate > 0:
            ev.append(f"tool_err={features.tool_error_rate:.2f}")
        return tuple(ev)[:12]


def routing_label_hint(scores: OutcomeScores) -> dict:
    should = (
        scores.frustration_slope > 0.05
        and scores.resolved in ("no", "partial", "unknown")
    ) or (scores.frustration >= 0.6 and scores.resolved != "yes")
    return {
        "should_escalate": should,
        "reason": "high_frustration_slope+unresolved"
        if should
        else "ok_or_resolved",
    }