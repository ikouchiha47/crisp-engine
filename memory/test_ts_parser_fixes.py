"""Real assertions against lib.ts_parser.parse_file using actual installed
tree-sitter grammars (python, typescript, cpp) — not mocked.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from lib.code_index.treesitter_strategy import parse_file


def _write(tmp_dir: Path, name: str, content: str) -> Path:
    p = tmp_dir / name
    p.write_text(content)
    return p


def test_hash_changes_when_edit_is_past_2000_chars():
    tmp = Path(tempfile.mkdtemp())

    # Body padded so the real edit sits well past the 2000-char truncation
    # point that body/full_content get capped to.
    padding = "\n".join(f"    x{i} = {i}" for i in range(400))  # >2000 chars

    source_before = f"def big_function():\n{padding}\n    return 1\n"
    source_after = f"def big_function():\n{padding}\n    return 2\n"  # tail edit

    f1 = _write(tmp, "before.py", source_before)
    elems_before = parse_file(f1)
    assert elems_before, "expected at least one element parsed"
    fn_before = next(e for e in elems_before if e.name == "big_function")

    f2 = _write(tmp, "after.py", source_after)
    elems_after = parse_file(f2)
    fn_after = next(e for e in elems_after if e.name == "big_function")

    assert len(fn_before.body) >= 2000 - 5  # confirms body really is near/at the cap
    assert fn_before.body == fn_after.body, "truncated body should look identical (both capped)"
    assert fn_before.hash != fn_after.hash, (
        "hash did not change for an edit past char 2000 — "
        "it was computed from the truncated body instead of the full one"
    )


def test_tsx_language_field_matches_analyzer_canonical_naming():
    tmp = Path(tempfile.mkdtemp())
    f = _write(tmp, "component.tsx", "function Widget() {\n  return null;\n}\n")
    elems = parse_file(f)
    assert elems, "expected at least one element parsed from .tsx"
    assert elems[0].language == "typescript", (
        f"tree-sitter emitted {elems[0].language!r} for .tsx — should be normalized "
        "to 'typescript' to match analyzer.py's regex-fallback naming"
    )


def test_cpp_language_field_matches_analyzer_canonical_naming():
    tmp = Path(tempfile.mkdtemp())
    f = _write(tmp, "thing.cpp", "class Widget {\npublic:\n  int render();\n};\n")
    elems = parse_file(f)
    assert elems, "expected at least one element parsed from .cpp"
    langs = {e.language for e in elems}
    assert langs == {"c_cpp"}, (
        f"tree-sitter emitted {langs} for .cpp — should be normalized to 'c_cpp' "
        "to match analyzer.py's regex-fallback naming"
    )
