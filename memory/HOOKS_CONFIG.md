# Claude Code Hook Configuration

How to wire the memory engine into Claude Code's lifecycle hooks so capture +
continuous learning happen automatically.

The hook handler is the **`crisp-hook`** executable (installed on PATH by
`uv tool install -e .` / `make install`; it is `lib.hooks:main`). Claude Code pipes
the hook-event JSON to it on **stdin**; `crisp-hook` routes by its **first argument**.

## Entry points (`crisp-hook <arg>`)

| Invocation | Claude Code event | What it does |
|---|---|---|
| `crisp-hook claude-session-start` | `SessionStart` | Eager, whole-repo structural index (bounded, resumable across sessions) |
| `crisp-hook claude-pre-tool` | `PreToolUse` | Record a tool-use observation (instinct engine) + inject existing memory for the file about to be Read/Edited via `additionalContext` |
| `crisp-hook claude-post-tool` | `PostToolUse` | Record a tool-use observation + lazily index edited/read files |
| `crisp-hook claude-stop` | `Stop` | Distill the observation buffer into instincts; correction/frustration detection |
| `crisp-hook claude-session-end` | `SessionEnd` | Capture transcript, consolidate (L0→L1→L2→L3), prune |
| `crisp-hook claude-pre-compact` | `PreCompact` | Same capture/consolidate before context compaction |

All entry points read stdin, **exit 0 on error** (never block a tool or a turn), and
write a small JSON result to stdout.

> Instincts are stored in the **per-project** store (resolved from the hook payload's
> `cwd`), so learning in project A stays in project A. See `skills/memory/instincts.md`.

## Install (settings.json)

Claude Code uses an **array-of-matchers** schema, and `timeout` is in **seconds**.
Add this to `~/.claude/settings.json` (merge with any existing `hooks` — do not
replace the object). Most per-tool observers use `"async": true` so they add
**zero latency** and can never block/deny a tool — they are pure observation.
**`PreToolUse` is the one exception**: it must run synchronously (no `"async"`
key at all) because `additionalContext` injection only reaches the model when
Claude Code waits for the hook's stdout — an async `PreToolUse` hook fires
but its output is discarded.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "\"$HOME/.local/bin/crisp-hook\" claude-session-start", "async": true, "timeout": 60 }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "\"$HOME/.local/bin/crisp-hook\" claude-pre-tool", "timeout": 10 }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "\"$HOME/.local/bin/crisp-hook\" claude-post-tool", "async": true, "timeout": 10 }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "\"$HOME/.local/bin/crisp-hook\" claude-stop", "async": true, "timeout": 10 }
        ]
      },
      {
        "hooks": [
          { "type": "command", "command": "bash \"$HOME/.claude/hooks/memory-session-end.sh\"", "async": true, "timeout": 15 }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          { "type": "command", "command": "\"$HOME/.local/bin/crisp-hook\" claude-session-end", "timeout": 30 }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          { "type": "command", "command": "\"$HOME/.local/bin/crisp-hook\" claude-pre-compact", "timeout": 30 }
        ]
      }
    ]
  }
}
```

`Stop` fires after every Claude response — use it for fast async work (instinct distillation, stale-file checkpoint). `SessionEnd` and `PreCompact` run synchronously and trigger L0→L1→L2→L3 consolidation; they should not be async. `SessionStart` is also async — the eager repo walk is bounded (500 files/run) and safe to run in the background; a mid-scan search just misses files not yet indexed this run.

### memory-session-end.sh (Stop hook)

This companion script checkpoints which source files were modified during the session so the next session knows which index entries are stale:

```bash
#!/bin/bash
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
unstaged=$(git -C "$PROJECT_DIR" diff --name-only 2>/dev/null | grep -E '\.(ts|tsx|kt|sql)$')
staged=$(git -C "$PROJECT_DIR" diff --name-only --cached 2>/dev/null | grep -E '\.(ts|tsx|kt|sql)$')
changed=$(printf '%s\n%s' "$unstaged" "$staged" | sort -u | grep -v '^$')
if [ -n "$changed" ]; then
  file_list=$(echo "$changed" | tr '\n' ' ' | sed 's/ $//')
  crisp checkpoint --note "Session ended. Modified files may need re-indexing: $file_list" 2>/dev/null || true
fi
```

Install to `~/.claude/hooks/memory-session-end.sh` and `chmod +x` it.

After editing settings, open **`/hooks`** once (or restart) so Claude Code reloads the
config — otherwise the new hooks won't fire until the watcher picks them up.

## Hook input (stdin, Claude Code native)

```json
{
  "session_id": "abc123",
  "cwd": "/path/to/project",
  "tool_name": "Edit",
  "tool_input": { "file_path": "/path/src/auth.go" },
  "tool_response": { "success": true }
}
```

`crisp-hook` also accepts an **internal format** (a `hook_event_name` field instead of
the `argv` selector) for `FileChange`, `Stop`, `SessionEnd`, `ToolFailure` — used by
tests and non-Claude callers.

## Test manually

```bash
# observation (PreToolUse)
echo '{"tool_name":"Bash","tool_input":{"command":"git status"},"cwd":"'"$PWD"'"}' | crisp-hook claude-pre-tool
# -> {"status":"ok","observed":"pre"}

# distill at stop
echo '{"session_id":"t"}' | crisp-hook claude-stop

# then inspect what was learned (from inside the project)
crisp instinct analyze --force && crisp instinct list
```

## Tuning / disabling

- Per-project knobs live in `<store>/config/config.json` under `"instincts"`
  (`enabled`, `min_observations`, `base_confidence`, `reinforce_step`,
  `evolve_threshold`, `promote_min_projects`). Set `"enabled": false` to make
  `observe` a no-op without removing the hooks.
- Or remove the hook entries from `settings.json` (use `/hooks` to review/disable).

## Troubleshooting

- **Nothing recorded?** Open `/hooks` or restart so the config reloads; confirm
  `crisp-hook` is on PATH (`which crisp-hook`); pipe-test the command above.
- **Wrong/empty project?** Instinct scope comes from the payload `cwd`; a dir with no
  git remote is keyed by its path. Confirm with `crisp instinct list` from that dir.
- **Duplicates / decay surprises?** Instincts live at L2 (30-day half-life), reinforced
  on recurrence; see `skills/memory/instincts.md`.
