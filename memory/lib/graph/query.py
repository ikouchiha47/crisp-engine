"""Read-only lookups over a built call graph."""
from typing import Any, Dict, List

import networkx as nx


def find_node_by_name(G: nx.DiGraph, name: str) -> List[str]:
    """Node ids whose `name` attr matches exactly."""
    return [n for n, attrs in G.nodes(data=True) if attrs.get("name") == name]


def callers_of(G: nx.DiGraph, node_id: str) -> List[Dict[str, Any]]:
    if node_id not in G:
        return []
    return [{"id": p, **G.nodes[p], **G.edges[p, node_id]} for p in G.predecessors(node_id)]


def callees_of(G: nx.DiGraph, node_id: str) -> List[Dict[str, Any]]:
    if node_id not in G:
        return []
    return [{"id": s, **G.nodes[s], **G.edges[node_id, s]} for s in G.successors(node_id)]


def shortest_path(G: nx.DiGraph, source_id: str, target_id: str) -> List[Dict[str, Any]]:
    """Shortest path (direction-agnostic hop-finding, direction-aware
    reporting) between two nodes.

    Returns a list of hops: [{from, to, relation, confidence, reversed}],
    empty if source/target are missing or no path exists. `reversed=True`
    means the path walks a `calls` edge backwards (callee -> caller) to
    connect the two nodes, same as graphify reporting `<--` vs `-->`.
    """
    if source_id not in G or target_id not in G:
        return []
    undirected = G.to_undirected(as_view=True)
    try:
        node_path = nx.shortest_path(undirected, source_id, target_id)
    except nx.NetworkXNoPath:
        return []

    hops = []
    for a, b in zip(node_path, node_path[1:]):
        if G.has_edge(a, b):
            attrs = G.edges[a, b]
            hops.append({"from": a, "to": b, "relation": attrs.get("relation", "calls"),
                         "confidence": attrs.get("confidence", ""), "reversed": False})
        else:
            attrs = G.edges[b, a]
            hops.append({"from": a, "to": b, "relation": attrs.get("relation", "calls"),
                         "confidence": attrs.get("confidence", ""), "reversed": True})
    return hops


def neighbors(G: nx.DiGraph, node_id: str, depth: int = 1) -> List[str]:
    if node_id not in G:
        return []
    seen = {node_id}
    frontier = {node_id}
    for _ in range(depth):
        nxt = set()
        for n in frontier:
            nxt |= set(G.predecessors(n)) | set(G.successors(n))
        nxt -= seen
        seen |= nxt
        frontier = nxt
    seen.discard(node_id)
    return list(seen)
