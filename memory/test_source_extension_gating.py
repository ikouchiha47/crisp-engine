"""Real assertions that hooks.py's capture gating now derives from real
Linguist data instead of a hand-typed 11-entry set — e.g. .pyi (a real
Python extension) was invisible to the old SOURCE_EXTENSIONS set entirely.
"""
from __future__ import annotations

from lib.lang_detect import is_source_extension


def test_previously_hand_listed_extensions_still_gate_as_source():
    for ext in (".py", ".ts", ".js", ".go", ".rs"):
        assert is_source_extension(ext), f"{ext} should be a source extension"


def test_extension_missing_from_old_hand_typed_list_now_gates_correctly():
    # .pyi is real Python (type stubs) but was never in hooks.py's old
    # SOURCE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs",
    # ".c", ".cpp", ".h", ".ino"} — files like this were silently never
    # captured by any hook, with no error, no signal, nothing.
    assert is_source_extension(".pyi"), ".pyi should now be recognized via Linguist"


def test_non_source_extension_is_excluded():
    assert not is_source_extension(".png")
    assert not is_source_extension(".jpg")


def test_data_type_extensions_are_excluded_not_treated_as_source():
    # .json's Linguist type is "data", not "programming"/"markup" — capture
    # gating should not treat every structured-data file as source code.
    assert not is_source_extension(".json")
