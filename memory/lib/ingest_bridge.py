"""Bridges lib.ingest (chat-history parsing/scoring) into Crisp Engine's episode store.

lib.ingest is vendored from chinfer and stays store-agnostic — dependencies
flow inward only (see lib/ingest/pipeline.py's docstring), and its only
built-in emitter writes outcome-label JSONL, not episodes. This module is
the one place that knows about both sides: it drives the same
provider -> normalize -> segment -> feature -> score strategies
IngestPipeline composes, but turns each scored segment into a MemoryEpisode
and saves it through the real store, instead of a label file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .ingest.container import build_pipeline
from .ingest.types import IngestConfig, OutcomeScores, Segment, SessionIR
from .store import MemoryEpisode, MemoryStore


def _episode_from_segment(
    session: SessionIR, segment: Segment, scores: OutcomeScores
) -> MemoryEpisode:
    core = segment.core_turns
    lines = []
    for t in core:
        if t.text:
            lines.append(f"{t.role}: {t.text}")
        for tc in t.tools:
            suffix = f" (error: {tc.error})" if tc.error else ""
            lines.append(f"  tool: {tc.name}{suffix}")
    content = "\n".join(lines) or "(no text content in this segment)"

    ts = core[-1].ts if core else (
        segment.turns[-1].ts if segment.turns else datetime.now(timezone.utc).isoformat()
    )

    return MemoryEpisode(
        id=f"ingest_{session.provider}_{segment.segment_id}",
        session_id=session.session_id,
        timestamp=ts,
        layer=0,
        title=f"{session.provider} session segment ({scores.resolved})",
        content=content,
        source_type="chat",
        source_path=session.project_path,
        category="correction" if scores.correction_rate > 0 else "chat",
        importance=min(1.0, 0.5 + (0.3 if (scores.explicit_thanks or scores.explicit_anger) else 0.0)),
        frustration_score=scores.frustration,
        correction_applied=scores.correction_rate > 0,
        retry_count=int(scores.correction_rate * len(core)) if core else 0,
        tags=[session.provider, scores.resolved],
        context_snapshot={
            "confidence": scores.confidence,
            "evidence": list(scores.evidence),
            "segment_id": segment.segment_id,
        },
    )


def ingest_project_into_store(
    store: MemoryStore,
    project_path_filter: Optional[str] = None,
    config: Optional[IngestConfig] = None,
) -> Dict[str, Any]:
    """Run the real ingest pipeline against real transcripts and save episodes.

    Reuses IngestPipeline's own composed strategies (provider/normalizer/
    segmenter/feature_extractor/scorer) rather than reimplementing any of
    them — this file only owns the segment -> MemoryEpisode conversion and
    the save loop.
    """
    config = config or IngestConfig(project_path_filter=project_path_filter)
    pipeline = build_pipeline(config)

    saved = 0
    skipped = 0
    errors = []

    for name, provider in pipeline.providers.items():
        if config.providers and name not in config.providers:
            continue
        try:
            refs = list(provider.discover(config))
        except Exception as e:
            errors.append(f"discover[{name}]: {e}")
            continue

        for ref in refs:
            try:
                session = provider.parse(config, ref)
                session = pipeline.normalizer.normalize(session)
            except Exception as e:
                errors.append(f"parse[{ref}]: {e}")
                continue

            if not session.turns:
                continue
            if config.project_path_filter and config.project_path_filter not in (
                session.project_path or ""
            ):
                continue

            for segment in pipeline.segmenter.segment(session):
                features = pipeline.feature_extractor.extract(session, segment)
                scores = pipeline.scorer.score(session, segment, features)
                episode = _episode_from_segment(session, segment, scores)
                if store.save_episode(episode):
                    saved += 1
                else:
                    skipped += 1

    return {
        "saved": saved,
        "skipped": skipped,
        "errors": errors[:10],
        "error_count": len(errors),
    }
