"""Cross-file code call graph — the piece tree-sitter extraction stops short
of: who calls whom, across files, with a confidence label per edge.

Pipeline (mirrors graphify's extract -> build -> analyze shape, scoped down):
    callwalk.walk_calls(elements)   populates elem.calls with raw callee names
    resolve.resolve(elements)       raw names -> typed, confidence-labeled Edges
    graphstore.build_graph(...)     Edges + elements -> networkx.DiGraph
    query.callers_of / callees_of   read-only graph lookups
"""
from .resolve import Edge, EXTRACTED, INFERRED, AMBIGUOUS

__all__ = ["Edge", "EXTRACTED", "INFERRED", "AMBIGUOUS"]
