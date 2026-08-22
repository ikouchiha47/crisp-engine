"""Normalizer stage: scrub secrets, collapse whitespace, enforce ordering."""

from __future__ import annotations

from dataclasses import replace

from .redact import NullRedactor, Redactor, RegexRedactor
from .types import SessionIR


def _collapse_ws(text: str) -> str:
    return " ".join(text.split())


class DefaultNormalizer:
    """Normalize strategy: redaction + whitespace + stable ordering."""

    def __init__(self, redactor: Redactor | None = None, redact: bool = True) -> None:
        self._redactor = redactor or (RegexRedactor() if redact else NullRedactor())

    def normalize(self, session: SessionIR) -> SessionIR:
        turns = []
        for t in session.turns:
            text = self._redactor.redact(t.text)
            text = _collapse_ws(text)
            turns.append(replace(t, text=text))
        turns.sort(key=lambda t: (t.ts, t.provider_msg_id))
        return replace(session, turns=tuple(turns))