"""LLM-backed narrative generation for L1/L2/L3 consolidation, via DSPy
typed Signatures — not hand-rolled JSON prompts/parsing.

Separate from lib/affect.py (extraction: pulls preferences/corrections out
of a transcript) — this module's job is summarization: turn a batch of
already-real content into a real narrative. Same SRP split as the
lib/hooks/* collaborators.

Every function here degrades to None on any failure (no LM configured, LM
unreachable, dspy raising) — callers must fall back to their existing
template logic, never break, never silently produce garbage.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import dspy

from .generation_log import log_generation

_PIPELINE_CONTEXT = """You are part of Crisp, a persistent memory system for an AI coding agent (Claude Code).

MEMORY TYPES — you are writing EPISODIC memory specifically:
  - Episodic: what happened, in a specific session, in order (this pipeline: L0-L3 below). Ties events to when/where they occurred.
  - Semantic: durable facts, true independent of any one event ("this user always uses uv", "imports go at the top"). Handled elsewhere (lib/affect.py distills these into standing preference/correction facts, injected every session regardless of topic).
  - Procedural: learned behavioral rules from recurring patterns, confidence-scored and reinforced over time until promoted into an actual emitted skill. Handled elsewhere (lib/instincts).
  Your job is the episodic layer: WHAT HAPPENED and WHAT WAS LEARNED FROM IT, not standing facts or behavioral rules — those get extracted separately.

SYSTEM: MemGPT-style hierarchical paging, within the episodic layer. Each layer consolidates the one below it; none of it is regenerated from scratch once written, only extended.

  L0 — raw episode. One captured unit: a conversation chunk, a correction, a git commit, a frustration signal. Written as it happens, never summarized itself.
  L1 — session summary. Compresses one session's L0 episodes into what that session actually did and learned.
  L2 — topic cluster. Groups L1 summaries by embedding similarity (not just chronology) — sessions that are actually about the same thing, even if weeks apart — and synthesizes what's recurring or evolving across them.
  L3 — life arc. Built from L2 clusters, taken chronologically. The permanent, top-level "what has this codebase's history with this agent actually been" — the first thing a brand-new session reads.

You are generating exactly ONE of these layers per call — you'll be told which below.

HOW YOUR OUTPUT GETS FOUND LATER: a future session (same agent, same codebase, no memory of this one) retrieves episodes by:
  - embedding similarity to what it's currently working on
  - Ebbinghaus decay (recency-weighted; episodes accessed often decay slower, so being useful is self-reinforcing)
  - explicit contradicts / corrected_by links, which boost an episode's rank when it's relevant to a live contradiction

