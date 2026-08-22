"""Segment, feature, mood, scoring, emit, redaction, pipeline tests (ADR-0001 §17)."""

from __future__ import annotations

import json
from pathlib import Path

from lib.ingest.emitter import JsonlEmitter, label_from_segment
from lib.ingest.features import DefaultFeatureExtractor
from lib.ingest.mood import VaderDomainMoodAnalyzer, is_mood_eligible
from lib.ingest.normalize import DefaultNormalizer
from lib.ingest.pipeline import IngestPipeline
from lib.ingest.providers import ClaudeJSONLProvider
from lib.ingest.redact import RegexRedactor
from lib.ingest.scoring import RuleScorer, clamp, linreg_slope
from lib.ingest.segment import SlidingWindowSegmenter
from lib.ingest.types import IngestConfig, TurnIR


def _parse_claude(fixture_path: Path):
    cfg = IngestConfig(
        claude_projects_root=str(fixture_path.parent), providers=("claude_code",)
    )
    provider = ClaudeJSONLProvider()
    refs = list(provider.discover(cfg))
    return provider.parse(cfg, refs[0])


class TestSegmenter:
    def test_single_segment_when_small(self, claude_fixture):
        s = _parse_claude(claude_fixture)
        segs = SlidingWindowSegmenter(max_tokens=4000, overlap_user_turns=3).segment(s)
        assert len(segs) == 1
        assert len(segs[0].core_turns) == len(s.turns)
        assert all(not t.context_only for t in segs[0].turns)

    def test_overlap_marks_context_only_and_disjoint_cores(self, claude_fixture):
        s = _parse_claude(claude_fixture)
        segs = SlidingWindowSegmenter(max_tokens=12, overlap_user_turns=3).segment(s)
        assert len(segs) >= 2
        for seg in segs[1:]:
            assert any(t.context_only for t in seg.turns)
        # core ranges cover all turns exactly once
        core_ids = [t.turn_id for seg in segs for t in seg.core_turns]
        assert len(core_ids) == len(set(core_ids))
        assert set(core_ids) == {t.turn_id for t in s.turns}

    def test_exact_prefix_uniqueness(self, claude_fixture):
        s = _parse_claude(claude_fixture)
        segs = SlidingWindowSegmenter(max_tokens=12, overlap_user_turns=3).segment(s)
        ids = [seg.segment_id for seg in segs]
        assert len(ids) == len(set(ids))
        # deterministic: same session → same ids
        segs2 = SlidingWindowSegmenter(max_tokens=12, overlap_user_turns=3).segment(s)
        assert [seg.segment_id for seg in segs2] == ids

    def test_empty_session_no_segments(self):
        from lib.ingest.types import SessionIR

        s = SessionIR(provider="claude_code", session_id="x", project_path="", turns=())
        assert SlidingWindowSegmenter(4000, 3).segment(s) == []

    def test_oversized_turn_forced(self):
        turns = (TurnIR(turn_id="t0", provider_msg_id="m0", parent_turn_id=None,
                         role="user", ts="2000-01-01T00:00:00Z",
                         text="x" * 100, text_kind="human"),)
        s = _parse_claude_mock("s", turns)
        segs = SlidingWindowSegmenter(max_tokens=5, overlap_user_turns=0).segment(s)
        assert len(segs) == 1
        assert len(segs[0].core_turns) == 1


def _parse_claude_mock(session_id: str, turns):
    from lib.ingest.types import SessionIR

    return SessionIR(provider="claude_code", session_id=session_id,
                     project_path="/p", turns=tuple(turns))


class TestMood:
    def test_is_mood_eligible_filters_context_only(self, claude_fixture):
        s = _parse_claude(claude_fixture)
        user_human = [t for t in s.turns if t.role == "user"]
        assert all(is_mood_eligible(t) for t in user_human)

    def test_thanks_s_gt_f(self, claude_fixture):
        s = _parse_claude(claude_fixture)
        last = [t for t in s.turns if t.role == "user"][-1]
        m = VaderDomainMoodAnalyzer().analyze(last)
        assert m.s_t > m.f_t
        assert m.domain_pos > 0

    def test_still_broken_frustration(self, claude_fixture):
        s = _parse_claude(claude_fixture)
        broken = next(t for t in s.turns if "still broken" in t.text)
        m = VaderDomainMoodAnalyzer().analyze(broken)
        assert m.domain_neg > 0
        assert m.f_t > 0.2

    def test_context_only_zero_mood(self):
        t = TurnIR(turn_id="t", provider_msg_id="m", parent_turn_id=None, role="user",
                   ts="2000-01-01T00:00:00Z", text="bad noise", text_kind="human",
                   context_only=True)
        assert not is_mood_eligible(t)
        m = VaderDomainMoodAnalyzer().analyze(t)
        assert m.f_t == 0.0 and m.s_t == 0.0


