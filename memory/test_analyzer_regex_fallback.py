"""Real assertions against RegexStrategy (no tree-sitter, testing the exact
code path that had the class-state-reset and JS
control-flow-misindexed-as-function bugs).
"""
from __future__ import annotations

from lib.code_index.regex_strategy import RegexStrategy


def test_function_after_a_class_is_not_dropped():
    source = '''\
class Foo:
    def method_a(self):
        pass

def top_level_function():
    return 42
'''
    strategy = RegexStrategy()
    elements = strategy._extract_python_structure(source.splitlines(keepends=True), "test.py")
    names = {e.name for e in elements}
    assert "Foo" in names
    assert "method_a" in names
    assert "top_level_function" in names, (
        f"top_level_function was dropped — got {names}. "
        "current_class was likely never reset after the class ended."
    )


def test_multiple_functions_after_multiple_classes_all_survive():
    source = '''\
class A:
    def a_method(self):
        pass

def between_one():
    pass

class B:
    def b_method(self):
        pass

def between_two():
    pass
'''
    strategy = RegexStrategy()
    elements = strategy._extract_python_structure(source.splitlines(keepends=True), "test.py")
    names = {e.name for e in elements}
    assert {"A", "a_method", "between_one", "B", "b_method", "between_two"} <= names, (
        f"missing symbols: got {names}"
    )


def test_js_control_flow_keywords_are_not_indexed_as_functions():
    source = '''\
function realFunction() {
  if (x) {
    doSomething();
  }
  while (y) {
    doOther();
  }
  try {
    risky();
  } catch (e) {
    handle(e);
  }
}
'''
    strategy = RegexStrategy()
    elements = strategy._extract_generic_structure(
        source.splitlines(keepends=True), "test.js", "javascript"
    )
    names = {e.name for e in elements}
    assert "if" not in names, f"'if' was indexed as a function symbol: {names}"
    assert "while" not in names, f"'while' was indexed as a function symbol: {names}"
    assert "catch" not in names, f"'catch' was indexed as a function symbol: {names}"


def test_js_real_bare_method_shaped_function_is_still_indexed():
    # Not wrapped in a class: _extract_generic_structure's class branch
    # swallows its whole body without sub-scanning for methods — a real,
    # separate, pre-existing bug (confirmed present before this fix too),
    # out of scope here. This test isolates just the keyword-exclusion
    # change: a bare, non-keyword, method-shaped line must still match.
    source = '''\
render(props) {
  return null;
}
'''
    strategy = RegexStrategy()
    elements = strategy._extract_generic_structure(
        source.splitlines(keepends=True), "test.js", "javascript"
    )
    names = {e.name for e in elements}
    assert "render" in names, f"a real method was lost by the keyword exclusion: {names}"
