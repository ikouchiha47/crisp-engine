"""Secret redaction strategy (ADR-0001 §14). Default ON; configurable."""

from __future__ import annotations

import re
from typing import Protocol

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
)


class Redactor(Protocol):
    def redact(self, text: str) -> str:
        ...


class RegexRedactor:
    """Redactor strategy: regex secret scrub."""

    def __init__(self, patterns: tuple[re.Pattern[str], ...] = SECRET_PATTERNS) -> None:
        self.patterns = patterns

    def redact(self, text: str) -> str:
        out = text
        for pat in self.patterns:
            out = pat.sub("[REDACTED]", out)
        return out


class NullRedactor:
    """Redactor strategy: pass-through (when --no-redact)."""

    def redact(self, text: str) -> str:
        return text