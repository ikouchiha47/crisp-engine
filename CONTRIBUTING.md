# Contributing to Crisp Engine

## Repository layout

```
memory/
  lib/
    hooks.py          -- Claude Code hook entry points
    cli.py            -- crisp CLI subcommands
    watchers/         -- ToolWatcher plugins (this doc)
    store/            -- episode persistence
    instincts/        -- behavioral pattern engine
    consolidate/      -- reflect / prune
    code_index/       -- structural code indexing
    log.py            -- structured rotating logger
  pyproject.toml
skills/
  memory/             -- Claude skill files
README.md
CONTRIBUTING.md
```

---

## How ToolWatchers work

Every time Claude Code calls a tool (Write, Edit, Bash, Read, ...) a `PostToolUse`
hook fires. The hook payload contains:

- `tool_name` -- the tool that ran (e.g. `"Bash"`)
- `tool_input` -- the arguments (e.g. `{"command": "git commit -m \"...\""}`)
- `tool_response` -- the output (e.g. `{"stdout": "...", "exit_code": 0}`)

`hooks.py` hands this to the `WatcherRegistry`, which iterates every loaded
watcher and asks two questions:

1. `watcher.matches(tool_name, inp, out)` -- should this watcher handle this call?
2. `watcher.episodes(tool_name, inp, out, session_id, project_root)` -- what episodes does it produce?

Episodes are saved to the memory store immediately. Watchers never write to the
store directly -- they only return `MemoryEpisode` objects and the registry saves them.

### Built-in watchers

| Watcher | Tools | What it captures |
|---|---|---|
| `file_change` | Write, Edit, MultiEdit | Source file edits -- diff + symbols. One episode per file. |
| `git` | Bash (`git ...`) | commit, push, merge, rebase, reset, stash, tag. Commit subject goes into `lesson`. Conflicts and rejected pushes get `frustration_score`. |
| `bash_failure` | Bash (exit != 0) | Any non-git command that fails. Stderr truncated to 500 chars. |

Commands that are NOT captured: `git add`, `git fetch`, `git status`, `git log`,
`git diff` -- these are mechanics with no decision or lesson attached.

---

## Writing a watcher plugin

A plugin is a single `.py` file that exports exactly one class implementing the
`ToolWatcher` protocol.

### Minimal example

```python
# ~/.config/crisp/watchers/docker_watcher.py

from datetime import datetime, timezone
from lib.store import MemoryEpisode


class DockerWatcher:
    name = "docker"
    tool_names = ["Bash"]

    def matches(self, tool_name: str, inp: dict, out: dict) -> bool:
        cmd = inp.get("command", "")
        return cmd.startswith("docker build") or (
            cmd.startswith("docker push") and out.get("exit_code", 0) != 0
        )

    def episodes(
        self,
        tool_name: str,
        inp: dict,
        out: dict,
        session_id: str,
        project_root: str,
    ) -> list[MemoryEpisode]:
        cmd = inp.get("command", "")
        exit_code = out.get("exit_code", 0)
        stderr = (out.get("stderr", "") or "")[:600]
        failed = exit_code != 0

        ep_id = f"docker_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        return [MemoryEpisode(
            id=ep_id,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            layer=0,
            title=f"docker {'failed' if failed else 'ok'}: {cmd[:60]}",
            content=f"# {cmd}\n\nExit: {exit_code}\n\n```\n{stderr}\n```",
            source_type="bash",
            source_path=project_root,
            category="failure" if failed else "docker",
            importance=0.8 if failed else 0.5,
            frustration_score=0.7 if failed else 0.0,
            tags=["docker", "build"] + (["failure"] if failed else []),
        )]
```

Drop the file in `~/.config/crisp/watchers/`. It is loaded at the start of the
next session. No restart, no config change needed.

### Rules

- Export exactly one class. The registry finds it via `inspect.getmembers`.
- Never import from `hooks.py`. Only import from `lib.store` and the standard library.
- Never raise exceptions from `matches()` or `episodes()`. Bad watchers are logged
  and skipped -- they never break a session.
- `episodes()` may return an empty list if the call is not interesting enough to record.
- `tool_names` is a coarse filter (checked before `matches()`). Include every tool
  your watcher might want to look at.

### Watcher precedence

All matching watchers run for every tool call. There is no "first match wins" --
GitWatcher and BashWatcher can both see the same Bash call, but BashWatcher
explicitly skips `git` commands to avoid duplicate failure episodes. Use the
same pattern if your watcher overlaps with a built-in.

### Testing a watcher locally

```python
from lib.watchers.docker_watcher import DockerWatcher

w = DockerWatcher()
print(w.matches("Bash", {"command": "docker build ."}, {"exit_code": 1}))  # True
eps = w.episodes("Bash", {"command": "docker build ."}, {"exit_code": 1, "stderr": "no such file"}, "sid", "/tmp/p")
print(eps[0].category, eps[0].importance)
```

### Generating a watcher from instincts

If you have a cluster of `Bash:docker` instincts with high confidence, the skill
can scaffold a watcher for you:

```
/memory instinct evolve --kind watcher --name DockerWatcher
```

This collects the observation buffer for that tool prefix, groups by sub-command,
and writes a starter watcher to `~/.config/crisp/watchers/docker_watcher.py`.
Review and edit it before the next session loads it.

---

## Episode shape reference

```python
MemoryEpisode(
    id="unique_slug",               # required, unique per store
    session_id="...",               # required, from hook payload
    timestamp="2026-08-22T...",     # ISO 8601 UTC
    layer=0,                        # 0=raw, 1=session, 2=topic, 3=permanent
    title="short human label",      # shown in search results
    content="markdown body",        # main text, markdown ok
    source_type="git|file|bash",    # provenance
    source_path="/abs/path",        # file or project root
    category="git_commit|code|...", # used for filtering
    importance=0.8,                 # 0.0-1.0, drives consolidation priority
    lesson="why it matters",        # optional, surfaced by reflect
    frustration_score=0.0,          # 0.0-1.0, set on friction signals
    tags=["git", "commit"],
    context_snapshot={},            # arbitrary JSON, for future retrieval
)
```

---

## Logging

All watchers can use the shared logger:

```python
from lib.log import get_logger
_log = get_logger("docker_watcher")
_log.debug("build failed", extra={"session_id": session_id, "project": project_root})
```

Logs go to `~/.cache/crisp/crisp.log` (rotating, 10 MB, 3 backups).
Tail it during development: `tail -f ~/.cache/crisp/crisp.log`

Or via the CLI: `crisp logs --tail`
