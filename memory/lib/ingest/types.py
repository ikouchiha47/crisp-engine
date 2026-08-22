"""Core immutable IR datatypes shared across the ingest pipeline.

Single source of truth for the normalized session representation
(see ADR-0001 §2). Keep these frozen dataclasses; producers build them,
consumers read them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Provider = Literal["claude_code", "opencode"]
Role = Literal["user", "assistant", "tool", "system"]
TextKind = Literal["human", "meta", "command", "tool", "reasoning", "mixed"]
Resolved = Literal["yes", "no", "partial", "unknown"]


@dataclass(frozen=True)
class ToolCallIR:
    """A single tool invocation captured from a transcript."""

    name: str
    ok: bool | None = None
    error: str | None = None


@dataclass(frozen=True)
class TurnIR:
    """One normalized dialogue turn."""

    turn_id: str
    provider_msg_id: str
    parent_turn_id: str | None
    role: Role
    ts: str
    text: str
    text_kind: TextKind
    tools: tuple[ToolCallIR, ...] = ()
    model: str | None = None
    usage_input: int | None = None
    usage_output: int | None = None
    usage_cache_read: int | None = None
    sidechain: bool = False
    context_only: bool = False


@dataclass(frozen=True)
class ContinuationLink:
    """Link to another session (parent, subagent, compaction, fork…)."""

    kind: Literal["parent_session", "compact_summary", "resume", "fork", "subagent"]
    from_session: str
    to_session: str | None = None
    overlap_turn_ids: tuple[str, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class SessionIR:
    """Normalized session produced by a SessionProvider."""

    provider: Provider
    session_id: str
    project_path: str
    turns: tuple[TurnIR, ...]
    parent_session_id: str | None = None
    title: str | None = None
    started_at: str = ""
    ended_at: str = ""
    model_hints: tuple[str, ...] = ()
    agent_hints: tuple[str, ...] = ()
    side_threads: tuple[tuple[TurnIR, ...], ...] = ()
    links: tuple[ContinuationLink, ...] = ()
    n_user: int = 0
    n_assistant: int = 0
    n_tool_calls: int = 0
    n_tool_errors: int = 0
    total_cost: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    work_landed: bool = False
    todos_all_done: bool | None = None
    summary_additions: int | None = None
    summary_deletions: int | None = None


@dataclass(frozen=True)
class TurnMood:
    """Per-user-turn mood scalars produced by FeatureExtractor."""

    turn_id: str
    s_t: float
    f_t: float
    vader_compound: float = 0.0
    domain_pos: float = 0.0
    domain_neg: float = 0.0
    retry_sim: float = 0.0
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutcomeScores:
    satisfaction: float
    frustration: float
    frustration_slope: float
    resolved: Resolved
    explicit_thanks: bool = False
    explicit_anger: bool = False
    correction_rate: float = 0.0
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Segment:
    """A token-budgeted window over part of a session with overlap prefix."""

    session_id: str
    segment_id: str
    turns: tuple[TurnIR, ...]
    core_start_idx: int
    approx_tokens: int
    overlap_user_turns: int

    @property
    def core_turns(self) -> tuple[TurnIR, ...]:
        return tuple(t for t in self.turns if not t.context_only)


@dataclass(frozen=True)
class SegmentFeatures:
    moods: tuple = ()
    f_series: tuple[float, ...] = ()
    s_series: tuple[float, ...] = ()
    tool_error_rate: float = 0.0
    correction_rate: float = 0.0
    explicit_thanks: bool = False
    explicit_anger: bool = False


@dataclass
class IngestConfig:
    """Composition-root configuration assembled into a pipeline."""

    out_path: str = "out/labels.jsonl"
    claude_projects_root: str | None = None
    opencode_db: str | None = None
    providers: tuple[Provider, ...] = ("claude_code", "opencode")
    max_segment_tokens: int = 4000
    overlap_user_turns: int = 3
    project_path_filter: str | None = None
    redact: bool = True
    read_only_assert: bool = True