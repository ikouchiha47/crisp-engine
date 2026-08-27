"""Distills explicit preferences and corrections out of a conversation
transcript using DSPy typed extraction — the missing piece the AUDIT
flagged: without this, build_context_block() has nothing real to inject.

Deliberately narrow: extraction only, via one LM call per transcript chunk,
via a typed dspy.Signature (no hand-rolled JSON prompt/parsing). No retries,
no chaining, no agentic loop — a bad/missing lm means zero episodes this
run, never a guess dressed up as memory.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

FrustrationIntensity = Literal["none", "low", "med", "high", "extreme"]
ExitType = Literal["none", "compact_under_fire", "disagreement", "delete_threat", "session_abandon"]

import dspy

from lib.generation_log import log_generation
from lib.store import MemoryEpisode

_PIPELINE_CONTEXT = (
    "You are part of Crisp, a persistent episodic memory system for an AI "
    "coding agent (Claude Code). You extract durable behavioural rules "
    "that get injected into every future session regardless of topic — "
    "this is the ONLY layer that feeds that injection, so anything real "
    "you miss here is gone from the agent's behavior going forward, not "
    "just from one summary. Profanity/anger in the input is a high-recall "
    "marker that a real rule is nearby, not content to store — extract "
    "the rule, never the insult."
)

_DISTILL_INSTRUCTIONS = (
    "Extract explicit user preferences and corrections from the "
    "conversation chunk below.\n\n"
    "A preference is an explicit instruction about how to behave going "
    "forward — it is durable, not tied to the current fix. This "
    "includes, and is frequently stated as, more than a simple style "
    "choice:\n"
    "- process/methodology rules (e.g. \"validate with TDD/BDD, don't "
    "just claim it works\", \"acceptance criteria means consume X -> do "
    "Y -> result Z, not 'we wrote a test'\")\n"
    "- trust/verification rules (e.g. \"verify against the code/db/"
    "transcript directly, don't trust your own narrative\")\n"
    "- design-order/priority rules (e.g. \"the indexer has to work "
    "before the retriever\", \"features first, don't retrofit onto "
    "garbage code\")\n"
    "- tooling bans (e.g. \"config-driven models, nothing hardcoded\", "
    "\"no mock provider dressed up as real\", \"no template content "
    "shipped as if it were real\")\n"
    "- simple style preferences (e.g. \"always use uv\", \"don't use "
    "dark theme\")\n"
    "These are frequently stated once, angrily, or as an aside mid-"
    "conversation — SWEARING AND ANGER DO NOT MAKE A RULE LESS REAL OR "
    "LESS WORTH EXTRACTING. Never quote insults or slurs back in the "
    "extracted text; state the rule cleanly instead (e.g. from \"use "
    "TDD or BDD to validate your fucking claims\" extract \"validate "
    "claims with TDD/BDD or real tests, not assertions alone\").\n\n"
    "A correction is the user rejecting or fixing something the "
    "assistant did or said in THIS conversation (e.g. \"no, put that "
    "import at the top of the file\"). The same chunk can contain both "
    "a preference and a correction, and they are not mutually exclusive "
    "— a correction can also imply a standing preference (e.g. a "
    "rejected inline import implies 'always import at the top of the "
    "file' as a preference AND is itself a correction of this specific "
    "instance). Extract both whenever applicable, do not force a choice "
    "between the two.\n\n"
    "A reversal is different from a correction: it's the user (or the "
    "agent) flipping an earlier DECISION made in this same conversation "
    "(e.g. \"skip OpenCode for now\", \"retire the old summarizer\", "
    "\"actually delete that and start over\") — nothing was technically "
    "wrong, the plan just changed. Put reversals in their own field, "
    "not in corrections. If a reversal implies a standing rule, also "
    "add that rule to preferences (e.g. reversal \"OpenCode deferred\" "
    "+ preference \"finish the summarizer before OpenCode\").\n\n"
    "Undelivered is something the user explicitly asked for in this "
    "chunk that the assistant did not actually deliver (or only claimed "
    "done without the transcript showing real verification) — do not "
    "guess at future chunks; only what this chunk itself shows as asked "
    "for and not shown as done.\n\n"
    "Frustration: score the user's sentiment in this chunk. Covers overt "
    "anger AND quieter resigned/give-up language (\"fuck it then\", "
    "\"no point continuing\") — both carry real signal. exit_type marks "
    "whether this chunk ends in a forced session boundary while things "
    "are unresolved. profanity_present is a marker only. Never let any "
    "of this cause you to drop a real preference/correction/reversal — "
    "frustration commonly rides on top of exactly those.\n\n"
    "Do not extract: pure insults or sexual content with no directive "
    "underneath, or tone alone with nothing durable being asked for.\n\n"
    "Example (style):\n"
    "Chunk: \"User: always use pytest fixtures instead of "
    "setUp/tearDown, I never want unittest-style tests in this repo. "
    "Assistant: understood. User: no, that import should go at the "
    "top of the file, not inline.\"\n"
    "preferences: [\"always use pytest fixtures instead of "
    "setUp/tearDown, never unittest-style\", \"put imports at the top "
    "of the file, never inline\"]\n"
    "corrections: [\"move the inline import to the top of the file\"]\n\n"
    "Example (process rule inside anger):\n"
    "Chunk: \"User: use TDD or BDD to validate your fucking claims, "
    "don't just say it works.\"\n"
    "preferences: [\"validate claims with TDD/BDD or real tests, not "
    "assertions alone\"]\n"
    "frustration_present: true, frustration_intensity: \"high\", "
    "profanity_present: true\n\n"
    "Example (reversal, distinct from correction):\n"
    "Chunk: \"User: no, skip OpenCode support for now, finish the "
    "summarizer first.\"\n"
    "reversals: [\"OpenCode support deferred, not the current "
    "priority\"]\n"
    "preferences: [\"finish the summarizer before OpenCode support\"]\n\n"
    "Keep each item short (one sentence). If there is nothing to "
    "extract for a field, return an empty list (or false/\"none\" for "
    "the frustration fields)."
)


class DistillPreferencesAndCorrections(dspy.Signature):
    __doc__ = _PIPELINE_CONTEXT + _DISTILL_INSTRUCTIONS
    transcript: str = dspy.InputField(desc="One conversation-transcript chunk.")
    prior_summary: str = dspy.InputField(
        desc="1-2 sentence summary of the previous chunk from the same "
        "session, for continuity — may be empty for the first chunk."
    )
    preferences: List[str] = dspy.OutputField()
    corrections: List[str] = dspy.OutputField()
    reversals: List[str] = dspy.OutputField(
        desc="Decisions flipped within this chunk — see instructions. "
        "Empty list if none."
    )
    undelivered: List[str] = dspy.OutputField(
        desc="Things explicitly asked for in this chunk that weren't "
        "actually delivered (or only claimed done). Empty list if none."
    )
    frustration_present: bool = dspy.OutputField(
        desc="True if the user expressed real frustration/anger/resigned "
        "give-up language in this chunk, false otherwise."
    )
    frustration_intensity: FrustrationIntensity = dspy.OutputField(
        desc="'none' if frustration_present is false."
    )
    exit_type: ExitType = dspy.OutputField(
        desc="Whether this chunk ends in a forced session boundary while "
        "something is unresolved. 'none' if not."
    )
    profanity_present: bool = dspy.OutputField(
        desc="True if profanity/insults appear in this chunk. Marker "
        "only — never a reason to store or omit anything by itself."
    )
    summary: str = dspy.OutputField(
        desc="1-2 sentences on what this chunk covered — carried forward "
        "as prior_summary for the NEXT chunk of the same session, so a "
        "later chunk that reads like a continuation (e.g. the user just "
        "says \"no, also fix the other one\") can be understood correctly."
    )


def _empty_distill_result() -> Dict[str, Any]:
    return {
        "preferences": [], "corrections": [], "reversals": [], "undelivered": [],
        "frustration_present": False, "frustration_intensity": "none",
        "exit_type": "none", "profanity_present": False, "summary": "",
    }


_VALID_INTENSITY = {"none", "low", "med", "high", "extreme"}
_VALID_EXIT_TYPE = {"none", "compact_under_fire", "disagreement", "delete_threat", "session_abandon"}


def distill_transcript(
    transcript_text: str,
    lm: Optional[dspy.LM],
    prior_summary: str = "",
) -> Dict[str, Any]:
    """Run one extraction call over a transcript chunk. Empty result on any
    failure or missing lm — never raises, never guesses."""
    empty = _empty_distill_result()
    if lm is None or not transcript_text.strip():
        return empty

    try:
        with dspy.context(lm=lm):
            out = dspy.Predict(DistillPreferencesAndCorrections)(
                transcript=transcript_text, prior_summary=prior_summary.strip(),
            )
    except Exception:
        return empty

    intensity = str(out.frustration_intensity).strip().lower()
    exit_type = str(out.exit_type).strip().lower()
    result = {
        "preferences": [str(p).strip() for p in out.preferences if str(p).strip()],
        "corrections": [str(c).strip() for c in out.corrections if str(c).strip()],
        "reversals": [str(r).strip() for r in out.reversals if str(r).strip()],
        "undelivered": [str(u).strip() for u in out.undelivered if str(u).strip()],
        "frustration_present": bool(out.frustration_present),
        "frustration_intensity": intensity if intensity in _VALID_INTENSITY else "none",
        "exit_type": exit_type if exit_type in _VALID_EXIT_TYPE else "none",
        "profanity_present": bool(out.profanity_present),
        "summary": str(out.summary).strip(),
    }
    # Full structured record, all 9 fields together, including `summary`
    # which was previously never persisted anywhere at all (see
    # lib/generation_log.py docstring) — episode_id/session_id/project
    # filled in by the caller (distill_to_episodes doesn't know episode
    # ids yet at this point; logged here as a pre-episode record).
    log_generation("distill", result)
    return result


def preference_episode(session_id: str, text: str) -> MemoryEpisode:
    """L1 preference episode — permanent, always eligible for injection
    (see lib/memory_policy.py)."""
    return MemoryEpisode(
        id=f"preference_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S%f')}",
        session_id=session_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        layer=1,
        title="User preference",
        content=text,
        category="preference",
        importance=1.0,
        tags=["preference", "distilled"],
        is_permanent=True,
        trigger_type="user_request",
        lesson=text,
    )


def correction_episode(session_id: str, text: str) -> MemoryEpisode:
    """L1 correction episode — permanent, always eligible for injection."""
    return MemoryEpisode(
        id=f"correction_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S%f')}",
        session_id=session_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        layer=1,
        title="Correction applied",
        content=text,
        category="correction",
        importance=1.0,
        tags=["correction", "distilled"],
        correction_applied=True,
        correction_delta=text,
        is_permanent=True,
        trigger_type="reaction",
        user_sentiment="negative",
        lesson=text,
    )


def reversal_episode(session_id: str, text: str) -> MemoryEpisode:
    """L1 reversal episode — a decision flipped mid-session, not a
    rejection of assistant work. Permanent/injectable: the reversal is
    itself a standing fact worth a future session knowing about (see
    docs/transcript-audit-findings.md §4)."""
    return MemoryEpisode(
        id=f"reversal_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S%f')}",
        session_id=session_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        layer=1,
        title="Decision reversed",
        content=text,
        category="reversal",
        importance=1.0,
        tags=["reversal", "distilled"],
        is_permanent=True,
        trigger_type="user_request",
        lesson=text,
    )


def undelivered_episode(session_id: str, text: str) -> MemoryEpisode:
    """L1 open/undelivered episode — asked for, not shown as done. NOT
    permanent/injected by default: an open item can get resolved later,
    unlike a standing preference or a settled reversal (see
    docs/prompt-critique-brief.md ownership table: undelivered -> inject
    "maybe", not "yes")."""
    return MemoryEpisode(
        id=f"undelivered_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S%f')}",
        session_id=session_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        layer=1,
        title="Asked, not delivered",
        content=text,
        category="undelivered",
        importance=0.7,
        tags=["undelivered", "distilled"],
        is_permanent=False,
        trigger_type="user_request",
        lesson=text,
    )


def distill_to_episodes(
    session_id: str,
    transcript_text: str,
    lm: Optional[dspy.LM],
    prior_summary: str = "",
) -> tuple:
    """Full pipeline: transcript -> distill -> MemoryEpisode objects, ready
    to save. Returns (episodes, summary, frustration) — summary is this
    chunk's own "what happened" text, meant to be passed back in as
    prior_summary for the NEXT chunk of the same session (see
    distill_transcript). frustration is a dict (present/intensity/
    exit_type/profanity_present) for the caller to emit as a bus event —
    structural signal only, never injected as text."""
    result = distill_transcript(transcript_text, lm, prior_summary=prior_summary)
    episodes: List[MemoryEpisode] = []
    for text in result["preferences"]:
        episodes.append(preference_episode(session_id, text))
    for text in result["corrections"]:
        episodes.append(correction_episode(session_id, text))
    for text in result["reversals"]:
        episodes.append(reversal_episode(session_id, text))
    for text in result["undelivered"]:
        episodes.append(undelivered_episode(session_id, text))
    frustration = {
        "present": result["frustration_present"],
        "intensity": result["frustration_intensity"],
        "exit_type": result["exit_type"],
        "profanity_present": result["profanity_present"],
    }
    return episodes, result["summary"], frustration
