"""Turn-level mood feature extraction (VADER + domain lexicon).

Implements the f_t / s_t composition from research §D/§E. Depends on
the MoodAnalyzer protocol so the whole scoring path can later swap in
an LLM judge without changing the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .lexicon import domain_scores
from .types import TurnIR

_VADER = SentimentIntensityAnalyzer()


@dataclass(frozen=True)
class MoodResult:
    """Turn-level mood scalar bundle (satisfaction/frustration components)."""

    turn_id: str
    s_t: float
    f_t: float
    vader_compound: float
    domain_pos: float
    domain_neg: float
    retry_sim: float
    evidence: tuple[str, ...]


class MoodAnalyzer(Protocol):
    """Analyzes a single user turn into mood features."""

    def analyze(self, turn: TurnIR, prev_user_text: str | None = None) -> MoodResult:
        ...


def caps_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters)


def retry_similarity(prev: str | None, cur: str) -> float:
    if not prev or not cur:
        return 0.0
    a = set(prev.lower().split())
    b = set(cur.lower().split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_mood_eligible(turn: TurnIR) -> bool:
    """Only human-origin, non-compact-core user turns get mood scores."""
    return (
        turn.role == "user"
        and turn.text_kind in ("human", "mixed")
        and not turn.context_only
        and bool(turn.text.strip())
    )


class VaderDomainMoodAnalyzer:
    """MoodAnalyzer strategy: VADER + domain lexicon + structural markers."""

    W_VADER = 0.35
    W_DOMAIN = 0.45
    W_STRUCT = 0.20

    def analyze(self, turn: TurnIR, prev_user_text: str | None = None) -> MoodResult:
        if not is_mood_eligible(turn):
            return MoodResult(
                turn_id=turn.turn_id, s_t=0.0, f_t=0.0, vader_compound=0.0,
                domain_pos=0.0, domain_neg=0.0, retry_sim=0.0, evidence=(),
            )
        text = turn.text
        v = _VADER.polarity_scores(text)
        compound = v["compound"]
        dpos, dneg, dcorr, ev = domain_scores(text)
        rsim = retry_similarity(prev_user_text, text)

        bangs = text.count("!") + text.count("?")
        caps = caps_ratio(text)
        struct_neg = min(
            1.0,
            0.4 * dcorr + 0.2 * (1 if bangs >= 3 else 0) + 0.2 * caps,
        )
        struct_pos = 0.0

        s_t = self.W_VADER * max(compound, 0.0) + self.W_DOMAIN * dpos + 0.15 * struct_pos
        f_t = self.W_VADER * max(-compound, 0.0) + self.W_DOMAIN * dneg + self.W_STRUCT * struct_neg

        evidence = list(ev)
        if compound != 0.0:
            evidence.append(f"vader={compound:.2f}")
        return MoodResult(
            turn_id=turn.turn_id,
            s_t=max(0.0, min(1.0, s_t)),
            f_t=max(0.0, min(1.0, f_t)),
            vader_compound=compound,
            domain_pos=dpos,
            domain_neg=dneg,
            retry_sim=rsim,
            evidence=tuple(evidence),
        )