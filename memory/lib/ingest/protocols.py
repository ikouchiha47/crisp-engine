"""Provider / Strategy abstractions for the ingest pipeline.

Every stage of the pipeline depends on these Protocols (DIP), and the
concrete implementations are injected at composition time (via
`chinfer.container.build_pipeline`). New providers / scorers / segmenters
plug in without modifying the pipeline (OCP).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import Protocol, runtime_checkable

from .types import (
    IngestConfig,
    OutcomeScores,
    Segment,
    SegmentFeatures,
    SessionIR,
)


# ---------------------------------------------------------------------------
# SessionProvider — Strategy for sourcing + parsing raw transcripts
# ---------------------------------------------------------------------------
@runtime_checkable
class SessionProvider(Protocol):
    """Knows where sessions live and how to turn one into a SessionIR."""

    name: str

    def discover(self, config: IngestConfig) -> Iterator[str]:
        """Yield session identifiers (ids or paths) to parse."""
        ...

    def parse(self, config: IngestConfig, session_ref: str) -> SessionIR:
        """Parse a single session_ref into normalized IR."""
        ...


# ---------------------------------------------------------------------------
# Redactor — Strategy for scrubbing secrets from text
# ---------------------------------------------------------------------------
class Redactor(Protocol):
    def redact(self, text: str) -> str:
        ...


# ---------------------------------------------------------------------------
# TokenEstimator — Strategy for approximating token counts
# ---------------------------------------------------------------------------
class TokenEstimator(Protocol):
    def estimate(self, text: str) -> int:
        ...


# ---------------------------------------------------------------------------
# Segmenter — Strategy for cutting sessions into overlapping windows
# ---------------------------------------------------------------------------
class Segmenter(Protocol):
    def segment(self, session: SessionIR) -> Sequence[Segment]:
        ...


# ---------------------------------------------------------------------------
# FeatureExtractor — composes mood series + structural signals
# ---------------------------------------------------------------------------
class FeatureExtractor(Protocol):
    def extract(self, session: SessionIR, segment: Segment) -> SegmentFeatures:
        """Return feature bundle consumed by Scorer."""
        ...


# ---------------------------------------------------------------------------
# Scorer — Strategy for outcome scoring (rules v0, model later)
# ---------------------------------------------------------------------------
class Scorer(Protocol):
    def score(self, session: SessionIR, segment: Segment, features: SegmentFeatures) -> OutcomeScores:
        ...


# ---------------------------------------------------------------------------
# Emitter — Strategy for label output
# ---------------------------------------------------------------------------
class Emitter(Protocol):
    def emit(self, labels: Iterable[dict]) -> int:
        ...


# ---------------------------------------------------------------------------
# Pipeline — the composed orchestrator
# ---------------------------------------------------------------------------
class IngestPipeline(Protocol):
    def run(self, config: IngestConfig) -> int:
        ...