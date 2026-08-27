"""PreToolUse context injection — what actually reaches the model.

Rewritten (docs/next-steps-sequence.md Phase 1.6) to an allowlisted pass
instead of the old two-section dump: no raw per-file code_element signature
listing (that's what `crisp graph show` is for now), no tool-frequency
instinct noise below the confidence bar. Only categories memory_policy.py
marks injectable ever appear here.

Phase 2 (hot memory + put-back): injection order is now hot files (user
preferences, then corrections) -> episodic preference/correction/instinct
episodes, all under a total char budget (lowest-priority section trimmed
first, always at a section boundary, never mid-sentence). Every episode
that actually gets included has its access_count/last_accessed bumped and
persisted — closes the AUDIT.md-flagged gap where access_freq was always
zero because nothing ever recorded a retrieval.
"""
from pathlib import Path
from typing import Any, Dict, List

from lib import config as _cfg
from lib.bus import emit as _bus_emit, ContextInjected
from lib.memory_policy import is_injectable
from lib.store import MemoryStore
from lib.time_utils import now_iso

from ..hot_memory import HotMemoryStore

DEFAULT_CHAR_BUDGET = 4000


class ContextInjector:
    """Builds and serves the PreToolUse `additionalContext` block."""

    def __init__(self, store: MemoryStore):
        self.store = store
        self._ep_cache: list | None = None  # populated once per process
        self.project_root = ""
        self.hot = HotMemoryStore(store)

    def set_project_root(self, root: str) -> None:
        if root:
            self.project_root = root

    def warm_cache(self) -> None:
        """Called at session-start, where there's budget to spare — pre-tool
        calls must stay fast."""
        self._ep_cache = self.store.list_episodes()

    def _char_budget(self) -> int:
        merged = _cfg.load()
        merged.update(self.store.config)
        try:
            return int(merged.get("inject_char_budget", DEFAULT_CHAR_BUDGET))
        except (ValueError, TypeError):
            return DEFAULT_CHAR_BUDGET

    def build_context_block(self, tool: str, tool_input: Dict[str, Any], session_id: str) -> str:
        """Return an injectable memory block, or "" when nothing qualifies.

        Section order (highest priority first — trimmed from the bottom if
        over budget, never mid-section):
          1. Hot user preferences (lib/hooks/hot_memory.py)
          2. Hot corrections
          3. Hot reversals (ADR-004 Track A — separate from corrections:
             a settled decision that changed, not a this-turn mistake)
          4. Episodic preference/correction/reversal/instinct episodes
             (memory_policy.is_injectable), capped at 5, ranked by
             confidence/importance descending.

        hot/identity.md (L3 standing relationship laws) is injected once
        per session, on the FIRST call only — not SessionStart: that hook
        is configured "async": true (HOOKS_CONFIG.md), so Claude Code
        doesn't consume its output for additionalContext the way it does
        for synchronous PreToolUse (confirmed this session — same class
        of gap as the daemon-thread bug found earlier). Tracked via
        store.get_file_state, same delta-tracking pattern already used
        for transcript cursors.
        """
        try:
            budget = self._char_budget()
            sections: List[str] = []

            identity_key = f"identity_injected:{session_id}"
            if session_id and not self.store.get_file_state(identity_key):
                hot_identity = self.hot.read("identity").strip()
                if hot_identity:
                    sections.append("[crisp identity]\n" + hot_identity)
                self.store.set_file_state(identity_key, "1")

            hot_user = self.hot.read("user").strip()
            if hot_user:
                sections.append("[crisp preferences]\n" + hot_user)

            hot_memory = self.hot.read("memory").strip()
            if hot_memory:
                sections.append("[crisp corrections]\n" + hot_memory)

            hot_reversal = self.hot.read("reversal").strip()
            if hot_reversal:
                sections.append("[crisp reversals]\n" + hot_reversal)

            if self._ep_cache is None:
                self._ep_cache = self.store.list_episodes()
            all_eps = self._ep_cache

            hot_texts = (hot_user, hot_memory, hot_reversal)
            candidates = [
                ep for ep in all_eps
                if ep.layer >= 1
                and is_injectable(ep.category, getattr(ep, "confidence", 0.0))
                and (ep.category != "instinct" or tool in (ep.tags or []))
                # Skip only if this exact content is already surfaced via a
                # hot file — content-based, not category-based: an episode
                # saved directly (never hot-patched, e.g. by a caller other
                # than run_distill) must still be injectable here. Category
                # alone isn't a safe signal that hot-patching happened.
                and not (ep.content.strip() and any(
                    ep.content.strip() in t for t in hot_texts if t
                ))
            ]
            included = []
            if candidates:
                candidates.sort(
                    key=lambda e: (getattr(e, "confidence", 0.0), e.importance),
                    reverse=True,
                )
                lines = ["[crisp memory]"]
                for ep in candidates[:5]:
                    conf = getattr(ep, "confidence", 0.0)
                    suffix = f" (confidence {conf:.2f})" if ep.category == "instinct" else ""
                    lines.append(f"- {ep.content.strip()}{suffix}")
                    included.append(ep)
                sections.append("\n".join(lines))

            # Trim lowest-priority (last) sections first until under budget —
            # section boundaries only, never mid-sentence.
            while sections and sum(len(s) for s in sections) + 2 * (len(sections) - 1) > budget:
                dropped = sections.pop()
                if dropped.startswith("[crisp memory]"):
                    included = []  # that section didn't make it — nothing to mark accessed

            if included:
                ts = now_iso()
                for ep in included:
                    ep.access_count = (ep.access_count or 0) + 1
                    ep.last_accessed = ts
                    try:
                        self.store.update_episode(ep)
                    except Exception:
                        pass

            return "\n\n".join(sections)
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
