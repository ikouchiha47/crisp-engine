"""Tree-sitter/regex structural indexing — turning a file into code_index
episodes. Deliberately has nothing to do with consolidation: code_index*
episodes are structural/searchable/graph-eligible, never a "lesson"
(see lib/memory_policy.py).
"""
from pathlib import Path
from typing import Any, Dict, List

from lib.lang_detect import is_source_extension
from lib.log import get_logger as _get_logger
from lib.store import MemoryEpisode, MemoryStore, is_code_index_category

from ..episode_writer import EpisodeWriter

_log = _get_logger("hooks")

# Directories skipped during a repo-wide walk (SessionStart eager index).
IGNORE_DIRS = {".git", "node_modules", "android", "ios", "build", "dist",
               ".venv", "venv", "__pycache__", ".next", "target"}


class StructuralIndexer:
    """Lazy (PostToolUse Read) and eager (SessionStart) structural indexing."""

    def __init__(self, store: MemoryStore, writer: EpisodeWriter):
        self.store = store
        self.writer = writer

    def is_indexed_fresh(self, file_path_str: str) -> bool:
        """True if file already has a non-stale code-index episode."""
        return any(
            ep.source_path == file_path_str
            and is_code_index_category(ep.category)
            and "stale" not in (ep.tags or [])
            for ep in self.store.list_episodes()
        )

    def index_file(self, file_path: Path, session_id: str, registry=None) -> Dict[str, Any]:
        """Structural index of a single file: symbols only, no semantic summary.

        Shared by the lazy PostToolUse(Read) path and the eager SessionStart
        walk so there is exactly one code path that turns a file into
        code_index episodes. No directory placeholder stubs are created here
        (removed per docs/next-steps-sequence.md Phase 1.3 — they were pure
        landfill: "DIR: ... (placeholder — awaiting semantic summary from
        /index skill)" that the /index skill never actually filled in).
        """
        if registry is None:
            from lib.indexers import IndexerRegistry
            registry = IndexerRegistry()

        indexer = registry.get_indexer(file_path)
        if indexer is None:
            return {"status": "ignored", "reason": "no indexer"}

        result = indexer.index(file_path)
        episodes = indexer.extract_episodes(result)
        saved = 0
        for ep_data in episodes:
            ep = MemoryEpisode(session_id=session_id, **ep_data)
            if self.writer.save(ep):
                saved += 1

        return {"status": "indexed", "file": str(file_path), "episodes": saved}

    def handle_claude_post_read(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """PostToolUse(Read) — lazy structural index if file not yet indexed."""
        tool_input = data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")
        if not file_path or not is_source_extension(Path(file_path).suffix):
            return {"status": "ignored", "reason": "not a source file"}

        file_path_str = str(Path(file_path).resolve())
        if self.is_indexed_fresh(file_path_str):
            return {"status": "ignored", "reason": "already indexed"}

        try:
            return self.index_file(Path(file_path), data.get("session_id", "unknown"))
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def run_session_start_walk(self, project_root: Path, session_id: str, max_files: int = 500) -> Dict[str, Any]:
        """Eager, whole-repo structural index.

        Walks the project once so retrieval has L0 code_index episodes before
        Claude reads anything, instead of only building them up reactively as
        files happen to get Read/Edited during the session. Bounded by
        max_files per invocation so a huge repo can't stall session start;
        re-running (e.g. next session) picks up where it left off since
        already-fresh files are skipped via is_indexed_fresh.
        """
        from lib.indexers import IndexerRegistry
        registry = IndexerRegistry()

        indexed = 0
        skipped_fresh = 0
        errors: List[str] = []
        capped = False

        for file_path in project_root.rglob("*"):
            if not file_path.is_file() or not is_source_extension(file_path.suffix):
                continue
            if any(part in IGNORE_DIRS for part in file_path.parts):
                continue

            if self.is_indexed_fresh(str(file_path.resolve())):
                skipped_fresh += 1
                continue

            if indexed >= max_files:
                capped = True
                break

            try:
                result = self.index_file(file_path, session_id, registry=registry)
                if result.get("status") == "indexed":
                    indexed += 1
            except Exception as e:
                errors.append(f"{file_path}: {e}")

        return {
            "indexed": indexed,
            "skipped_fresh": skipped_fresh,
            "errors": errors[:10],
            "error_count": len(errors),
            "capped": capped,
        }

    def mark_index_stale(self, file_path: str) -> None:
        """Mark all code_index episodes for this file as stale."""
        file_path_str = str(Path(file_path).resolve())
        for ep in self.store.list_episodes():
            if ep.source_path == file_path_str and is_code_index_category(ep.category):
                if "stale" not in (ep.tags or []) and not ep.is_permanent:
                    ep.tags = list(ep.tags or []) + ["stale"]
                    self.store.delete_episode(ep.id)
                    self.writer.save(ep)
