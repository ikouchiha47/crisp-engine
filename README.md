# Crisp Engine

> **Deprecated.** Superseded by [supermemory](https://github.com/supermemoryai/supermemory) — production-grade, benchmarked (#1 on LongMemEval/LoCoMo/ConvoMem), self-hostable, with a shipped Claude Code integration. This repo's memory pipeline had no real differentiator once compared directly; see `memory/AUDIT.md` and `docs/next-steps-sequence.md` for the full audit trail. The one component kept out of this: the code call-graph (`memory/lib/graph/`, `crisp graph explain/path`) — not something supermemory does, may live on separately.

Episodic memory for Claude Code. Every file you edit, every correction you give, every commit you push gets captured, summarised, and promoted through four layers so future sessions know what past ones learned.

```
edit / tool use  ->  L0 raw episode  (1-day decay)
                     |
                     v  ~20 episodes
                     L1 session summary  (7-day decay)
                     |
                     v  clustered by topic
                     L2 topic cluster   (30-day decay)
                     |
                     v  promoted
                     L3 life arc  (permanent)
```

Memory is per-project by default. Each project gets its own store under `~/.claude/memory/projects/<id>/`, auto-detected from the working directory or git root. A global store at `~/.claude/memory/` holds cross-project instincts.

---

## What gets captured

| Hook | When | What |
|---|---|---|
| `SessionStart` | Session opens | Whole-repo structural index (up to 500 files/run, skips already-fresh files) + git log delta |
| `PostToolUse` | After Write/Edit/Read | File diff + extracted code symbols |
| `Stop` | End of a turn | Corrections and frustration signals; instinct distillation |
| `SessionEnd` | Session closes | Conversation transcript + full L0->L1->L2->L3 cascade |
| `PreCompact` | Context fills | Same as SessionEnd |

---

## Install

```bash
git clone git@github.com:ikouchiha47/huh
uv tool install -e ./huh/memory
```

Installs `crisp` (CLI) and `crisp-hook` (hook entry point) into `~/.local/bin/`.

Fish shell: `fish_add_path ~/.local/bin`

---

## Wire into Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse":  [{ "matcher": "*", "hooks": [{ "type": "command", "command": "crisp-hook claude-pre-tool",  "timeout": 10 }] }],
    "PostToolUse": [{ "matcher": "*", "hooks": [{ "type": "command", "command": "crisp-hook claude-post-tool", "async": true, "timeout": 10 }] }],
    "Stop":        [{ "hooks": [{ "type": "command", "command": "crisp-hook claude-stop",        "async": true, "timeout": 10 }] }]
  }
}
```

`PreToolUse` must stay synchronous (no `"async"` key) — that's what lets `additionalContext` reach the model; every other hook can be `"async": true` since they're pure observation.

See `HOOKS_CONFIG.md` for the full setup including `SessionStart`, `SessionEnd`, and `PreCompact`.

---

## CLI

```bash
crisp stats                        # layer counts, cache size
crisp search "query"               # keyword search
crisp search "query" --semantic    # vector search (user-triggered only)
crisp reflect                      # run L0->L1->L2->L3 cascade manually
crisp prune                        # remove decayed episodes
crisp save "note" --permanent      # save a permanent note
crisp logs --tail                  # follow the live log
crisp reindex-vecs                 # rebuild vector sidecar from stored episodes
crisp config get                   # show merged embedding/store config
crisp config set embedding_provider=ollama embedding_model=qllama/bge-large-en-v1.5:latest
crisp instinct list                # learned behaviors for this project
crisp instinct evolve              # emit a skill from high-confidence instincts
```

In Claude Code, the skill is `/memory`. Subcommands route to the CLI above.

---

## Embedding / semantic search

The provider chain: HuggingFace (sentence-transformers) -> Ollama -> DSPy -> Word2Vec (gensim, last resort).

Configure via CLI flags, environment variables, `.crisp.json` in the project root, or `~/.config/crisp/config.json`:

```bash
# project-local
crisp config set embedding_provider=ollama embedding_model=qllama/bge-large-en-v1.5:latest

