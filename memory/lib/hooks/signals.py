"""Correction/frustration/tool-failure signal detection, plus the internal
Crisp Engine FileChange/ToolFailure handlers.

These heuristics (regex patterns on the user's message) are the *current*
correction/frustration detector — a stopgap until lib/affect.py (Phase 3)
does this properly via local-model distillation instead of regex.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
import json
import re

from lib.code_index import CodeAnalyzer
from lib.lang_detect import is_source_extension
from lib.log import bind as _log_bind
from lib.store import MemoryEpisode, MemoryStore

from ..episode_writer import EpisodeWriter


class SignalDetector:
    """Detects corrections/frustration/failures and saves them as L0 episodes."""

    def __init__(self, store: MemoryStore, writer: EpisodeWriter):
        self.store = store
        self.writer = writer
        self.analyzer = CodeAnalyzer()

    def handle_claude_stop(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Translate Stop payload -> Stop handler."""
        return self.handle_stop({
            "session_id": data.get("session_id", "unknown"),
            "cwd": data.get("cwd") or data.get("project_dir") or "",
            "message": data.get("message", ""),
            "tool_outputs": data.get("tool_outputs", []),
        })

    def handle_stop(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle Stop — detect corrections and frustration, save as L0."""
        session_id = event_data.get("session_id", "unknown")
        cwd = event_data.get("cwd", "")
        message = event_data.get("message", "")
        tool_outputs = event_data.get("tool_outputs", [])
        log = _log_bind(session_id=session_id, project=cwd, name="hooks")
        log.debug("Stop message_len=%d tool_outputs=%d", len(message), len(tool_outputs))
        result: Dict[str, Any] = {"event": "Stop"}

        correction = self._detect_correction(message, tool_outputs)
        if correction:
            ep = self._create_correction_episode(correction, event_data)
            self.writer.save(ep)
            result["correction"] = {"episode_id": ep.id}

        frustration = self._detect_frustration(message)
        if frustration:
            ep = self._create_frustration_episode(frustration, event_data)
            self.writer.save(ep)
            result["frustration"] = {"episode_id": ep.id}

        failures = self._detect_tool_failures(tool_outputs)
        for failure in failures:
            ep = self._create_failure_episode(failure, event_data)
            self.writer.save(ep)
        if failures:
            result["failures"] = len(failures)

        return result

    def handle_file_change(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle FileChange (internal Crisp Engine format) — capture diff + symbols."""
        file_path = event_data.get("file_path", "")
        if not file_path:
            return {"event": "FileChange", "error": "no file_path"}

        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="replace")
            content_hash = self.store.compute_hash(content)
        except Exception as e:
            return {"event": "FileChange", "error": str(e)}

        if self.store.get_file_state(file_path) == content_hash:
            return {"event": "FileChange", "unchanged": True}

        diff = event_data.get("diff", "")
        change_type = event_data.get("change_type", "edit")
        session_id = event_data.get("session_id", "unknown")

        code_elements = []
        if is_source_extension(Path(file_path).suffix):
            try:
                code_elements = self.analyzer.analyze_file(file_path)
            except Exception:
                pass

        lines = [f"File changed: {file_path}", f"Change type: {change_type}", ""]
        if diff:
            lines += ["## Diff", f"```diff\n{diff[:2000]}\n```", ""]
        if code_elements:
            lines += ["## Code Elements"] + [
                f"- `{e.signature}` ({e.type})" for e in code_elements[:15]
            ] + [""]

        episode_id = f"file_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        episode = MemoryEpisode(
            id=episode_id,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0,
            title=f"File change: {Path(file_path).name}",
            content="\n".join(lines),
            source_type="file",
            source_path=file_path,
            source_hash=content_hash,
            category="code",
            importance=0.6,
            tags=["file_change", Path(file_path).suffix.lstrip(".")],
            context_snapshot={
                "change_type": change_type,
                "code_elements": len(code_elements),
                "diff_size": len(diff),
            },
        )

        saved = self.writer.save(episode)
        self.store.set_file_state(file_path, content_hash)
        return {"event": "FileChange", "episode_id": episode_id if saved else None, "duplicate": not saved}

    def handle_tool_failure(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle ToolFailure (internal Crisp Engine format) — high-importance L0 episode."""
        tool_name = event_data.get("tool_name", "unknown")
        error = event_data.get("error", "")
        tool_input = event_data.get("tool_input", {})

        content = f"Tool failure: {tool_name}\n\nError: {error}\n"
        if tool_input:
            content += f"\nInput: {json.dumps(tool_input, indent=2)}\n"

        episode_id = f"failure_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        episode = MemoryEpisode(
            id=episode_id,
            session_id=event_data.get("session_id", "unknown"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0,
            title=f"Tool failure: {tool_name}",
            content=content,
            source_type="tool",
            category="failure",
            importance=0.8,
            tags=["failure", tool_name],
            trigger_type="error_recovery",
            frustration_score=0.7,
            context_snapshot={"tool_name": tool_name, "error": error},
        )
        self.writer.save(episode)
        return {"event": "ToolFailure", "episode_id": episode_id}

    def _detect_correction(self, message: str, tool_outputs: list) -> Optional[Dict]:
        msg = message.lower()
        patterns = [
            r"\bno\b", r"\bwrong\b", r"\bdon'?t\b", r"\bincorrect\b",
            r"\bthat'?s wrong\b", r"\bnot right\b", r"\bfix\b",
        ]
        for p in patterns:
            if re.search(p, msg):
                return {"type": "explicit", "message": message, "pattern": p}
        for output in tool_outputs:
            if isinstance(output, dict) and output.get("error"):
                return {"type": "tool_error", "error": output["error"]}
        return None

    def _detect_frustration(self, message: str) -> Optional[Dict]:
        msg = message.lower()
        patterns = [r"\bugh\b", r"\bagain\b", r"\bstill\b", r"\bnot working\b",
                    r"\bdoesn'?t work\b", r"\bargh\b", r"\bfrustrat\b", r"\bannoying\b"]
        matches = [p for p in patterns if re.search(p, msg)]
        if matches:
            return {"patterns": matches, "message": message, "score": min(1.0, len(matches) * 0.3)}
        return None

    def _detect_tool_failures(self, tool_outputs: list) -> list:
        return [o for o in tool_outputs
                if isinstance(o, dict) and (o.get("error") or o.get("status") == "error")]

    def _create_correction_episode(self, correction: Dict, event_data: Dict) -> MemoryEpisode:
        delta = correction.get("message") or correction.get("error", "")
        return MemoryEpisode(
            id=f"correction_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            session_id=event_data.get("session_id", "unknown"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0, title="Correction applied",
            content=f"User correction: {delta}",
            category="correction", importance=1.0,
            tags=["correction", "learning"],
            correction_applied=True, correction_delta=delta,
            is_permanent=True, trigger_type="reaction",
            user_sentiment="negative",
            lesson=f"Learned from correction: {delta[:100]}",
        )

    def _create_frustration_episode(self, frustration: Dict, event_data: Dict) -> MemoryEpisode:
        return MemoryEpisode(
            id=f"frustration_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            session_id=event_data.get("session_id", "unknown"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0, title="User frustration detected",
            content=f"Signals: {', '.join(frustration['patterns'])}\n\n{frustration['message']}",
            category="frustration", importance=0.7,
            tags=["frustration"], frustration_score=frustration["score"],
            user_sentiment="negative", trigger_type="reaction",
            lesson="User experienced frustration — consider different approach",
        )

    def _create_failure_episode(self, failure: Dict, event_data: Dict) -> MemoryEpisode:
        tool_name = failure.get("tool_name", "unknown")
        error = failure.get("error", "")
        return MemoryEpisode(
            id=f"failure_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            session_id=event_data.get("session_id", "unknown"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0, title=f"Tool failure: {tool_name}",
            content=f"Error: {error}\nTool: {tool_name}",
            category="failure", importance=0.8,
            tags=["failure", tool_name],
            trigger_type="error_recovery", root_cause=error,
            lesson=f"Tool {tool_name} failed — investigate root cause",
        )
