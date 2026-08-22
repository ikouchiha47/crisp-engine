# Crisp Engine — Episodic Memory for AI Agents

Automatic, layered memory for Claude Code sessions. Every file you edit, every
correction you give, every tool you run — captured, summarised, and promoted
through four layers so future sessions carry what past ones learned.

```
Tool use / edit  →  L0 episode (raw diff + symbols, or a tool observation)
                    ↓ every ~20 episodes
                 L1 session summary
                    ↓ clustered by topic
                 L2 topic cluster   ← instincts also live here
                    ↓ promoted
                 L3 life arc (permanent)
```

Memory is **per-project by default**: each project gets its own store under
`~/.claude/memory/projects/<id>/` (auto-detected from the working directory / git
root). Work in project A stays in A. A separate **global** store
(`~/.claude/memory/`) is used when you're not inside a project, and is where
cross-project **instincts** graduate (see below).

---

## How it works

| Hook | Fires when | What gets stored |
|---|---|---|
| `SessionStart` | Session begins | Eager, whole-repo structural index (bounded to 500 files/run; resumes across sessions since already-indexed files are skipped) → L0 |
| `PreToolUse` / `PostToolUse` | Any tool runs | A tool-use **observation** (instinct engine); PostToolUse also lazily indexes edited/read files → L0 |
| `Stop` | Claude finishes a turn | Distills observations into instincts; corrections/frustration → L0 |
| `PreCompact` | Context window fills | Last conversation turns → L0, then full cascade |
| `SessionEnd` | Session closes | Conversation transcript → L0, then full cascade |

The cascade (`L0→L1→L2→L3`) runs automatically at `PreCompact` and `SessionEnd`.
Each layer has a decay half-life: **L0 = 1 day, L1 = 7 days, L2 = 30 days, L3 = permanent**.
Reinforcement (re-seeing the same content/pattern) bumps access and resets decay.

### Continuous-learning instincts

Tool-use observations are distilled into **instincts** — confidence-scored behaviors
(an episode with `category="instinct"` at L2). They `reinforce` on recurrence,
`evolve` into emitted skills/commands/agents, and `promote` project→global once a
signature is seen in ≥2 projects. See `../skills/memory/instincts.md`.

### Semantic search (opt-in)

`crisp search` is keyword/structured by default. `crisp search --semantic` uses
embeddings — **user-triggered only**, never on the automatic path. The provider is
configurable (`embedding_provider`: `mock` default, or `ollama` with a configurable
model + API route). A persistent vector index (sqlite) is on the roadmap; today the
mock keeps the path exercised and `ollama` computes on the fly.

---

## Installation

```bash
git clone git@github.com:ikouchiha47/huh
uv tool install -e ./huh/memory   # editable: tracks the repo
```

This installs three commands into `~/.local/bin/`:

| Command | Maps to | Purpose |
|---|---|---|
| `crisp` | `lib.cli:main` | CLI — search, reflect, stats, prune, `instinct …` |
| `crisp-hook` | `lib.hooks:main` | hook entry point wired into `.claude/settings.json` |

For **fish**, ensure `~/.local/bin` is on PATH: `fish_add_path ~/.local/bin`.

The `/memory` skill lives in `../skills/memory/` (install via the repo `Makefile`'s
`make link`, or copy it to `~/.claude/skills/memory/`).

---

## Wire into Claude Code

See **`HOOKS_CONFIG.md`** for the full setup. Minimal passive learning — add to
`~/.claude/settings.json` (`async` so observers add no latency and never block a tool):

```json
{
  "hooks": {
    "PreToolUse":  [{ "matcher": "*", "hooks": [{ "type": "command", "command": "\"$HOME/.local/bin/crisp-hook\" claude-pre-tool",  "async": true, "timeout": 10 }] }],
    "PostToolUse": [{ "matcher": "*", "hooks": [{ "type": "command", "command": "\"$HOME/.local/bin/crisp-hook\" claude-post-tool", "async": true, "timeout": 10 }] }],
    "Stop":        [{ "hooks": [{ "type": "command", "command": "\"$HOME/.local/bin/crisp-hook\" claude-stop", "async": true, "timeout": 10 }] }]
  }
}
```

After editing settings, open `/hooks` once (or restart) so Claude Code reloads them.

---

## CLI usage