# global default
crisp config set embedding_provider=huggingface --global
```

Environment variables: `CRISP_EMBEDDING_PROVIDER`, `CRISP_EMBEDDING_MODEL`, `CRISP_EMBEDDING_URL`, `CRISP_EMBEDDING_DIM`.

Precedence: CLI flags > env vars > `.crisp.json` > `~/.config/crisp/config.json` > store defaults.

---

## Logging

All hook and CLI activity is written to `~/.cache/crisp/crisp.log` (rotating, 10 MB, 3 backups).

Each line: `timestamp | level | session_id | project | module | message`

```
2026-08-22T21:28:13 | INFO  | 2d246d7e | ideas/chinfer | crisp.hooks | SessionEnd transcript=...
2026-08-22T21:28:13 | INFO  | 2d246d7e | ideas/chinfer | crisp.hooks | saved episode conv_... layer=0 turns=30
2026-08-22T21:28:14 | INFO  | 2d246d7e | ideas/chinfer | crisp.hooks | SessionEnd cascade done: l1=1 l2=0 l3=0
```

```bash
crisp logs --tail          # live feed
crisp logs --last -n 100   # last 100 lines
```

Override log level with `CRISP_LOG_LEVEL=INFO` (default: `DEBUG`).

---

## Storage layout

```
~/.claude/memory/
  projects/<id>/
    layers/
      l0/   raw episodes (.md)
      l1/   session summaries (.md)
      l2/   topic clusters + instincts (.md)
      l3/   life arcs (.md, permanent)
    cache/
      hashes.json        content-hash dedup index
      file_states.json   per-file change detection + git last-SHA
      links.json         episode graph edges
      vec_sidecar.db     sqlite-vec ANN index (rebuildable)
    config/
    observations/        tool-use buffers for instinct engine
    evolved/             skills/commands emitted by instinct evolve
    project.json
```

Episodes are plain markdown with YAML frontmatter. Readable in any editor, diff-friendly in git. The vec sidecar is fully rebuildable from the .md files with `crisp reindex-vecs`.

---

## Episode format

```markdown
---
id: file_20260504_135959
layer: 0
timestamp: 2026-05-04T13:59:59+00:00
title: "File change: auth.ts"
source_type: file
source_path: /project/src/auth.ts
category: code
importance: 0.6
tags: [file_change, ts]
---

## Diff
\`\`\`diff
@@ -12,6 +12,8 @@
\`\`\`

## Code Elements
- `verifyToken()` (function)
- `AuthService` (class)
```

---

## Architecture

```
lib/
  hooks.py             Claude Code hook handlers + payload routing
  cli.py               crisp CLI entry point
  log.py               structured rotating logger
  embeddings.py        HF / Ollama / DSPy / Word2Vec provider chain
  config.py            layered config loader
  time_utils.py        UTC-everywhere helpers
  lang_detect.py       language detection (GitHub Linguist data, vendored)

  store/
    memory_store.py    MemoryStore: read/write/dedup, sqlite-vec sidecar
    project_memory.py  per-project store resolution
    sqlite_store.py    SQLiteVecStore (alternative backend)

  code_index/
    treesitter_strategy.py   tree-sitter extraction
    ctags_strategy.py        ctags extraction
    regex_strategy.py        regex fallback

  indexers/            file -> episode adapters (code/markdown/text/image/audio)

  consolidate/
    reflector.py       L0->L1->L2->L3 pipeline
    prune.py           Ebbinghaus decay + archive lifecycle

  instincts/           observe -> distill -> reinforce -> evolve -> promote

  retrieve/
    episodic_search.py 5-layer zoom search + graph expansion + composite reranking

  ingest/              conversation history parsing (JSONL + OpenCode SQLite)
  ingest_bridge.py     ingest segments -> MemoryEpisode -> store
```
