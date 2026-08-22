"""
Content filter — cleans episode content before it reaches the store.

Two-pass pipeline:
  Pass 1: regex wordlist (built-in wordlist.py + dsojevic/profanity-list cache)
  Pass 2: optional LLM rewrite (off by default)

Actions:
  redact — replace matched text with [redacted]
  drop   — return None (caller must discard the episode)
  flag   — keep content, add filtered=true tag
"""

import re
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .wordlist import WORDLIST

log = logging.getLogger(__name__)

_PLACEHOLDER = "[redacted]"


@dataclass
class FilterResult:
    content: str
    action_taken: str           # "none" | "redact" | "drop" | "flag"
    hits: list[str] = field(default_factory=list)
    dropped: bool = False


class ContentFilter:
    def __init__(self, config: dict | None = None, extra_wordlist_path: Path | None = None):
        cfg = config or {}
        self.enabled: bool = cfg.get("enabled", True)
        self.default_mode: str = cfg.get("mode", "redact")   # redact | drop | flag
        self.log_hits: bool = cfg.get("log_hits", True)
        self.user_words: list[str] = cfg.get("wordlist", [])
        self.user_drop_patterns: list[str] = cfg.get("drop_patterns", [])

        self._drop_re: re.Pattern | None = None
        self._redact_re: re.Pattern | None = None
        self._drop_patterns_re: list[re.Pattern] = []

        self._build_patterns(extra_wordlist_path)

    # ── public ────────────────────────────────────────────────────────────────

    def clean(self, content: str) -> Optional[str]:
        """
        Returns cleaned content string, or None if the content should be dropped.
        """
        if not self.enabled or not content:
            return content

        result = self._pass1(content)

        if result.dropped:
            if self.log_hits:
                log.info("content_filter: DROP hits=%s", result.hits)
            return None

        if result.action_taken != "none" and self.log_hits:
            log.info("content_filter: %s hits=%s", result.action_taken.upper(), result.hits)

        return result.content

    # ── internals ─────────────────────────────────────────────────────────────

    def _build_patterns(self, extra_path: Path | None) -> None:
        drop_words: list[str] = []
        redact_words: list[str] = []

        for cat in WORDLIST.values():
            action = cat.get("action", self.default_mode)
            words = cat.get("words", [])
            if action == "drop":
                drop_words.extend(words)
            else:
                redact_words.extend(words)

            for pat in cat.get("patterns", []):
                if action == "drop":
                    self._drop_patterns_re.append(
                        re.compile(pat, re.IGNORECASE)
                    )
                # redact patterns are folded into _redact_re below via the word list
                # (pattern-only categories use drop_patterns_re or redact via _redact_re)

        # Load external JSON list (dsojevic format) if available
        if extra_path and extra_path.exists():
            try:
                entries = json.loads(extra_path.read_text())
                for entry in entries:
                    severity = entry.get("severity", 3)
                    action = "drop" if severity >= 3 else "redact"
                    for variant in entry.get("match", "").split("|"):
                        v = variant.strip()
                        if v:
                            (drop_words if action == "drop" else redact_words).append(v)
            except Exception as e:
                log.warning("content_filter: failed to load extra wordlist %s: %s", extra_path, e)

        # User overrides
        redact_words.extend(self.user_words)

        # Compile user drop patterns
        for pat in self.user_drop_patterns:
            try:
                self._drop_patterns_re.append(re.compile(pat, re.IGNORECASE))
            except re.error:
                log.warning("content_filter: invalid drop_pattern ignored: %s", pat)

        self._drop_re = self._build_word_re(drop_words) if drop_words else None
        self._redact_re = self._build_word_re(redact_words) if redact_words else None

    @staticmethod
    def _build_word_re(words: list[str]) -> re.Pattern:
        # Escape and sort longest-first to avoid partial shadowing
        escaped = sorted(
            (re.escape(w) for w in set(words) if w),
            key=len,
            reverse=True,
        )
        return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)

    def _pass1(self, content: str) -> FilterResult:
        hits: list[str] = []

        # Check drop patterns first (regex-only categories)
        for pat in self._drop_patterns_re:
            m = pat.search(content)
            if m:
                hits.append(m.group())
                return FilterResult(content="", action_taken="drop", hits=hits, dropped=True)

        # Check drop word list
        if self._drop_re:
            m = self._drop_re.search(content)
            if m:
                hits.append(m.group())
                return FilterResult(content="", action_taken="drop", hits=hits, dropped=True)

        # Check redact word list
        if self._redact_re:
            cleaned, n = self._redact_re.subn(_PLACEHOLDER, content)
            if n:
                hits = self._redact_re.findall(content)
                return FilterResult(content=cleaned, action_taken="redact", hits=hits, dropped=False)

        return FilterResult(content=content, action_taken="none", hits=[], dropped=False)
