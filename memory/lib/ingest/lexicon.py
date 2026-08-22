"""Domain lexicon for agent-prose sentiment (ADR-0001 §10).

Compiled once at import. The MoodAnalyzer combines these signals with
VADER (see research/session-outcome-signals.md §E).
"""

from __future__ import annotations

import re

POS_PHRASES: tuple[str, ...] = (
    r"\bthanks\b",
    r"\bthank you\b",
    r"\bperfect\b",
    r"\blgtm\b",
    r"\bship it\b",
    r"\bworks\b",
    r"\bworking now\b",
    r"\bthat fixed\b",
    r"\bexactly\b",
    r"\bnice\b",
    r"\bgreat\b",
    r"\bresolved\b",
)

NEG_PHRASES: tuple[str, ...] = (
    r"\bstill broken\b",
    r"\bstill wrong\b",
    r"\bdoesn't work\b",
    r"\bdoes not work\b",
    r"\bnot what i\b",
    r"\byou didn't\b",
    r"\brevert\b",
    r"\bundo that\b",
    r"\buseless\b",
    r"\bbroken\b",
    r"\bwrong\b",
    r"\bagain\b",
    r"\bhow many times\b",
    r"\bi said\b",
    r"\bstop\b",
    r"\bfixing\b",
)

CORRECTION_PHRASES: tuple[str, ...] = (
    r"\bno,\b",
    r"\binstead\b",
    r"\buse .+ not\b",
    r"\bnot that\b",
    r"\btry again\b",
    r"\bdo it again\b",
)

_POS_RE = tuple(re.compile(p) for p in POS_PHRASES)
_NEG_RE = tuple(re.compile(p) for p in NEG_PHRASES)
_CORR_RE = tuple(re.compile(p) for p in CORRECTION_PHRASES)


def domain_scores(text: str) -> tuple[float, float, float, list[str]]:
    """Return (pos, neg, correction, evidence) for a single user turn."""
    if not text:
        return 0.0, 0.0, 0.0, []
    t = text.lower()
    pos_hits = [p for p in _POS_RE if p.search(t)]
    neg_hits = [p for p in _NEG_RE if p.search(t)]
    corr_hits = [p for p in _CORR_RE if p.search(t)]

    pos = min(1.0, 0.34 * len(pos_hits))
    neg = min(1.0, 0.34 * len(neg_hits))
    corr = min(1.0, 0.5 * len(corr_hits))

    evidence = []
    if pos_hits:
        evidence.append(f"pos:{len(pos_hits)}")
    if neg_hits:
        evidence.append(f"neg:{len(neg_hits)}")
    if corr_hits:
        evidence.append(f"corr:{len(corr_hits)}")
    return pos, neg, corr, evidence