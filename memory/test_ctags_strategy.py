"""Real assertions against lib.code_index.ctags_strategy and the fixed
fallback-chain orchestrator, using an actually installed universal-ctags
(verified: Universal Ctags 6.2.1) — not mocked, not guessed.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from lib.code_index import CodeAnalyzer
from lib.code_index.ctags_strategy import has_ctags, parse_file


def test_has_ctags_detects_the_real_installed_binary():
    assert has_ctags(), "universal-ctags should be detected on this machine"


def test_python_class_method_and_function_are_extracted():
    tmp = Path(tempfile.mkdtemp())
    f = tmp / "sample.py"
    f.write_text("class Foo:\n    def method_a(self, x):\n        return x\n\ndef top_level(y):\n    return y\n")

    elements = parse_file(f)
    assert elements is not None
    by_name = {e.name: e for e in elements}
    assert by_name["Foo"].type == "class"
    assert by_name["method_a"].type == "method"
    assert by_name["top_level"].type == "function"
    assert all(e.language == "python" for e in elements)


def test_go_struct_field_is_excluded_but_method_and_struct_are_kept():
    # The exact case that required the `signature`-field heuristic instead
    # of trusting `kind` alone: Go tags a struct field "member" (same kind
    # Python uses for methods) but only the real method has `signature`.
    tmp = Path(tempfile.mkdtemp())
    f = tmp / "sample.go"
    f.write_text(
        "package main\n\ntype Widget struct {\n\tName string\n}\n\n"
        "func (w *Widget) Render() string {\n\treturn w.Name\n}\n"
    )

    elements = parse_file(f)
    assert elements is not None
    names = {e.name for e in elements}
    assert "Name" not in names, f"struct field should be excluded, got {names}"
    assert "Widget" in names
    assert "Render" in names
    render = next(e for e in elements if e.name == "Render")
    assert render.type == "method"


def test_language_with_no_walk_dispatch_falls_through_treesitter_to_ctags():
    # Ruby: tree-sitter grammar loads via the language pack, but _walk()
    # has zero dispatch rules for it -> must fall through to ctags, not
    # silently return the [] tree-sitter produces. This is the orchestrator
    # fix (truthy check instead of `is not None`), exercised end-to-end.
    tmp = Path(tempfile.mkdtemp())
    f = tmp / "sample.rb"
    f.write_text("class Widget\n  def render(x)\n    x\n  end\nend\n\ndef top_level(y)\n  y\nend\n")

    analyzer = CodeAnalyzer()
    elements = analyzer.analyze_file(str(f))
    names = {e.name for e in elements}
    assert names == {"Widget", "render", "top_level"}, (
        f"expected ctags fallback to recover Ruby symbols, got {names}"
    )
