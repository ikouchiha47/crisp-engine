"""Feature extraction: mood series + structural signals (ADR-0001 §11)."""

from __future__ import annotations

from .mood import MoodAnalyzer, VaderDomainMoodAnalyzer
from .types import Segment, SegmentFeatures, SessionIR

MOOD = VaderDomainMoodAnalyzer()


def tool_error_rate(segment: Segment) -> float:
    tools = [x for t in segment.core_turns for x in t.tools]
    if not tools:
        return 0.0
    errs = sum(1 for x in tools if x.ok is False)
    return errs / len(tools)


class DefaultFeatureExtractor:
    """FeatureExtractor strategy: iterate core user turns, track retry sim."""

    def __init__(self, analyzer: MoodAnalyzer = MOOD) -> None:
        self._analyzer = analyzer

    def extract(self, session: SessionIR, segment: Segment) -> SegmentFeatures:
        moods = []
        prev_user_text: str | None = None
        for turn in segment.core_turns:
            if turn.role != "user":
                continue
            m = self._analyzer.analyze(turn, prev_user_text)
            if m.s_t or m.f_t or m.evidence:
                moods.append(turn.turn_id)
            if m.domain_pos or m.domain_neg:
                prev_user_text = turn.text
            # carry tuple of mood results for slope calc
            # (store full result below by recomputing; keep simple)

        # recompute full mood results for scoring (keeps scorer stateless)
        moods_full = list(self._full_moods(segment, self._analyzer))
        f_series = tuple(m.f_t for m in moods_full)
        s_series = tuple(m.s_t for m in moods_full)

        return SegmentFeatures(
            moods=tuple(moods),
            f_series=f_series,
            s_series=s_series,
            tool_error_rate=tool_error_rate(segment),
            correction_rate=self._correction_rate(moods_full),
            explicit_thanks=any(m.domain_pos >= 0.34 for m in moods_full),
            explicit_anger=any(m.domain_neg >= 0.34 for m in moods_full),
        )

    @staticmethod
    def _full_moods(segment: Segment, analyzer: MoodAnalyzer):
        prev: str | None = None
        out = []
        for turn in segment.core_turns:
            if turn.role != "user":
                continue
            m = analyzer.analyze(turn, prev)
            if m.s_t or m.f_t or m.evidence:
                out.append(m)
            if m.domain_pos or m.domain_neg:
                prev = turn.text
        return out

    @staticmethod
    def _correction_rate(moods) -> float:
        if not moods:
            return 0.0
        n_corr = sum(1 for m in moods if "corr:" in ",".join(m.evidence) or m.retry_sim >= 0.5)
        return n_corr / len(moods)