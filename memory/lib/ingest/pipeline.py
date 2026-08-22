"""Composed ingest pipeline.

Dependencies in, never out (DIP): the pipeline owns only coordinator
logic and calls injected strategies. Swap providers / scorers /
segmenters / emitters by changing the container, not this class.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from .emitter import JsonlEmitter, label_from_segment
from .features import DefaultFeatureExtractor
from .normalize import DefaultNormalizer
from .scoring import RuleScorer
from .segment import SlidingWindowSegmenter
from .types import IngestConfig, SessionIR

log = logging.getLogger(__name__)


class IngestPipeline:
    """Composes providers → normalize → segment → features → score → emit."""

    def __init__(
        self,
        providers: dict[str, object],
        normalizer=None,
        segmenter=None,
        feature_extractor=None,
        scorer=None,
        emitter=None,
    ) -> None:
        self.providers = providers or {}
        self.normalizer = normalizer or DefaultNormalizer()
        self.segmenter = segmenter or SlidingWindowSegmenter(4000, 3)
        self.feature_extractor = feature_extractor or DefaultFeatureExtractor()
        self.scorer = scorer or RuleScorer()
        self.emitter = emitter or JsonlEmitter("out/labels.jsonl")

    # -- public API -------------------------------------------------------
    def run(self, config: IngestConfig) -> int:
        def labels():
            for session in self._iter_sessions(config):
                yield from self._session_labels(session)

        return self.emitter.emit(labels())

    def session_labels(self, session: SessionIR) -> list[dict]:
        return list(self._session_labels(session))

    # -- internals ---------------------------------------------------------
    def _iter_sessions(self, config: IngestConfig) -> Iterator[SessionIR]:
        for name, provider in self.providers.items():
            if config.providers and name not in config.providers:
                continue
            try:
                for ref in provider.discover(config):  # type: ignore[attr-defined]
                    try:
                        session = provider.parse(config, ref)  # type: ignore[attr-defined]
                        session = self.normalizer.normalize(session)
                    except Exception:
                        log.exception("parse failed: %s", ref)
                        continue
                    if config.project_path_filter and config.project_path_filter not in (
                        session.project_path or ""
                    ):
                        continue
                    yield session
            except Exception:
                log.exception("discover failed for provider: %s", name)

    def _session_labels(self, session: SessionIR) -> Iterator[dict]:
        if not session.turns:
            return
        segments = self.segmenter.segment(session)
        for seg in segments:
            features = self.feature_extractor.extract(session, seg)
            scores = self.scorer.score(session, seg, features)
            yield label_from_segment(session, seg, scores)