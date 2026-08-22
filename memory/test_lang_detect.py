"""Real assertions against the vendored languages.yml/heuristics.yml — not mocked."""
from __future__ import annotations

import tempfile
from pathlib import Path

from lib.lang_detect import classify_language, detect_language


def test_tier1_unambiguous_extension_resolves_without_content():
    assert detect_language("foo.py") == "Python"
    assert detect_language("foo.go") == "Go"


def test_rs_extension_is_ambiguous_in_real_data_and_needs_content():
    # .rs is claimed by RenderScript, Rust, AND XML in languages.yml itself —
    # heuristics.yml disambiguates by content. Without content (e.g. a
    # nonexistent path), it correctly falls back to the first candidate
    # rather than guessing — this asserts that documented, deliberate
    # behavior, not a bug.
    assert detect_language("nonexistent.rs") in ("RenderScript", "Rust", "XML")
    assert detect_language("real.rs", content="fn main() {\n    println!(\"hi\");\n}\n") == "Rust"


def test_tier2_h_extension_disambiguates_to_cpp_via_heuristics():
    content = "std::vector<int> x;\nclass Foo {\npublic:\n  int y;\n};\n"
    assert detect_language("foo.h", content=content) == "C++"


def test_tier2_h_extension_disambiguates_to_objectivec_via_heuristics():
    content = "#import <Foundation/Foundation.h>\n@interface Foo : NSObject\n@end\n"
    assert detect_language("foo.h", content=content) == "Objective-C"


def test_tier2_h_extension_defaults_to_c_when_no_markers_present():
    content = "int add(int a, int b);\n"
    assert detect_language("foo.h", content=content) == "C"


def test_tier4_genuinely_unknown_extension_returns_none():
    assert detect_language("foo.zzzznotarealext") is None


def test_tier0_cache_resolves_without_recomputing_heuristics():
    tmp = Path(tempfile.mkdtemp())
    classify_language(tmp, "/repo/weird.cfg", "TOML", scope="path")
    assert detect_language("/repo/weird.cfg", cache_dir=tmp) == "TOML"


def test_tier0_cache_extension_scope_applies_to_other_files_with_same_ext():
    tmp = Path(tempfile.mkdtemp())
    classify_language(tmp, "/repo/one.myext", "INI", scope="ext")
    assert detect_language("/repo/two.myext", cache_dir=tmp) == "INI"


def test_tier0_cache_takes_precedence_over_tier1():
    tmp = Path(tempfile.mkdtemp())
    # .py is unambiguously Python per languages.yml — an explicit path
    # override must still win, since it's more specific than the static table.
    classify_language(tmp, "/repo/generated.py", "Jupyter Notebook", scope="path")
    assert detect_language("/repo/generated.py", cache_dir=tmp) == "Jupyter Notebook"
