"""Black-box tests for lib/graph/callwalk.py and lib/graph/resolve.py.

Each assertion hardcodes the exact expected value — no re-deriving the
expectation from the code under test, no `assert len(x) > 0` tautologies.
"""
from pathlib import Path

from lib.code_index import CodeElement
from lib.code_index.treesitter_strategy import parse_file
from lib.graph.callwalk import extract_calls_from_source, parse_source_and_walk_calls, walk_calls
from lib.graph.resolve import AMBIGUOUS, EXTRACTED, INFERRED, Edge, resolve


def test_callwalk_extracts_direct_calls_python():
    body = "    foo()\n    bar(1, 2)\n"
    assert extract_calls_from_source(body, "python") == ["foo", "bar"]


def test_callwalk_extracts_method_calls_javascript():
    body = "  obj.method();\n  standalone();\n"
    assert extract_calls_from_source(body, "javascript") == ["method", "standalone"]


def test_callwalk_no_calls_returns_empty_list():
    body = "    x = 1\n    return x\n"
    assert extract_calls_from_source(body, "python") == []


def test_callwalk_unsupported_language_returns_empty_list():
    assert extract_calls_from_source("foo()", "cobol") == []


def test_walk_calls_finds_call_past_2000_char_truncation_boundary(tmp_path: Path):
    # Reproduces the real bug: a call sitting past character 2000 of a large
    # function body was invisible because callwalk used to re-parse the
    # truncated `elem.body` (treesitter_strategy hard-caps it at [:2000]).
    padding = "    x = 1\n" * 250  # > 2000 chars of filler before the real call
    src = "def big():\n" + padding + "    late_call()\n"
    assert len(src) > 2100

    f = tmp_path / "big.py"
    f.write_text(src)
    elements = parse_file(f)
    big = next(e for e in elements if e.name == "big")

    # the stored/truncated body does NOT contain the call (confirms the bug exists)
    assert "late_call" not in big.body

    walk_calls(elements)

    # but walk_calls (reading the untruncated file) still finds it
    assert "late_call" in big.calls


def test_parse_source_and_walk_calls_populates_elements():
    src = (
        "def a():\n"
        "    foo()\n"
        "    bar()\n\n"
        "def foo():\n"
        "    pass\n"
    )
    elements = parse_source_and_walk_calls(src, "python", "a.py")
    by_name = {e.name: e for e in elements}
    assert by_name["a"].calls == ["foo", "bar"]
    assert by_name["foo"].calls == []


# ── resolve.py ───────────────────────────────────────────────────────────

def _elem(id_, name, file_path, calls=None):
    return CodeElement(
        id=id_, name=name, type="function", language="python",
        file_path=file_path, start_line=1, end_line=2,
        calls=calls or [],
    )


def test_resolve_same_file_is_extracted():
    a = _elem("a_id", "a", "x.py", calls=["b"])
    b = _elem("b_id", "b", "x.py")
    edges = resolve([a, b])
    assert edges == [Edge("a_id", "b_id", "calls", EXTRACTED)]


def test_resolve_cross_file_is_inferred():
    a = _elem("a_id", "a", "file1.py", calls=["c"])
    c = _elem("c_id", "c", "file2.py")
    edges = resolve([a, c])
    assert len(edges) == 1
    assert edges[0].source == "a_id"
    assert edges[0].target == "c_id"
    assert edges[0].confidence == INFERRED


def test_resolve_duplicate_name_is_ambiguous():
    caller = _elem("caller_id", "caller", "main.py", calls=["d"])
    d1 = _elem("d1_id", "d", "file1.py")
    d2 = _elem("d2_id", "d", "file2.py")
    edges = resolve([caller, d1, d2])
    assert len(edges) == 1
    assert edges[0].confidence == AMBIGUOUS
    assert edges[0].source == "caller_id"
    assert edges[0].target == "d1_id"  # deterministic: lexicographically smallest id


def test_resolve_unknown_call_is_dropped():
    a = _elem("a_id", "a", "x.py", calls=["print"])  # stdlib, no matching element
    edges = resolve([a])
    assert edges == []


def test_resolve_same_file_ambiguous_beats_cross_file_inferred():
    # two `e` definitions in the caller's own file -> AMBIGUOUS, even though
    # a third `e` exists elsewhere that alone would be INFERRED.
    caller = _elem("caller_id", "caller", "x.py", calls=["e"])
    e1 = _elem("e1_id", "e", "x.py")
    e2 = _elem("e2_id", "e", "x.py")
    e3 = _elem("e3_id", "e", "other.py")
    edges = resolve([caller, e1, e2, e3])
    assert len(edges) == 1
    assert edges[0].confidence == AMBIGUOUS
    assert edges[0].target == "e1_id"
