"""Single source of truth for what an episode `category` is allowed to do.

Three questions, three functions — nothing else in the codebase should
re-derive these answers with its own ad-hoc string check:

  is_episodic(category)    -> eligible for L0->L1->L2->L3 consolidation
  is_code_index(category)  -> structural/indexed content (never consolidated,
                               never used as an L1 "lesson")
  is_injectable(category, confidence) -> allowed into build_context_block()

Reuses store.is_code_index_category() rather than duplicating that check.
"""
from lib.store.memory_store import is_code_index_category

EPISODIC_CATEGORIES = frozenset({
    "conversation", "correction", "frustration", "preference", "git_commit",
})

INJECTABLE_CATEGORIES = frozenset({"preference", "correction"})

INSTINCT_CONFIDENCE_THRESHOLD = 0.5


def is_episodic(category: str) -> bool:
    """True if this category represents real content worth consolidating
    into L1 session summaries and beyond. code_index* is deliberately
    excluded — see is_code_index()."""
    return category in EPISODIC_CATEGORIES


def is_code_index(category: str) -> bool:
    """True for structural/indexed episodes (tree-sitter symbols, directory
    stubs) — searchable and graph-eligible, never consolidated, never
    treated as an L1 "lesson"."""
    return is_code_index_category(category)


def is_injectable(category: str, confidence: float = 0.0) -> bool:
    """True if an episode of this category/confidence may appear in
    build_context_block(). Preferences/corrections are always eligible;
    instincts need confidence >= INSTINCT_CONFIDENCE_THRESHOLD."""
    if category in INJECTABLE_CATEGORIES:
        return True
    if category == "instinct":
        return confidence >= INSTINCT_CONFIDENCE_THRESHOLD
    return False