WHAT THIS MEANS FOR YOU: vague or generic text doesn't embed distinctly, doesn't get accessed, and decays out fast — functionally invisible. Specific text (real names, real decisions, real outcomes) is the only kind that survives this pipeline.
"""


_L1_INSTRUCTIONS = (
    "You are generating an L1 session summary — one level up from raw "
    "episodes, the first compression step. This summary will later be "
    "re-read by a future session (possibly weeks from now) that has no "
    "other memory of this one, and may itself get folded into an L2 "
    "topic cluster. If it's vague, that future session gets nothing "
    "usable.\n\n"
    "Capture ALL of these, not just the first:\n"
    "1. WHAT WAS DONE — the technical outcome: what was built, fixed, "
    "found, decided (file names, real numbers, real decisions).\n"
    "2. HOW THE USER WANTS WORK DONE GOING FORWARD — process/methodology "
    "directives and demands, even ones stated once, in anger, or as an "
    "aside mid-conversation (e.g. \"use TDD/BDD instead of just claiming "
    "it works\", \"stop doing X\", \"always verify Y before Z\"). These "
    "are frequently the most consequential thing in the session and the "
    "easiest to drop, because they're wrapped around technical work "
    "rather than being the technical work itself — a future session "
    "needs them just as much as it needs to know what code changed.\n"
    "3. ITEMIZED CONTENT STAYS ITEMIZED — if the raw episodes already "
    "contain a structured list (numbered findings, a bug audit, a table, "
    "a checklist, a task DAG), do not compress it into a vague paragraph "
    "like 'several issues were found.' List each item with its specific "
    "detail (file, line, what's wrong) preserved. A short paragraph is "
    "the right shape for prose narrative; it is the wrong shape for "
    "content that was already a structured list — forcing it into prose "
    "destroys the only thing that made it useful. Length should scale "
    "with how much specific, itemized content is actually present.\n"
    "4. DECISIONS THAT GOT REVERSED — if the user (or the agent) decided "
    "something ('let's deprecate this', 'delete the codebase and start "
    "over') and then later reversed or contradicted that decision within "
    "the same session, state both the original call and the reversal, "
    "with the reason if one was given. A future session needs to know a "
    "decision was made AND unmade, not just land on whichever one came "
    "last as if it were the only one.\n"
    "5. STRONG SENTIMENT, NAMED TO ITS TARGET — this covers more than "
    "overt anger. Include resigned dismissal and giving-up-on-something "
    "language too (\"fuck it then\", \"no point continuing this\", "
    "\"there's no differentiator\") — these read as quieter than a raged "
    "line but carry the same weight: they mark exactly where the user's "
    "patience or belief in an approach ran out, often right before a real "
    "decision. Note what the sentiment was actually about (e.g. "
    "frustration at research quality, resignation about a whole "
    "approach's viability) — not as a vague 'user was unhappy,' and not "
    "sanitized away because the tone was flat/resigned instead of loud.\n"
    "6. OPEN / UNDELIVERED — things the user asked for that were not "
    "delivered by the end of this session, or were only claimed done "
    "without the episodes showing real verification. One short bullet "
    "each. Do not mark something as delivered unless the episodes "
    "actually show it working, not just being attempted.\n\n"
    "WRONG-LAYER / WRONG-ORDER: if the user says the agent is building "
    "the wrong component, or building things in the wrong order (e.g. "
    "\"why retriever before code indexer?\", \"why UI before the memory "
    "actually works?\"), that is a priority/sequencing directive — file "
    "it under category 2, explicitly, not as generic dissatisfaction.\n\n"
    "SWEARING NEVER REDUCES WHAT YOU CAPTURE: profanity, insults, and "
    "anger frequently carry the highest-value process rules and "
    "reversals in real sessions — they do not make the content less "
    "real or less worth capturing. Never quote slurs or purely abusive "
    "language back into the narrative, but never let tone cause you to "
    "drop, soften, or omit the actual directive/rule/decision underneath "
    "it either. State the rule cleanly (e.g. \"user rejected claims of "
    "correctness without test evidence\"), not the insult."
)


class NarrateL1(dspy.Signature):
    __doc__ = _PIPELINE_CONTEXT + _L1_INSTRUCTIONS
    episodes: str = dspy.InputField(
        desc="Raw episode contents for one session — conversation turns, "
        "corrections, frustration signals, git commits — joined together."
    )
    narrative: str = dspy.OutputField(
        desc="Must cover every category from the instructions that is "
        "actually present in the episodes given: technical outcome, "
        "process/priority directives (incl. wrong-layer/wrong-order), "
        "itemized lists kept itemized, reversed decisions, sentiment "
        "named to its target (incl. resigned/quiet registers), and open/ "
        "undelivered asks. Technical outcome alone is a FAIL if any of "
        "the other categories were present in the input and got dropped. "
        "at least 3 sentences for ordinary session content, more whenever "
        "itemized list, when the source content itself was a structured "
        "list — length follows content density, not a fixed target. "
        "Never generic filler like 'various improvements were made'. "
        "Never quote slurs or purely abusive language — state the "
        "underlying rule/directive/decision cleanly instead."
    )


class JudgeL1Completeness(dspy.Signature):
    """Score how completely a generated L1 narrative captured what its
    source episodes actually contain, across six categories: (1) real
    specifics (files/numbers/decisions), (2) any process/methodology/
    priority-order directive from the user, (3) itemized content (a
    structured list in the source) preserved as a list rather than
    collapsed into vague prose, (4) any decision that got reversed
    within the episodes, (5) any strong user sentiment — overt anger AND
    quieter resigned/give-up language ("fuck it then", "no point
    continuing") — and what it targeted, (6) anything the user asked for
    that the episodes show as undelivered or only claimed done without
    evidence. Confirmed reliability gap: the same narrate call, run
    twice on similar real input, sometimes catches these and sometimes
    silently drops them — this judge exists to let dspy.Refine detect
    and retry the drop, not to grade writing style."""
    episodes: str = dspy.InputField()
    narrative: str = dspy.InputField()
    score: float = dspy.OutputField(
        desc="0.0-1.0. For each of the six categories above that is "
        "actually present in episodes, check whether narrative captured "
        "it. score = fraction captured (categories absent from episodes "
        "don't count against the score). 1.0 only if nothing present was "
        "missed."
    )


def _l1_completeness_reward(inputs: dict, pred) -> float:
    try:
        judge = dspy.Predict(JudgeL1Completeness)
        out = judge(episodes=inputs["episodes"], narrative=pred.narrative)
        return max(0.0, min(1.0, float(out.score)))
    except Exception:
        return 0.0


_L2_INSTRUCTIONS = (
    "You are generating an L2 topic cluster — a mid-level "
    "compression synthesizing several L1 session summaries that were "
    "grouped together (by semantic similarity, not just chronology). "
    "This becomes the input a future L3 identity arc gets built from, "
    "and may be surfaced directly to a future session as 'here's what's "
    "been recurring in this codebase.' A vague cluster is worse than "
    "none.\n\n"
    "The sessions grouped together are often NOT about the same "
    "technical work — don't force a fake shared technical theme if "
    "there isn't one. The real recurring thread across sessions is "
    "frequently methodological, not technical: confirmed directly "
    "against real transcripts, the same trust/process pattern showed "
    "up independently across genuinely different sessions (one "
    "auditing storage bugs, another building a distillation pipeline) "
    "far more reliably than any shared technical topic did. Check for "
    "ALL of these across the L1s given, not just a technical label:\n"
    "1. TECHNICAL THEME — only if the sessions genuinely share concrete "
    "technical ground (same files/subsystem/feature area).\n"
    "2. RECURRING PROCESS RULES — a methodology or verification demand "
    "that shows up in more than one session (e.g. 'don't trust your own "
    "analysis, verify against code/db/jsonl directly', 'prove it with "
    "real tests, not claims').\n"
    "3. RECURRING AGENT FAILURES — the same kind of mistake or shortcut "
    "recurring across sessions (e.g. claiming something works without "
    "verifying it, shipping template/placeholder content as real, "
    "building the wrong layer/component first).\n"
    "4. OPEN OBLIGATIONS — something asked for in an earlier session "
    "that's still unresolved or unmentioned as done in a later one.\n"
    "If a pattern from 2-4 is present in even two of the L1s given, "
    "name it explicitly — that recurrence is the whole point of this "
    "layer existing, and it's real signal even when the sessions are "
    "about unrelated technical work."
)


class NarrateL2(dspy.Signature):
    __doc__ = _PIPELINE_CONTEXT + _L2_INSTRUCTIONS
    l1_summaries: str = dspy.InputField(desc="Several L1 session summaries, joined together.")
    topic: str = dspy.OutputField(
        desc="Short name (2-5 words) for what these sessions concretely "
        "have in common — technical OR methodological/process, whichever "
        "is the stronger real pattern. Never force a fake shared "
        "technical label when the real recurrence is methodological."
    )
    synthesis: str = dspy.OutputField(
        desc="At least 3 sentences covering whichever of TECHNICAL THEME / "
        "RECURRING PROCESS RULES / RECURRING AGENT FAILURES / OPEN "
        "OBLIGATIONS are actually present across the L1s given — real "
        "patterns tied to the actual content, not generic filler. A "
        "recurring process rule or agent failure must not be omitted "
        "just because the sessions don't share a technical topic."
    )


_L3_INSTRUCTIONS = (
    "You are generating an L3 life arc — the permanent, top-level "
    "identity summary for this project's memory: the thing a "
    "brand-new session reads first to understand 'what has this "
    "codebase's history with this agent actually been.' It is never "
    "regenerated from scratch, only extended, so getting it generic "
    "here is a standing failure, not a one-off.\n\n"
    "This is identity, not a changelog — it should read like 'here's "
    "the working relationship and standing rules for anyone picking "
    "this up,' not 'here's a list of things that got built.' Pull from "
    "the L2 clusters given whichever of these are actually supported "
    "by their content:\n"
    "- STANDING PROCESS LAWS — methodology rules that recurred across "
    "clusters (e.g. evidence/TDD required over claims, behavioural "
    "acceptance criteria, real e2e over test theatre, config-driven over "
    "hardcoded, fix root cause not symptoms).\n"
    "- TRUST RULES — what the user has established about verifying the "
    "agent's own work (e.g. verify against code/db/transcripts directly, "
    "don't trust the agent's narrative alone).\n"
    "- PRODUCT PILLARS — what this project keeps prioritizing across "
    "sessions, if a real pattern (not a one-off feature).\n"
    "- RECURRING FAILURE MODES TO AVOID — mistakes the agent has made "
    "more than once across these clusters, stated as a rule for the "
    "future, not as a complaint about the past.\n"
    "Never a generic category label like 'Decision-making patterns' or "
    "'Personal Development' unless the content genuinely, specifically "
    "supports exactly that."
)


class NarrateL3(dspy.Signature):
    __doc__ = _PIPELINE_CONTEXT + _L3_INSTRUCTIONS
    l2_clusters: str = dspy.InputField(desc="Several L2 topic clusters, joined together.")
    arc_name: str = dspy.OutputField(
        desc="Short name (2-4 words) reflecting the actual dominant theme "
        "across these clusters — not a generic label like 'Personal "
        "Development' unless the content genuinely, specifically supports it."
    )
    meta_lessons: List[str] = dspy.OutputField(
        desc="At least 3 lessons specific to the actual content given, covering "
        "whichever of standing process laws / trust rules / product "
        "pillars / recurring failure modes are actually supported by the "
        "clusters — never generic categories like 'Decision-making "
        "patterns'."
    )


_TITLE_INSTRUCTIONS = (
    "You are titling one raw conversation-transcript chunk (an L0 "
    "episode — the most granular capture level) so it's identifiable "
    "in a list alongside dozens of other episodes, without opening it.\n\n"
    "Priority for what the title names, in order:\n"
    "1. A process/methodology demand or ban, if that's what the chunk is "
    "actually about (e.g. \"TDD required before claims\").\n"
    "2. Else a reversal or correction, if that's the main content.\n"
    "3. Else the concrete technical action/decision.\n"
    "Never title a chunk 'Conversation', 'User feedback', 'Discussion', "
    "'Frustration', or 'Chat' — those describe the format, not the "
    "content, and are true of every chunk so they identify nothing."
)


class NarrateTitle(dspy.Signature):
    __doc__ = _PIPELINE_CONTEXT + _TITLE_INSTRUCTIONS
    context: str = dspy.InputField(desc="One conversation-transcript chunk.")
    title: str = dspy.OutputField(
        desc="4-8 words describing what this specific chunk was actually "
        "about, per the priority order in the instructions — the real "
        "topic/decision/action/demand, not a generic label like "
        "'Conversation' or a restatement of who was speaking. Examples of "
        "good titles: 'TDD required before claims', 'Skip OpenCode for "
        "now', 'Imports go at file top'. Examples of bad titles: 'Angry "
        "user message', 'Session notes', 'User feedback'."
    )


def _joined(texts: List[str], limit: int = 8000) -> str:
    joined = "\n---\n".join(t.strip() for t in texts if t.strip())
    return joined[:limit]


def narrate_title(lm: Optional[dspy.LM], context: str) -> Optional[str]:
    """Real LLM-generated title for one conversation-transcript chunk, or
    None if lm unavailable/failed. No heuristic fallback (e.g. "first line
    of text") — that was tried and rejected: it just truncates real content
    into a fake-looking label instead of describing it."""
    if lm is None or not context.strip():
        return None
    try:
        with dspy.context(lm=lm):
            out = dspy.Predict(NarrateTitle)(context=context.strip()[:8000])
        log_generation("title", {"title": out.title})
        title = str(out.title).strip()
        return title or None
    except Exception:
        return None


def narrate_l1(lm: Optional[dspy.LM], episode_texts: List[str]) -> Optional[str]:
    """Session narrative, or None if lm unavailable/failed.

    Wrapped in dspy.Refine (N=3, LLM-judge reward via
    JudgeL1Completeness): confirmed directly this session that a single
    narrate_l1 call is unreliable on real input — the exact same class of
    content (a process directive, a reversed decision, real anger) got
    captured on one real session and silently dropped on another,
    same-shaped one. Refine reruns up to 3x and keeps whichever attempt the
    judge scores highest, rather than accepting whatever the first sample
    happened to produce. Real cost: up to 3x the LM calls per L1 (2 of
    which are the small `JudgeL1Completeness` judge, not full narrate
    calls) — a deliberate tradeoff for reliability, not free.
    """
    joined = _joined(episode_texts)
    if lm is None or not joined:
        return None
    try:
        with dspy.context(lm=lm):
            refined = dspy.Refine(
                module=dspy.Predict(NarrateL1),
                N=3,
                reward_fn=_l1_completeness_reward,
                threshold=0.85,
            )
            out = refined(episodes=joined)
        log_generation("l1", {"narrative": out.narrative})
        narrative = str(out.narrative).strip()
        return narrative or None
    except Exception:
        return None


def narrate_l2(lm: Optional[dspy.LM], l1_texts: List[str]) -> Optional[Tuple[str, str]]:
    """Returns (topic_name, synthesis_paragraph), or None if unavailable/failed."""
    joined = _joined(l1_texts)
    if lm is None or not joined:
        return None
    try:
        with dspy.context(lm=lm):
            out = dspy.Predict(NarrateL2)(l1_summaries=joined)
        log_generation("l2", {"topic": out.topic, "synthesis": out.synthesis})
        topic = str(out.topic).strip()
        synthesis = str(out.synthesis).strip()
        if not topic or not synthesis:
            return None
        return (topic, synthesis)
    except Exception:
        return None


def narrate_l3(lm: Optional[dspy.LM], l2_texts: List[str]) -> Optional[Tuple[str, List[str]]]:
    """Returns (arc_name, [meta_lesson, ...]), or None if unavailable/failed."""
    joined = _joined(l2_texts)
    if lm is None or not joined:
        return None
    try:
        with dspy.context(lm=lm):
            out = dspy.Predict(NarrateL3)(l2_clusters=joined)
        log_generation("l3", {"arc_name": out.arc_name, "meta_lessons": list(out.meta_lessons)})
        name = str(out.arc_name).strip()
        lessons = [str(l).strip() for l in out.meta_lessons if str(l).strip()]
        if not name or not lessons:
            return None
        return (name, lessons)
    except Exception:
        return None
