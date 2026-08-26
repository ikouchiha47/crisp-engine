"""Walks a CodeElement's already-extracted body for call expressions.

Re-parses `elem.body` standalone with the same tree-sitter grammar used by
treesitter_strategy.py (rather than threading a second walk through the
original file parse) — keeps this module decoupled from treesitter_strategy's
internals and testable with bare source strings.
"""
from pathlib import Path
from typing import List

from ..code_index import CodeElement
from ..code_index.treesitter_strategy import _load_grammar  # noqa: F401 (internal, same codebase)

# CodeElement.language is already canonicalized (tsx/jsx -> javascript/typescript,
# c/cpp -> c_cpp) by treesitter_strategy._make_elem. Map back to a parseable
# grammar key for standalone re-parsing of the body text.
_LANGUAGE_TO_GRAMMAR = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "go": "go",
    "c_cpp": "cpp",  # cpp grammar parses plain C well enough for call-node walking
    "rust": "rust",
    "java": "java",
}

# tree-sitter node type that represents a function/method call, per grammar.
_CALL_NODE_TYPE = {
    "python": "call",
    "javascript": "call_expression",
    "typescript": "call_expression",
    "go": "call_expression",
    "cpp": "call_expression",
    "rust": "call_expression",
    "java": "method_invocation",
}


def _callee_name(func_node, source: bytes) -> str:
    """Extract the plain callee identifier from a call's function/name child.

    `a.b.c()` and `obj->method()` resolve to the rightmost name (`c`,
    `method`) — good enough for name-based symbol resolution; we are not
    doing type inference.
    """
    t = func_node.type
    if t in ("identifier", "field_identifier"):
        return source[func_node.start_byte:func_node.end_byte].decode("utf-8", errors="replace")
    # attribute (py: obj.attr), member_expression (js/ts: obj.attr),
    # field_access / pointer_expression (c/cpp: obj.attr / obj->attr),
    # selector_expression (go: obj.attr)
    name_field = (
        func_node.child_by_field_name("attribute")
        or func_node.child_by_field_name("property")
        or func_node.child_by_field_name("field")
        or func_node.child_by_field_name("name")
    )
    if name_field is not None:
        return source[name_field.start_byte:name_field.end_byte].decode("utf-8", errors="replace")
    # Fallback: last identifier-like leaf in the subtree.
    last = None
    stack = [func_node]
    while stack:
        n = stack.pop()
        if n.type in ("identifier", "field_identifier"):
            last = n
        stack.extend(n.children)
    if last is not None:
        return source[last.start_byte:last.end_byte].decode("utf-8", errors="replace")
    return ""


def _walk_calls(node, source: bytes, call_node_type: str, out: List[str]) -> None:
    if node.type == call_node_type:
        func_node = node.child_by_field_name("function") or node.child_by_field_name("name")
        if func_node is not None:
            name = _callee_name(func_node, source)
            if name:
                out.append(name)
    for child in node.children:
        _walk_calls(child, source, call_node_type, out)


def extract_calls_from_source(body_source: str, language: str) -> List[str]:
    """Parse a standalone body string and return callee names in call order.

    Returns [] for unsupported languages or parse failures — never raises,
    matching the fallback-chain convention used throughout code_index/.
    """
    grammar_key = _LANGUAGE_TO_GRAMMAR.get(language)
    call_node_type = _CALL_NODE_TYPE.get(grammar_key)
    if not grammar_key or not call_node_type or not body_source.strip():
        return []

    lang = _load_grammar(grammar_key)
    if lang is None:
        return []

    try:
        from tree_sitter import Parser
        source = body_source.encode("utf-8")
        parser = Parser(lang)
        tree = parser.parse(source)
    except Exception:
        return []

    calls: List[str] = []
    _walk_calls(tree.root_node, source, call_node_type, calls)
    return calls


def _read_full_element_source(elem: CodeElement) -> str:
    """Re-read an element's own line range straight from disk.

    `elem.body`/`elem.full_content` are hard-truncated to 2000 chars by
    treesitter_strategy._make_elem — fine for a preview, wrong for call
    extraction: a call sitting past character 2000 of a large function
    (main()-style dispatchers routinely are) would otherwise be silently
    invisible to the graph. Re-reading from disk removes the cap; returns
    "" (not an exception) when the file isn't there or the range is stale,
    so callers can fall back to the truncated body.
    """
    try:
        path = Path(elem.file_path)
        if not path.is_file():
            return ""
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        start, end = elem.start_line, elem.end_line
        if start < 0 or end >= len(lines) or start > end:
            return ""
        return "".join(lines[start:end + 1])
    except Exception:
        return ""


def walk_calls(elements: List[CodeElement]) -> None:
    """Populate `elem.calls` in place for every element with a body.

    Prefers the untruncated on-disk source for the element's line range;
    falls back to the (possibly truncated) stored body/full_content when
    the file isn't reachable — e.g. elements built from a temp file in
    tests, or a file that's since moved.
    """
    for elem in elements:
        body = _read_full_element_source(elem) or elem.body or elem.full_content
        if not body:
            continue
        elem.calls = extract_calls_from_source(body, elem.language)


def parse_source_and_walk_calls(source: str, language: str, file_path: str) -> List[CodeElement]:
    """Test/CLI convenience: parse a whole file's source (function defs +
    their calls) in one call, for callers that don't already have
    CodeElements from treesitter_strategy.parse_file (which requires a real
    file on disk).
    """
    import hashlib
    import tempfile
    from pathlib import Path

    from .. import code_index  # noqa: F401 ensure package import order
    from ..code_index.treesitter_strategy import parse_file as _parse_file

    suffix = {
        "python": ".py", "javascript": ".js", "typescript": ".ts",
        "go": ".go", "c_cpp": ".cpp", "rust": ".rs", "java": ".java",
    }.get(language, ".txt")

    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as f:
        f.write(source)
        tmp_path = Path(f.name)

    try:
        elements = _parse_file(tmp_path) or []
        # parse_file records the temp path; overwrite with the caller's
        # intended logical file_path so ids/paths are stable for tests.
        for e in elements:
            e.file_path = file_path
            e.id = f"ts_{e.name}_{hashlib.md5(f'{file_path}:{e.name}:{e.start_line}'.encode()).hexdigest()[:8]}"
        walk_calls(elements)
        return elements
    finally:
        tmp_path.unlink(missing_ok=True)
