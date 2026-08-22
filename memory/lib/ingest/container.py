"""Composition root (DI). 

Builds a fully-wired IngestPipeline from IngestConfig. To swap a strategy
(LLM scorer, new provider, different segmenter) override the wiring here —
pipeline and consumers stay untouched.
"""

from __future__ import annotations

from .emitter import JsonlEmitter
from .features import DefaultFeatureExtractor
from .normalize import DefaultNormalizer
from .pipeline import IngestPipeline
from .providers import ClaudeJSONLProvider, OpenCodeSQLiteProvider
from .scoring import RuleScorer
from .segment import SlidingWindowSegmenter
from .types import IngestConfig


def build_pipeline(config: IngestConfig) -> IngestPipeline:
    providers: dict[str, object] = {
        ClaudeJSONLProvider.name: ClaudeJSONLProvider(),
        OpenCodeSQLiteProvider.name: OpenCodeSQLiteProvider(),
    }

    return IngestPipeline(
        providers=providers,
        normalizer=DefaultNormalizer(redact=config.redact),
        segmenter=SlidingWindowSegmenter(
            max_tokens=config.max_segment_tokens,
            overlap_user_turns=config.overlap_user_turns,
        ),
        feature_extractor=DefaultFeatureExtractor(),
        scorer=RuleScorer(),
        emitter=JsonlEmitter(config.out_path),
    )