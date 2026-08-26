"""Distills explicit preferences and corrections out of a conversation
transcript using the local chat provider (lib/generate.py) — the missing
piece the AUDIT flagged: without this, build_context_block() has nothing
real to inject.

Deliberately narrow: extraction only, via one LLM call per transcript,
parsed defensively. No retries, no chaining, no agentic loop — a bad/missing
provider means zero episodes this run, never a guess dressed up as memory.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from lib.generate import ChatProvider
from lib.store import MemoryEpisode

_SYSTEM_PROMPT = (
    "Extract explicit user preferences and corrections from the conversation "
    'turn below. Return ONLY a JSON object: {"preferences": [string], '
    '"corrections": [string]}. A preference is an explicit instruction about '
    "how to behave going forward (e.g. \"always use uv\", \"don't use dark "
    'theme"). A correction is the user rejecting or fixing something the '
    "assistant did or said. Keep each item short (one sentence). If there is "
    "nothing to extract, return empty arrays. Do not include anything else "
    "in the response."
)


def distill_transcript(transcript_text: str, provider: Optional[ChatProvider]) -> Dict[str, List[str]]:
    """Run one extraction call over a transcript. Empty result on any failure
    or missing provider — never raises, never guesses.
    """
    empty: Dict[str, List[str]] = {"preferences": [], "corrections": []}
    if provider is None or not transcript_text.strip():
        return empty

    raw = provider.generate(_SYSTEM_PROMPT, transcript_text)
    if not raw:
        return empty

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return empty

    if not isinstance(data, dict):
        return empty

    prefs = data.get("preferences", [])
    corrections = data.get("corrections", [])
    return {
        "preferences": [str(p).strip() for p in prefs if isinstance(p, (str, int, float)) and str(p).strip()],
        "corrections": [str(c).strip() for c in corrections if isinstance(c, (str, int, float)) and str(c).strip()],
    }


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


def distill_to_episodes(session_id: str, transcript_text: str, provider: Optional[ChatProvider]) -> List[MemoryEpisode]:
    """Full pipeline: transcript -> distill -> MemoryEpisode objects, ready to save."""
    result = distill_transcript(transcript_text, provider)
    episodes: List[MemoryEpisode] = []
    for text in result["preferences"]:
        episodes.append(preference_episode(session_id, text))
    for text in result["corrections"]:
        episodes.append(correction_episode(session_id, text))
    return episodes
