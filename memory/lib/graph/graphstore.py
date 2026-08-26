"""Builds and persists the code call graph.

Persisted as plain JSON ({nodes:[], edges:[]}) under the project store's
cache dir — fully rebuildable from source via build_graph(), same philosophy
already used for vec_sidecar.db (see memory/README.md storage layout).
"""
import json
from pathlib import Path
from typing import List

import networkx as nx

from ..code_index import CodeAnalyzer, CodeElement
from .callwalk import walk_calls
from .resolve import Edge, resolve

GRAPH_FILENAME = "code_graph.json"

# CodeAnalyzer.analyze_directory() does a raw rglob with no exclusions —
# fine for indexing a single known file, wrong for a directory build, which
# would otherwise happily walk into .venv/site-packages and graph installed
# library code as if it were the project's own. Excluded here, not in
# CodeAnalyzer itself, to keep this fix scoped to the graph feature.
DEFAULT_EXCLUDE_DIRS = {
    ".venv", "venv", "env", "node_modules", "__pycache__", ".git",
    "site-packages", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".tox", "egg-info",
}


def extract_elements(directory: str, exclude_dirs: set = None) -> List[CodeElement]:
    """All CodeElements under a directory, flattened, with .calls populated.

    Walks files directly (rather than CodeAnalyzer.analyze_directory's raw
    rglob) so vendored/installed code under .venv, node_modules, etc. is
    never mistaken for project source.
    """
    exclude_dirs = DEFAULT_EXCLUDE_DIRS if exclude_dirs is None else exclude_dirs
    analyzer = CodeAnalyzer()
    root = Path(directory).resolve()
    elements: List[CodeElement] = []

    for fp in root.rglob("*"):
        if not fp.is_file():
            continue
        if any(part in exclude_dirs or part.endswith(".egg-info") for part in fp.parts):
            continue
        if fp.suffix.lower() not in analyzer.LANGUAGE_EXTENSIONS:
            continue
        elements.extend(analyzer.analyze_file(str(fp)))

    walk_calls(elements)
    return elements


def build_graph(directory: str) -> nx.DiGraph:
    elements = extract_elements(directory)
    edges = resolve(elements)
    G = graph_from(elements, edges)
    _assign_communities(G)
    return G


def _assign_communities(G: nx.DiGraph) -> None:
    """Louvain community id per node, stored as node attr `community` (int).

    Best-effort: an empty/edgeless graph or a networkx build without the
    community module leaves every node at community 0 rather than raising —
    this is a display grouping, not something downstream logic depends on.
    """
    try:
        from networkx.algorithms.community import louvain_communities
        undirected = G.to_undirected()
        communities = louvain_communities(undirected, seed=7) if G.number_of_edges() else []
    except Exception:
        communities = []

    for node in G.nodes:
        G.nodes[node]["community"] = 0
    for idx, members in enumerate(communities):
        for node in members:
            G.nodes[node]["community"] = idx


def graph_from(elements: List[CodeElement], edges: List[Edge]) -> nx.DiGraph:
    G = nx.DiGraph()
    for elem in elements:
        G.add_node(
            elem.id,
            name=elem.name,
            type=elem.type,
            file_path=elem.file_path,
            start_line=elem.start_line,
            end_line=elem.end_line,
        )
    for edge in edges:
        # An edge may reference a node not in `elements` if graph_from was
        # called with a partial element set (e.g. tests) — add a stub node
        # rather than raise, mirroring the fallback-chain "never raise"
        # convention used elsewhere in code_index/.
        for node_id in (edge.source, edge.target):
            if node_id not in G:
                G.add_node(node_id, name=node_id, type="unknown", file_path="", start_line=0, end_line=0)
        G.add_edge(edge.source, edge.target, relation=edge.relation, confidence=edge.confidence)
    return G


def save_graph(G: nx.DiGraph, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "nodes": [{"id": n, **attrs} for n, attrs in G.nodes(data=True)],
        "edges": [
            {"source": u, "target": v, **attrs}
            for u, v, attrs in G.edges(data=True)
        ],
    }
    path.write_text(json.dumps(data, indent=2))


def load_graph(path: Path) -> nx.DiGraph:
    path = Path(path)
    data = json.loads(path.read_text())
    G = nx.DiGraph()
    for node in data["nodes"]:
        node_id = node.pop("id")
        G.add_node(node_id, **node)
    for edge in data["edges"]:
        source = edge.pop("source")
        target = edge.pop("target")
        G.add_edge(source, target, **edge)
    return G
