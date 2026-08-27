"""Capped hot-memory files — the "always injected first" tier
(docs/next-steps-sequence.md Phase 2; restructured per
plans/ADR-004-hot-path-and-feedback-loops.md Track A). Four files per
project store, each trimmed to a config-driven char cap with simple FIFO
eviction (oldest line dropped first) — no LLM compression/dedup pass yet
(ADR-004 A2/A3, not built this pass):

  user.md      preferences — durable always/never/how rules
  memory.md    corrections — this-turn rejects
  reversal.md  reversals — settled decisions that flipped mid-session,
               kept separate from corrections since they're a different
               kind of fact (a plan changed, not something was wrong)
  identity.md  L3 standing relationship laws — populated only when L3
               regenerates (rare), read at SessionStart only, never via
               the per-tool-call build_context_block path (see
               lib/hooks/injection.py and lib/hooks/__init__.py)
"""
from __future__ import annotations

from pathlib import Path

from lib.bus import emit as _bus_emit, HotMemoryPatched
from lib.store import MemoryStore

DEFAULT_CHAR_CAP = 2000

_KINDS = ("user", "memory", "reversal", "identity")


class HotMemoryStore:
    def __init__(self, store: MemoryStore, char_cap: int = DEFAULT_CHAR_CAP):
        self.dir = Path(store.base_path) / "hot"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.char_cap = char_cap

    def _path(self, kind: str) -> Path:
        assert kind in _KINDS, f"unknown hot-memory kind: {kind!r}"
        return self.dir / f"{kind}.md"

    def read(self, kind: str) -> str:
        p = self._path(kind)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def apply_patch(self, kind: str, text: str, session_id: str = "unknown", project: str = "-") -> None:
        text = text.strip()
        if not text:
            return
        existing = self.read(kind)
        lines = [l for l in existing.splitlines() if l.strip()]
        lines.append(f"- {text}")

        # Trim oldest lines first until under cap.
        while lines and sum(len(l) + 1 for l in lines) > self.char_cap:
            lines.pop(0)

        content = "\n".join(lines) + "\n"
        self._path(kind).write_text(content, encoding="utf-8")

        try:
            _bus_emit(HotMemoryPatched(
                session_id=session_id, project=project, kind=kind, char_count=len(content),
            ))
        except Exception:
            pass

    def write_identity(self, lines: list, session_id: str = "unknown", project: str = "-") -> None:
        """Full replace, not FIFO-append — identity.md reflects the LATEST
        L3 arc's meta_lessons only. L3 itself already accumulates across
        regenerations (its own docstring: "never regenerated from scratch,
        only extended"); the hot file mirrors current state, it doesn't
        need its own separate history of every past L3 version."""
        content = "\n".join(f"- {l.strip()}" for l in lines if l.strip())[: self.char_cap] + "\n"
        self._path("identity").write_text(content, encoding="utf-8")
        try:
            _bus_emit(HotMemoryPatched(
                session_id=session_id, project=project, kind="identity", char_count=len(content),
            ))
        except Exception:
            pass
