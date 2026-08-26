"""PreToolUse context injection — what actually reaches the model.

Rewritten (docs/next-steps-sequence.md Phase 1.6) to an allowlisted pass
instead of the old two-section dump: no raw per-file code_element signature
listing (that's what `crisp graph show` is for now), no tool-frequency
instinct noise below the confidence bar. Only categories memory_policy.py
marks injectable ever appear here.

Known consequence: until lib/affect.py (Phase 3) exists, no preference/
correction episodes are ever created, so this will often return "" — that's
correct and intended, not a bug. Quiet beats actively wrong.
"""
from pathlib import Path
from typing import Any, Dict

from lib.bus import emit as _bus_emit, ContextInjected
from lib.memory_policy import is_injectable
from lib.store import MemoryStore


class ContextInjector:
    """Builds and serves the PreToolUse `additionalContext` block."""

    def __init__(self, store: MemoryStore):
        self.store = store
        self._ep_cache: list | None = None  # populated once per process
        self.project_root = ""

    def set_project_root(self, root: str) -> None:
        if root:
            self.project_root = root

    def warm_cache(self) -> None:
        """Called at session-start, where there's budget to spare — pre-tool
        calls must stay fast."""
        self._ep_cache = self.store.list_episodes()

    def build_context_block(self, tool: str, tool_input: Dict[str, Any], session_id: str) -> str:
        """Return an injectable memory block, or "" when nothing qualifies.

        Allowlist (memory_policy.is_injectable): preference/correction
        episodes unconditionally, instinct episodes at confidence >= 0.5,
        capped at 5, ranked by confidence/importance descending.
        """
        try:
            if self._ep_cache is None:
                self._ep_cache = self.store.list_episodes()
            all_eps = self._ep_cache

            candidates = [
                ep for ep in all_eps
                if ep.layer >= 1
                and is_injectable(ep.category, getattr(ep, "confidence", 0.0))
                and (ep.category != "instinct" or tool in (ep.tags or []))
            ]
            if not candidates:
                return ""

            candidates.sort(
                key=lambda e: (getattr(e, "confidence", 0.0), e.importance),
                reverse=True,
            )
            lines = ["[crisp memory]"]
            for ep in candidates[:5]:
                conf = getattr(ep, "confidence", 0.0)
                suffix = f" (confidence {conf:.2f})" if ep.category == "instinct" else ""
                lines.append(f"- {ep.content.strip()}{suffix}")
            return "\n".join(lines)
        except Exception:
            return ""

    def handle_claude_pre_tool_context(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """PreToolUse for Claude Code — returns hookSpecificOutput.additionalContext.

        Claude Code reads this field and prepends it to the tool context
        visible to the model. Returns {} when nothing to inject.
        """
        tool = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})
        session_id = data.get("session_id", "")

        block = self.build_context_block(tool, tool_input, session_id)
        if not block:
            return {}

        try:
            _bus_emit(ContextInjected(
                session_id=session_id or "unknown",
                project=self.project_root or "-",
                tool=tool,
                char_count=len(block),
            ))
        except Exception:
            pass

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": block,
            }
        }
