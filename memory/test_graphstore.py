"""Black-box tests for lib/graph/graphstore.py and lib/graph/query.py."""
from pathlib import Path

from lib.code_index import CodeElement
from lib.graph.graphstore import graph_from, load_graph, save_graph
from lib.graph.query import callees_of, callers_of, find_node_by_name, neighbors
from lib.graph.resolve import EXTRACTED, Edge


def _fixture():
    a = CodeElement(id="a_id", name="a", type="function", language="python",
                     file_path="x.py", start_line=1, end_line=2)
    b = CodeElement(id="b_id", name="b", type="function", language="python",
                     file_path="x.py", start_line=4, end_line=5)
    edges = [Edge("a_id", "b_id", "calls", EXTRACTED)]
    return [a, b], edges


def test_graph_from_has_expected_nodes_and_edges():
    elements, edges = _fixture()
    G = graph_from(elements, edges)
    assert set(G.nodes) == {"a_id", "b_id"}
    assert G.number_of_edges() == 1
    assert G.edges["a_id", "b_id"]["confidence"] == EXTRACTED


def test_save_and_load_graph_roundtrips(tmp_path: Path):
    elements, edges = _fixture()
    G = graph_from(elements, edges)
    out = tmp_path / "code_graph.json"
    save_graph(G, out)

    assert out.exists()
    G2 = load_graph(out)

    assert set(G2.nodes) == set(G.nodes)
    assert G2.number_of_edges() == G.number_of_edges()
    assert G2.nodes["a_id"]["name"] == "a"
    assert G2.edges["a_id", "b_id"]["confidence"] == EXTRACTED


def test_query_callers_and_callees():
    elements, edges = _fixture()
    G = graph_from(elements, edges)

    assert [c["id"] for c in callees_of(G, "a_id")] == ["b_id"]
    assert [c["id"] for c in callers_of(G, "b_id")] == ["a_id"]
    assert callers_of(G, "a_id") == []
    assert callees_of(G, "b_id") == []


def test_find_node_by_name():
    elements, edges = _fixture()
    G = graph_from(elements, edges)
    assert find_node_by_name(G, "a") == ["a_id"]
    assert find_node_by_name(G, "nonexistent") == []


def test_neighbors_depth_one():
    elements, edges = _fixture()
    G = graph_from(elements, edges)
    assert set(neighbors(G, "a_id", depth=1)) == {"b_id"}
    assert neighbors(G, "nonexistent", depth=1) == []
