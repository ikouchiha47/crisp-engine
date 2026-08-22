"""Real assertions against lib.code_index.treesitter_strategy.repo_grammar_status
— checked against this machine's actual tree-sitter-language-pack install
(371 languages) and Linguist data, not guessed or hand-typed.
"""
from __future__ import annotations

from lib.code_index.treesitter_strategy import repo_grammar_status


def test_originally_hand_typed_language_reports_installed():
    rows = repo_grammar_status({"Python"})
    assert rows == [{"language": "Python", "status": "installed", "pip_package": "tree-sitter-language-pack"}]


def test_language_beyond_the_old_13_is_now_installed_via_the_language_pack():
    # Zig was never in the old 13-language GRAMMAR_REGISTRY/EXT_TO_GRAMMAR —
    # this is the actual point of adopting tree-sitter-language-pack: real
    # coverage beyond what anyone hand-typed.
    rows = repo_grammar_status({"Zig", "Elixir", "Haskell", "Dart", "Lua"})
    statuses = {r["language"]: r["status"] for r in rows}
    assert statuses == {
        "Zig": "installed",
        "Elixir": "installed",
        "Haskell": "installed",
        "Dart": "installed",
        "Lua": "installed",
    }


def test_language_the_pack_genuinely_does_not_cover_reports_unsupported():
    # Confirmed for real (not guessed): '1C Enterprise' is a real Linguist
    # programming language whose normalized name isn't in the pack's
    # SupportedLanguage set.
    rows = repo_grammar_status({"1C Enterprise"})
    assert rows == [{"language": "1C Enterprise", "status": "unsupported", "pip_package": None}]


def test_mixed_repo_reports_each_language_correctly():
    rows = repo_grammar_status({"Python", "Zig", "1C Enterprise"})
    by_lang = {r["language"]: r["status"] for r in rows}
    assert by_lang == {
        "Python": "installed",
        "Zig": "installed",
        "1C Enterprise": "unsupported",
    }
