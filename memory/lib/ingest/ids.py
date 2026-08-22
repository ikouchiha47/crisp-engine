"""Stable identifier helpers (ADR-0001 §3)."""

from __future__ import annotations

import hashlib


def stable_id(*parts: str, n: int = 16) -> str:
    h = hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()
    return h[:n]


def turn_id(provider: str, session_id: str, provider_msg_id: str) -> str:
    return stable_id(provider, session_id, provider_msg_id)


def segment_id(session_id: str, start_idx: int, end_idx: int) -> str:
    return stable_id(session_id, str(start_idx), str(end_idx))