```bash
crisp stats                       # layer counts, cache size
crisp search "JWT validation"     # keyword search; add --semantic for embeddings
crisp reflect                     # manually trigger L0→L1→L2→L3 cascade
crisp prune                       # remove decayed episodes
crisp save "note" --permanent     # save a permanent note
crisp instinct list               # learned behaviors for this project
crisp instinct evolve             # emit a skill from high-confidence instincts
```

In Claude Code the skill is **`/memory`** (subcommands route to the `crisp` CLI).

---

## Storage layout

Per-project store (the default), with a global store at the same shape:

```
~/.claude/memory/
  projects/<id>/        per-project store (layers/, cache/, config/, observations/, evolved/)
  layers/               global store (used outside a project; promoted instincts)
    l0/  raw episodes (.md, 1-day half-life)
    l1/  session summaries (.md, 7-day)
    l2/  topic clusters + instincts (.md, 30-day)
    l3/  life arcs (.md, permanent)
  cache/
    hashes.json         content-hash dedup index
    file_states.json    per-file change detection
    links.json          episode graph edges
  config/config.json
  observations/         append-only tool-use buffers (instinct engine)
  evolved/              skills/commands/agents emitted by `instinct evolve`
```

Episodes are plain markdown with YAML frontmatter — readable in any editor,
diff-friendly in git. A SQLite vector index is on the roadmap for semantic scale.

---

## Architecture

One directory per ownable concern — each can be worked on without needing to
understand the others' internals. `(planned)` marks components that are
designed but not yet built; everything else is real, tested code.

```
lib/
  ingest/                  chat-history parsing/scoring, vendored from chinfer
                            (Claude Code JSONL + OpenCode SQLite -> scored segments)
  ingest_bridge.py           glue: ingest segments -> MemoryEpisode -> store
  git_correlate.py           (planned) upgrade episodes with commit SHA/message;
                              commit boundaries as consolidation triggers

  store/
    episode.py                MemoryEpisode schema + is_code_index_category
    memory_store.py             MemoryStore: identity/dedup, atomic+tolerant JSON state
    project_memory.py            per-project store resolution (git-root hashing)

  code_index/
    __init__.py                CodeElement (shared type) + analyze_file() orchestrator:
                                tries treesitter -> ctags -> regex, first success wins
    treesitter_strategy.py       tree-sitter extraction (lazy grammar loading)
    ctags_strategy.py            (planned) universal-ctags extraction
    regex_strategy.py            regex extraction, true last resort
  lang_detect.py                language ID (GitHub Linguist languages.yml +
                                 heuristics.yml, vendored) — tiered, agentic-fallback
                                 for the genuine remainder via classify-language

  indexers/                  media -> episode adapters (code/markdown/text/image/audio),
                              IndexerRegistry picks the right one per file

  consolidate/
    reflector.py                L0->L1->L2->L3 consolidation pipeline
    prune.py                     Ebbinghaus decay, archive/delete lifecycle
  instincts/                  continuous-learning: observe -> distill -> reinforce ->
                              evolve -> promote (project -> global)

  retrieve/
    episodic_search.py          multi-layer search + graph expansion + reranking
    code_search.py               (planned) Cursor-style n-gram fast raw-code search
    query_layer.py                (planned) unified interface, later MCP-exposed

  metrics/                    (planned) recall accuracy, correction-repeat rate,
                              indexing coverage — real evaluation, not just test counts

  embeddings.py               pluggable embedding providers (mock default, ollama optional)
  cli.py                      crisp CLI entry point
  hooks.py                    Claude Code hook handlers + payload translation
  mcp_server.py                (planned) exposes query/index as agent tools directly

ui/                           (planned) trace/episode browser — see what got captured
                              from commits/diffs, browse the memory layers themselves

unlib/                        orphaned/undecided modules, kept but out of the main
                              tree pending a real keep-or-delete call (see
                              plans/PRODUCT-personas-and-feature-audit.md)
```

---

## Episode format

```markdown
---
id: file_20260504_135959
layer: 0
timestamp: 2026-05-04T13:59:59Z
title: File change: auth.ts
source_type: file
source_path: /project/src/auth.ts
category: code
importance: 0.6
tags: [file_change, ts]
---

File changed: auth.ts
Change type: edit

## Diff
\`\`\`diff
@@ -12,6 +12,8 @@ ...
\`\`\`

## Code Elements
- `verifyToken()` (function)
- `AuthService` (class)
```
