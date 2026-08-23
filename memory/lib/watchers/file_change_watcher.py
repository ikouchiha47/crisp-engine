"""FileChangeWatcher — captures Write/Edit/MultiEdit as code episodes."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lib.log import get_logger
from lib.store import MemoryEpisode
from lib.lang_detect import is_source_extension

_log = get_logger("watchers.file_change")


class FileChangeWatcher:
    name = "file_change"
    tool_names = ["Write", "Edit", "MultiEdit"]

    def __init__(self):
        from lib.code_index import CodeAnalyzer
        self._analyzer = CodeAnalyzer()

    def matches(self, tool_name: str, inp: dict, out: dict) -> bool:
        if tool_name not in self.tool_names:
            return False
        file_path = inp.get("file_path", "")
        if not file_path:
            return False
        return is_source_extension(Path(file_path).suffix)

    def episodes(
        self,
        tool_name: str,
        inp: dict,
        out: dict,
        session_id: str,
        project_root: str,
    ) -> list[MemoryEpisode]:
        file_path = inp.get("file_path", "")
        if not file_path:
            return []

        _log.debug("file_change: file=%s tool=%s session=%s", file_path, tool_name, session_id,
                   extra={"session_id": session_id, "project": project_root})
        diff = self._git_diff(file_path, project_root)
        change_type = "create" if tool_name == "Write" else "edit"

        code_elements = []
        try:
            code_elements = self._analyzer.analyze_file(file_path)
        except Exception:
            pass

        lines = [f"File changed: {file_path}", f"Change type: {change_type}", ""]
        if diff:
            lines += ["## Diff", f"```diff\n{diff[:2000]}\n```", ""]
        if code_elements:
            lines += ["## Code Elements"] + [
                f"- `{e.signature}` ({e.type})" for e in code_elements[:15]
            ] + [""]

        ep_id = f"file_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        try:
            content_hash = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            content_hash = ""

        _log.debug("file_change: diff_len=%d symbols=%d ep=%s",
                   len(diff), len(code_elements), ep_id,
                   extra={"session_id": session_id, "project": project_root})
        return [MemoryEpisode(
            id=ep_id,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0,
            title=f"File change: {Path(file_path).name}",
            content="\n".join(lines),
            source_type="file",
            source_path=file_path,
            category="code",
            importance=0.6,
            tags=["file_change", Path(file_path).suffix.lstrip(".")],
            context_snapshot={
                "change_type": change_type,
                "code_elements": len(code_elements),
                "diff_size": len(diff),
            },
        )]

    def _git_diff(self, file_path: str, cwd: str) -> str:
        try:
            r = subprocess.run(
                ["git", "diff", "HEAD", "--", file_path],
                capture_output=True, text=True, timeout=5, cwd=cwd or str(Path(file_path).parent),
            )
            diff = r.stdout.strip()
            if not diff:
                r = subprocess.run(
                    ["git", "diff", "--", file_path],
                    capture_output=True, text=True, timeout=5, cwd=cwd or str(Path(file_path).parent),
                )
                diff = r.stdout.strip()
            return diff
        except Exception:
            return ""
