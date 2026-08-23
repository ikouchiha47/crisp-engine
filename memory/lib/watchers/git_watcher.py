"""GitWatcher — captures git commit/push/merge/rebase/reset/stash/tag as episodes."""

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lib.log import get_logger
from lib.store import MemoryEpisode

_log = get_logger("watchers.git")

_SKIP_CMDS = {"add", "fetch", "status", "log", "diff", "show", "ls-files", "branch", "checkout", "switch"}
_CAPTURE_CMDS = {"commit", "push", "merge", "rebase", "reset", "stash", "tag"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ep_id(prefix: str) -> str:
    return f"git_{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


class GitWatcher:
    name = "git"
    tool_names = ["Bash"]

    def matches(self, tool_name: str, inp: dict, out: dict) -> bool:
        if tool_name != "Bash":
            return False
        cmd = inp.get("command", "").strip()
        if not cmd.startswith("git "):
            return False
        sub = self._subcmd(cmd)
        return sub in _CAPTURE_CMDS

    def episodes(
        self,
        tool_name: str,
        inp: dict,
        out: dict,
        session_id: str,
        project_root: str,
    ) -> list[MemoryEpisode]:
        cmd = inp.get("command", "").strip()
        sub = self._subcmd(cmd)
        stdout = out.get("stdout", "") or ""
        stderr = out.get("stderr", "") or ""
        exit_code = out.get("exit_code", 0)

        _log.debug("git: sub=%s exit=%s session=%s project=%s",
                   sub, exit_code, session_id, project_root,
                   extra={"session_id": session_id, "project": project_root})
        dispatch = {
            "commit": self._handle_commit,
            "push": self._handle_push,
            "merge": self._handle_merge,
            "rebase": self._handle_rebase,
            "reset": self._handle_reset,
            "stash": self._handle_stash,
            "tag": self._handle_tag,
        }
        handler = dispatch.get(sub)
        if not handler:
            return []
        try:
            return handler(cmd, stdout, stderr, exit_code, session_id, project_root) or []
        except Exception:
            return []

    # ── sub-handlers ──────────────────────────────────────────────────────────

    def _handle_commit(self, cmd, stdout, stderr, exit_code, session_id, project_root):
        if exit_code != 0:
            return []

        # Try to extract SHA + subject from stdout first ("master abc1234 subject")
        sha, subject, body = "", "", ""
        m = re.search(r'\b([0-9a-f]{7,40})\b', stdout)
        if m:
            sha = m.group(1)
        subject_m = re.search(r'\]\s+(.+)', stdout)
        if subject_m:
            subject = subject_m.group(1).strip()

        # Fall back to git log -1 if we have a project root
        if project_root and (not sha or not subject):
            _log.debug("git commit: stdout parse incomplete (sha=%r subject=%r), falling back to git log -1", sha, subject,
                       extra={"session_id": session_id, "project": project_root})
            try:
                r = subprocess.run(
                    ["git", "log", "-1", "--format=%H%n%s%n%b"],
                    capture_output=True, text=True, timeout=5, cwd=project_root,
                )
                if r.returncode == 0:
                    parts = r.stdout.split("\n", 2)
                    sha = parts[0].strip() if len(parts) > 0 else sha
                    subject = parts[1].strip() if len(parts) > 1 else subject
                    body = parts[2].strip() if len(parts) > 2 else ""
                    _log.debug("git commit: fallback sha=%s subject=%r", sha[:12] if sha else "-", subject,
                               extra={"session_id": session_id, "project": project_root})
                else:
                    _log.warning("git commit: git log -1 failed rc=%d stderr=%s", r.returncode, r.stderr[:200],
                                 extra={"session_id": session_id, "project": project_root})
            except Exception as e:
                _log.warning("git commit: git log -1 exception: %s", e,
                             extra={"session_id": session_id, "project": project_root})

        if not subject:
            subject = stdout.strip()[:120] or "git commit"

        content_lines = [f"# git commit: {subject}", ""]
        if sha:
            content_lines.append(f"SHA: `{sha}`")
        if body:
            content_lines += ["", "## Body", body[:1000]]
        content_lines += ["", f"Command: `{cmd}`"]

        return [MemoryEpisode(
            id=_ep_id("commit"),
            session_id=session_id,
            timestamp=_now(),
            layer=0,
            title=f"git commit: {subject[:80]}",
            content="\n".join(content_lines),
            source_type="git",
            source_path=project_root,
            category="git_commit",
            importance=0.8,
            lesson=subject,
            tags=["git", "commit"],
            context_snapshot={"sha": sha, "subject": subject},
        )]

    def _handle_push(self, cmd, stdout, stderr, exit_code, session_id, project_root):
        rejected = exit_code != 0 or "rejected" in stdout.lower() or "rejected" in stderr.lower()
        remote_m = re.search(r'git push\s+(\S+)', cmd)
        remote = remote_m.group(1) if remote_m else "origin"
        branch_m = re.search(r'To \S+\n.*?(\S+)\s+->', stdout)
        branch = branch_m.group(1) if branch_m else ""

        content = f"# git push {'(REJECTED)' if rejected else '(ok)'}\n\n"
        content += f"Remote: {remote}\n"
        if branch:
            content += f"Branch: {branch}\n"
        if rejected:
            content += f"\nError output:\n```\n{(stdout + stderr)[:800]}\n```"

        return [MemoryEpisode(
            id=_ep_id("push"),
            session_id=session_id,
            timestamp=_now(),
            layer=0,
            title=f"git push {'rejected' if rejected else 'ok'} -> {remote}",
            content=content,
            source_type="git",
            source_path=project_root,
            category="git_push",
            importance=0.6,
            frustration_score=0.7 if rejected else 0.0,
            tags=["git", "push"] + (["rejected"] if rejected else []),
            context_snapshot={"remote": remote, "rejected": rejected},
        )]

    def _handle_merge(self, cmd, stdout, stderr, exit_code, session_id, project_root):
        conflict = "conflict" in stdout.lower() or "conflict" in stderr.lower()
        combined = (stdout + stderr)[:1200]

        return [MemoryEpisode(
            id=_ep_id("merge"),
            session_id=session_id,
            timestamp=_now(),
            layer=0,
            title=f"git merge {'(conflicts)' if conflict else 'ok'}",
            content=f"# git merge\n\n```\n{combined}\n```",
            source_type="git",
            source_path=project_root,
            category="git_conflict" if conflict else "git_merge",
            importance=0.9 if conflict else 0.5,
            frustration_score=0.8 if conflict else 0.0,
            tags=["git", "merge"] + (["conflict"] if conflict else []),
            context_snapshot={"conflict": conflict, "exit_code": exit_code},
        )]

    def _handle_rebase(self, cmd, stdout, stderr, exit_code, session_id, project_root):
        conflict = "conflict" in stdout.lower() or "conflict" in stderr.lower()
        combined = (stdout + stderr)[:1200]

        return [MemoryEpisode(
            id=_ep_id("rebase"),
            session_id=session_id,
            timestamp=_now(),
            layer=0,
            title=f"git rebase {'(conflicts)' if conflict else 'ok'}",
            content=f"# git rebase\n\n```\n{combined}\n```",
            source_type="git",
            source_path=project_root,
            category="git_conflict" if conflict else "git_rebase",
            importance=0.8 if conflict else 0.5,
            frustration_score=0.7 if conflict else 0.0,
            tags=["git", "rebase"] + (["conflict"] if conflict else []),
            context_snapshot={"conflict": conflict, "exit_code": exit_code},
        )]

    def _handle_reset(self, cmd, stdout, stderr, exit_code, session_id, project_root):
        hard = "--hard" in cmd
        return [MemoryEpisode(
            id=_ep_id("reset"),
            session_id=session_id,
            timestamp=_now(),
            layer=0,
            title=f"git reset {'--hard' if hard else ''}".strip(),
            content=f"# git reset\n\nCommand: `{cmd}`\n\nOutput:\n```\n{(stdout + stderr)[:600]}\n```",
            source_type="git",
            source_path=project_root,
            category="git_reset",
            importance=0.8,
            tags=["git", "reset"] + (["hard", "destructive"] if hard else []),
            lesson="Work was thrown away or history was rewritten" if hard else None,
            context_snapshot={"hard": hard, "exit_code": exit_code},
        )]

    def _handle_stash(self, cmd, stdout, stderr, exit_code, session_id, project_root):
        msg_m = re.search(r'stash\s+push\s+(?:-m\s+)?["\']?(.+?)["\']?\s*$', cmd)
        stash_msg = msg_m.group(1) if msg_m else ""
        return [MemoryEpisode(
            id=_ep_id("stash"),
            session_id=session_id,
            timestamp=_now(),
            layer=0,
            title=f"git stash{': ' + stash_msg if stash_msg else ''}",
            content=f"# git stash\n\nCommand: `{cmd}`\nMessage: {stash_msg or '(none)'}\n\n```\n{stdout[:400]}\n```",
            source_type="git",
            source_path=project_root,
            category="git_stash",
            importance=0.6,
            tags=["git", "stash"],
            context_snapshot={"stash_message": stash_msg},
        )]

    def _handle_tag(self, cmd, stdout, stderr, exit_code, session_id, project_root):
        tag_m = re.search(r'git tag\s+(\S+)', cmd)
        tag_name = tag_m.group(1) if tag_m else ""
        return [MemoryEpisode(
            id=_ep_id("tag"),
            session_id=session_id,
            timestamp=_now(),
            layer=0,
            title=f"git tag {tag_name}",
            content=f"# git tag {tag_name}\n\nCommand: `{cmd}`\n\n```\n{stdout[:400]}\n```",
            source_type="git",
            source_path=project_root,
            category="git_tag",
            importance=0.9,
            tags=["git", "tag", "release"],
            lesson=f"Release boundary: {tag_name}",
            context_snapshot={"tag": tag_name},
        )]

    # ── helpers ───────────────────────────────────────────────────────────────

    def _subcmd(self, cmd: str) -> str:
        parts = cmd.strip().split()
        # parts[0] = "git", parts[1] = subcommand (possibly)
        if len(parts) < 2:
            return ""
        return parts[1].lstrip("-")
