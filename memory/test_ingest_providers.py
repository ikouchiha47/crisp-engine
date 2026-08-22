"""Provider parsing tests (ADR-0001 §17)."""

from __future__ import annotations

import pytest

from lib.ingest.ids import turn_id
from lib.ingest.providers import (
    ClaudeJSONLProvider,
    OpenCodeSQLiteProvider,
    _flatten_content,
)
from lib.ingest.types import IngestConfig


def _claude_cfg(root) -> IngestConfig:
    return IngestConfig(claude_projects_root=str(root), providers=("claude_code",))


class TestClaudeParse:
    def test_parse_turns_and_stable_ids(self, claude_fixture):
        cfg = _claude_cfg(claude_fixture.parent)
        provider = ClaudeJSONLProvider()
        refs = list(provider.discover(cfg))
        assert len(refs) == 1
        s1 = provider.parse(cfg, refs[0])
        s2 = provider.parse(cfg, refs[0])

        assert s1.n_user == 3
        assert s1.n_assistant == 2
        assert s1.provider == "claude_code"
        assert s1.project_path == "/example/project"
        assert s1.title == "fix logic error"
        assert s1.model_hints == ("claude-sonnet-4-6",)
        assert [t.turn_id for t in s1.turns] == [t.turn_id for t in s2.turns]
        assert s1.turns[1].parent_turn_id == s1.turns[0].turn_id

    def test_meta_excluded_from_mood(self, claude_fixture):
        # fixture has no isMeta user; verify _flatten_content handles meta
        _text, _tools, kind = _flatten_content("WATCH OUT", True)
        assert kind == "meta"


class TestOpenCodeParse:
    def test_joins_parts_and_tools(self, opencode_fixture):
        cfg = IngestConfig(opencode_db=str(opencode_fixture), providers=("opencode",))
        provider = OpenCodeSQLiteProvider()
        refs = list(provider.discover(cfg))
        assert refs == ["ses_abc"]
        s = provider.parse(cfg, "ses_abc")
        assert s.provider == "opencode"
        assert s.project_path == "/example/project"
        assert s.total_cost == 0.5
        assert s.tokens_in == 1000
        assert s.todos_all_done is True
        assert s.work_landed is True  # summary_additions=10
        assert any(t.text == "make it faster" for t in s.turns)
        assert any(t.text == "Done, sped up 2x." for t in s.turns)
        # models parsed from session JSON
        assert "grok-4.5" in s.model_hints

    def test_missing_db_raises(self, tmp_path):
        cfg = IngestConfig(opencode_db=str(tmp_path / "nope.db"), providers=("opencode",))
        with pytest.raises(FileNotFoundError):
            list(OpenCodeSQLiteProvider().discover(cfg))

    def test_stable_turn_id(self):
        assert turn_id("opencode", "s", "m") == turn_id("opencode", "s", "m")
        assert turn_id("opencode", "s", "m") != turn_id("claude_code", "s", "m")