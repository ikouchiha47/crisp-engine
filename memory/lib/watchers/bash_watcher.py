"""BashWatcher — captures failed Bash commands as friction episodes."""

from datetime import datetime, timezone

from lib.log import get_logger
from lib.store import MemoryEpisode

_log = get_logger("watchers.bash_failure")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BashWatcher:
    name = "bash_failure"
    tool_names = ["Bash"]

    # Higher-priority watchers (by name) that already handled git commands.
    # If those produced episodes for this call, we skip to avoid duplicates.
    # The registry passes handled_by so we can check.
    _skip_if_handled_by = {"git"}

    def matches(self, tool_name: str, inp: dict, out: dict) -> bool:
        if tool_name != "Bash":
            return False
        exit_code = out.get("exit_code")
        if exit_code is None or exit_code == 0:
            return False
        # Skip git commands — GitWatcher owns those
        cmd = inp.get("command", "").strip()
        if cmd.startswith("git "):
            return False
        return True

    def episodes(
        self,
        tool_name: str,
        inp: dict,
        out: dict,
        session_id: str,
        project_root: str,
    ) -> list[MemoryEpisode]:
        cmd = inp.get("command", "").strip()
        stderr = (out.get("stderr", "") or "")[:500]
        stdout = (out.get("stdout", "") or "")[:200]
        exit_code = out.get("exit_code", 1)

        content = f"# Failed command\n\n```sh\n{cmd}\n```\n\nExit code: {exit_code}\n"
        if stderr:
            content += f"\nStderr:\n```\n{stderr}\n```"
        if stdout:
            content += f"\nStdout:\n```\n{stdout}\n```"

        ep_id = f"bash_fail_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        _log.debug("bash_failure: cmd=%r exit=%d session=%s",
                   cmd[:80], exit_code, session_id,
                   extra={"session_id": session_id, "project": project_root})
        return [MemoryEpisode(
            id=ep_id,
            session_id=session_id,
            timestamp=_now(),
            layer=0,
            title=f"Failed: {cmd[:60]}",
            content=content,
            source_type="bash",
            source_path=project_root,
            category="failure",
            importance=0.7,
            frustration_score=0.5,
            tags=["bash", "failure"],
            context_snapshot={"exit_code": exit_code, "command": cmd[:200]},
        )]