class TestScoring:
    def test_increasing_anger_slope_positive(self):
        texts = ("this is broken", "still broken, wrong output",
                 "still broken! wrong! revert this")
        f = []
        for i, txt in enumerate(texts):
            t = TurnIR(turn_id=f"t{i}", provider_msg_id=str(i), parent_turn_id=None,
                       role="user", ts=f"2000-01-01T00:00:00:0{i}Z", text=txt,
                       text_kind="human")
            f.append(VaderDomainMoodAnalyzer().analyze(t).f_t)
        assert linreg_slope(f) > 0

    def test_explicit_thanks_resolves_yes(self, claude_fixture):
        s = _parse_claude(claude_fixture)
        segs = SlidingWindowSegmenter(4000, 3).segment(s)
        seg = segs[-1]
        feat = DefaultFeatureExtractor().extract(s, seg)
        res = RuleScorer().score(s, seg, feat)
        assert feat.explicit_thanks is True
        assert res.resolved == "yes"
        assert res.confidence >= 0.5

    def test_anger_without_work_resolves_no(self, claude_fixture):
        s = _parse_claude(claude_fixture)
        # strip work_landed AND the thanks turn so anger dominates
        from dataclasses import replace

        turns = tuple(
            t for t in s.turns if not (t.role == "user" and "thanks" in t.text)
        )
        s = replace(s, work_landed=False, todos_all_done=False, turns=turns)
        seg = SlidingWindowSegmenter(4000, 3).segment(s)[-1]
        feat = DefaultFeatureExtractor().extract(s, seg)
        res = RuleScorer().score(s, seg, feat)
        assert feat.explicit_anger is True
        assert res.resolved == "no"

    def test_no_evidence_unknown_low_confidence(self):
        t = TurnIR(turn_id="t0", provider_msg_id="m0", parent_turn_id=None, role="user",
                   ts="2000-01-01T00:00:00Z", text="hello", text_kind="human")
        s = _parse_claude_mock("s", (t,))
        seg = SlidingWindowSegmenter(4000, 3).segment(s)[-1]
        feat = DefaultFeatureExtractor().extract(s, seg)
        res = RuleScorer().score(s, seg, feat)
        assert res.resolved == "unknown"
        assert res.confidence <= 0.25

    def test_clamp(self):
        assert clamp(3.0, 0.0, 1.0) == 1.0
        assert clamp(-1.0, 0.0, 1.0) == 0.0


class TestQuality:
    def test_redaction(self):
        r = RegexRedactor()
        assert "[REDACTED]" in r.redact("token sk-abcdefghijklmnop1234")
        assert "[REDACTED]" in r.redact("key: AKIAIOSFODNN7EXAMPLE")
        assert "keep this" == r.redact("keep this")

    def test_normalize_collapses_and_sorts(self, claude_fixture):
        s = _parse_claude(claude_fixture)
        norm = DefaultNormalizer().normalize(s)
        assert norm is not s
        ts = [t.ts for t in norm.turns]
        assert ts == sorted(ts)

    def test_emit_schema(self, claude_fixture):
        s = _parse_claude(claude_fixture)
        seg = SlidingWindowSegmenter(4000, 3).segment(s)[-1]
        feat = DefaultFeatureExtractor().extract(s, seg)
        res = RuleScorer().score(s, seg, feat)
        label = label_from_segment(s, seg, res)
        assert label["schema"] == "metafold.outcome/v0"
        for k in ("session_id", "segment_id", "project_path", "model_hints",
                  "turn_ids", "scores", "routing_label_hint"):
            assert k in label
        sc = label["scores"]
        assert sc["resolved"] in ("yes", "no", "partial", "unknown")
        assert 0.0 <= sc["confidence"] <= 1.0
        assert label["turn_ids"] == [t.turn_id for t in seg.core_turns]


class TestPipelineEndToEnd:
    def test_run_writes_jsonl(self, claude_fixture, tmp_path):
        out = tmp_path / "labels.jsonl"
        pipeline = IngestPipeline(
            providers={"claude_code": ClaudeJSONLProvider()},
            emitter=JsonlEmitter(str(out)),
        )
        cfg = IngestConfig(
            out_path=str(out),
            claude_projects_root=str(claude_fixture.parent),
            providers=("claude_code",),
        )
        n = pipeline.run(cfg)
        assert n >= 1
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == n
        assert json.loads(lines[0])["schema"] == "metafold.outcome/v0"

    def test_project_filter_excludes(self, claude_fixture, tmp_path):
        out = tmp_path / "labels.jsonl"
        pipeline = IngestPipeline(
            providers={"claude_code": ClaudeJSONLProvider()},
            emitter=JsonlEmitter(str(out)),
        )
        cfg = IngestConfig(
            out_path=str(out),
            claude_projects_root=str(claude_fixture.parent),
            providers=("claude_code",),
            project_path_filter="NOMATCH_XYZ",
        )
        assert pipeline.run(cfg) == 0

    def test_filters_worklanded(self, claude_fixture, tmp_path):
        out = tmp_path / "labels.jsonl"
        pipeline = IngestPipeline(
            providers={"claude_code": ClaudeJSONLProvider()},
            emitter=JsonlEmitter(str(out)),
        )
        cfg = IngestConfig(
            out_path=str(out),
            claude_projects_root=str(claude_fixture.parent),
            providers=("claude_code",),
        )
        assert pipeline.run(cfg) >= 1