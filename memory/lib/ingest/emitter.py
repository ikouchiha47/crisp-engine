"""JSONL label emitter (ADR-0001 §13)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .scoring import routing_label_hint
from .types import OutcomeScores, Segment, SessionIR


def label_from_segment(
    session: SessionIR, segment: Segment, scores: OutcomeScores
) -> dict:
    core = segment.core_turns
    ts0 = core[0].ts if core else (segment.turns[0].ts if segment.turns else "")
    ts1 = core[-1].ts if core else (segment.turns[-1].ts if segment.turns else "")
    return {
        "schema": "metafold.outcome/v0",
        "provider": session.provider,
        "session_id": session.session_id,
        "segment_id": segment.segment_id,
        "project_path": session.project_path,
        "model_hints": list(session.model_hints),
        "time_range": [ts0, ts1],
        "turn_ids": [t.turn_id for t in core],
        "scores": {
            "satisfaction": scores.satisfaction,
            "frustration": scores.frustration,
            "frustration_slope": scores.frustration_slope,
            "resolved": scores.resolved,
            "explicit_thanks": scores.explicit_thanks,
            "explicit_anger": scores.explicit_anger,
            "correction_rate": scores.correction_rate,
            "confidence": scores.confidence,
            "evidence": list(scores.evidence),
        },
        "continuation": {
            "overlap_user_turns": segment.overlap_user_turns,
            "approx_tokens": segment.approx_tokens,
            "links": [
                {"kind": l.kind, "from_session": l.from_session, "to_session": l.to_session}
                for l in session.links
            ],
        },
        "routing_label_hint": routing_label_hint(scores),
    }


class JsonlEmitter:
    """Emitter strategy: append JSONL rows to out_path."""

    def __init__(self, out_path: str) -> None:
        self.out_path = Path(out_path)

    def emit(self, labels: Iterable[dict]) -> int:
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with self.out_path.open("w", encoding="utf-8") as fh:
            for row in labels:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                n += 1
        return n