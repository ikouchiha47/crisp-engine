"""chinfer.ingest — session outcome label factory (ADR-0001)."""

from .container import build_pipeline
from .pipeline import IngestPipeline
from .types import (
    IngestConfig,
    OutcomeScores,
    Segment,
    SegmentFeatures,
    SessionIR,
    TurnIR,
)

__all__ = [
    "IngestConfig",
    "IngestPipeline",
    "OutcomeScores",
    "Segment",
    "SegmentFeatures",
    "SessionIR",
    "TurnIR",
    "build_pipeline",
]