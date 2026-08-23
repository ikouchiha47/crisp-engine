# Plan: Agent Adapter Layer

**Status:** Built  
**Date:** 2026-08-23

---

## Problem

`hooks.py` was coupled to Claude Code at two levels:

1. **Routing** -- `sys.argv[1]` matched Claude Code event names (`claude-post-tool`, etc.)
2. **Field access** -- handlers read `data.get("tool_name")`, `data.get("cwd")`, etc.
   directly from the Claude-specific payload shape

Adding any new agent (OpenCode, Pi, future) would have required duplicating routing
logic or adding `if agent == "opencode":` branches throughout every handler.

---

## Solution

A `lib/adapters/` layer that normalises every agent's payload into a single
`NormalizedEvent` before any handler sees it. Handlers are now agent-agnostic.
Adding a new agent is one new file.

---

## Architecture

```
crisp-hook <event-prefix> [JSON on stdin]
          │
          ▼
lib/adapters/registry.py
  resolve(argv, payload) -> NormalizedEvent
  -- tries adapters in order, first match wins
          │
    ┌─────┴──────┬────────────┬─────────────┐
    ▼            ▼            ▼             ▼
claude_code   opencode      pi           internal
.py           .py           .py          .py
    └─────────────────────────────────────┘
          │
          ▼
NormalizedEvent (base.py)
  agent, event_type, session_id, project_root,
  tool_name, tool_input, tool_output,
  last_message, tool_outputs, transcript_path
          │
          ▼
hooks.py main() -- dispatches on event.event_type only
watchers, store, instincts, user_model (unchanged)
```

---

## Canonical event types

| event_type | Claude Code | OpenCode | Pi |
|---|---|---|---|
| `session_start` | `claude-session-start` | `opencode-session-start` | `pi-session-start` |
| `pre_tool` | `claude-pre-tool` | -- | -- |
| `post_tool` | `claude-post-tool` | `opencode-post-tool` | `pi-post-tool` |
| `stop` | `claude-stop` | `opencode-stop` | `pi-stop` |
| `session_end` | `claude-session-end` | `opencode-session-end` | `pi-session-end` |
| `pre_compact` | `claude-pre-compact` | `opencode-pre-compact` | `pi-pre-compact` |
| `file_change` | internal only | -- | -- |
| `tool_failure` | internal only | -- | -- |

---

## Files

| File | Role |
|---|---|
| `lib/adapters/__init__.py` | exports `resolve()` |
| `lib/adapters/base.py` | `NormalizedEvent` dataclass + `AgentAdapter` Protocol |
| `lib/adapters/registry.py` | `resolve(argv, payload)` -- iterates adapters |
| `lib/adapters/claude_code.py` | maps `claude-*` argv + payload |
| `lib/adapters/opencode.py` | maps `opencode-*` argv + payload |
| `lib/adapters/pi.py` | maps `pi-*` argv + payload; flattens ContentBlock[] |
| `lib/adapters/internal.py` | maps `hook_event_name` field (legacy) |
| `docs/agent-shims/opencode.ts` | TypeScript shim: wires OpenCode events → crisp-hook |
| `docs/agent-shims/pi.ts` | TypeScript shim: wires Pi events → crisp-hook |

---

## How OpenCode and Pi call crisp-hook

Both are TypeScript-native agent platforms. They cannot call `crisp-hook` as a
shell hook directly. Instead a thin TypeScript shim is installed in the agent's
plugin/extension directory. The shim:

1. Subscribes to the agent's native events
2. Serialises relevant fields to JSON
3. Pipes it to `crisp-hook <prefix>-<event>` via stdin

The shims live in `docs/agent-shims/` and are copied by the user into:
- OpenCode: `.opencode/plugin/crisp_bridge.ts` or `~/.config/opencode/plugin/`
- Pi: `.pi/extensions/crisp_bridge.ts` or `~/.config/pi/extensions/`

---

## Tool name normalisation

OpenCode and Pi use lowercase or different tool names. Each adapter maps them
to the canonical names the watcher layer expects:

| Agent | Raw name | Canonical |
|---|---|---|
| OpenCode | `bash` | `Bash` |
| OpenCode | `patch` | `Edit` |
| Pi | `bash` | `Bash` |
| Pi | `multiedit` | `MultiEdit` |

---

## Pi output normalisation

Pi wraps tool output as typed `ContentBlock[]` instead of stdout/stderr strings.
The Pi adapter extracts text blocks and maps `isError` to `exit_code`:

```python
content = [b.get("text") for b in blocks if b.get("type") == "text"]
stdout = "\n".join(content) if not is_error else ""
stderr = "\n".join(content) if is_error else ""
exit_code = 1 if is_error else 0
```

Pi does not expose a raw exit code -- this is a best-effort mapping.

---

## Adding a new agent

1. Create `lib/adapters/<agent>.py` with a class implementing `AgentAdapter`
2. Add it to `_ADAPTERS` list in `lib/adapters/registry.py`
3. Write a shim in `docs/agent-shims/<agent>.[ts|py|sh]` if the agent needs one
4. Document the event mapping in this file

Nothing in `hooks.py`, watchers, store, or instincts changes.

---

## What is NOT decoupled yet

Handlers in `hooks.py` still receive `data` (the raw dict) in some places, not
`event`. Full handler migration to accept `NormalizedEvent` directly is a
follow-up -- it requires changing every handler signature and updating callers.
The adapter layer is complete; the handler migration is incremental.
