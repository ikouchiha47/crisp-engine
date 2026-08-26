"""Resolves raw callee names (from callwalk.py) into typed, confidence-labeled
edges between CodeElement ids — the cross-file symbol resolution step that
was previously entirely missing.

Confidence scheme matches graphify's (see its ARCHITECTURE.md):
  EXTRACTED — unambiguous, same file as the caller
  INFERRED  — unambiguous, but resolved across files
  AMBIGUOUS — multiple candidate definitions share the callee name
Calls that match no known symbol (stdlib/external) are dropped, not guessed.
"""
from dataclasses import dataclass
from typing import Dict, List

from ..code_index import CodeElement

EXTRACTED = "EXTRACTED"
INFERRED = "INFERRED"
AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class Edge:
    source: str  # caller CodeElement.id
    target: str  # callee CodeElement.id
    relation: str  # "calls"
    confidence: str  # EXTRACTED | INFERRED | AMBIGUOUS


def _build_symbol_table(elements: List[CodeElement]) -> Dict[str, List[CodeElement]]:
    table: Dict[str, List[CodeElement]] = {}
    for elem in elements:
        table.setdefault(elem.name, []).append(elem)
    return table


def resolve(elements: List[CodeElement]) -> List[Edge]:
    """Resolve every element's `.calls` raw names into Edges.

    Deterministic: when a name is ambiguous, the AMBIGUOUS edge points at
    the candidate with the lexicographically smallest id, so re-running on
    unchanged input reproduces the same graph.
    """
    symbol_table = _build_symbol_table(elements)
    edges: List[Edge] = []

    for caller in elements:
        for raw_name in caller.calls:
            candidates = symbol_table.get(raw_name, [])
            candidates = [c for c in candidates if c.id != caller.id]
            if not candidates:
                continue  # stdlib / external / unknown — drop, don't guess

            same_file = [c for c in candidates if c.file_path == caller.file_path]

            if len(same_file) == 1:
                edges.append(Edge(caller.id, same_file[0].id, "calls", EXTRACTED))
            elif len(same_file) > 1:
                target = min(same_file, key=lambda c: c.id)
                edges.append(Edge(caller.id, target.id, "calls", AMBIGUOUS))
            elif len(candidates) == 1:
                edges.append(Edge(caller.id, candidates[0].id, "calls", INFERRED))
            else:
                target = min(candidates, key=lambda c: c.id)
                edges.append(Edge(caller.id, target.id, "calls", AMBIGUOUS))

    return edges
