# Crisp Memory System — Full Audit

---

## 1. What We Were Trying to Build

A persistent, layered memory system for AI coding agents (Claude Code, OpenCode, Pi) that:

- **Observes** every tool call, file edit, git commit, bash command, and conversation turn
- **Stores** them as structured episodes in a 4-layer hierarchy (L0 raw → L1 session summary → L2 topic cluster / instinct → L3 life arc)
- **Consolidates** lower layers into higher-level abstractions using Ebbinghaus forgetting-curve decay
- **Retrieves** relevant memories using a 5-layer zoom search with composite reranking (vector similarity + recency + importance + access frequency)
- **Injects** relevant memory pre-tool so the agent behaves consistently with past preferences, corrections, and architectural decisions — without the user repeating themselves
- **Evolves** behavioral instincts from recurring patterns, promoting them from project-scoped to global scope
- **Builds a code knowledge graph** from tree-sitter AST parsing — nodes are functions/classes, edges are call relationships, imports, inheritance — so the agent understands the codebase structure without reading every file

Academic foundations claimed: CoALA 4-layer taxonomy, A-MEM linked episode graph, Ebbinghaus decay, MemGPT-style memory management.

---

## 2. What Is Actually Implemented and Working

### Event Bus (`lib/bus.py`)
- Fire-and-forget ring buffer deque + daemon flush thread every 200ms to WAL SQLite
- Typed dataclasses: `HookFired`, `EpisodeSaved`, `EmbedResult`, `WatcherMatched`, `WatcherSkipped`, `ReflectRan` — all require `session_id` and `project`
- `tail()` and `latest_id()` for polling
- Works correctly

### Session Registry (`lib/session.py`)
- Separate SQLite table, idempotent upsert at session_start
- Persistent across restarts, survives bus housekeeping
- Correctly wired into hooks.py `handle_claude_session_start`

### Episode Store (`lib/store/memory_store.py`)
- YAML frontmatter + markdown body per episode
- YAML performance fix: strips 1024-float embedding block with regex before parse (26s → 1.1s for 328 files)
- `list_episodes()`, `get_episode()`, `save_episode()`, `update_episode()` — all work

### Watchers (`lib/watchers/`)
- `git_watcher.py` — detects git commits, saves L0 `git_commit` episodes
- `file_change_watcher.py` — detects file edits, saves L0 `code` (diff) episodes
- `bash_watcher.py` — detects bash failures
- All emit typed bus events. Work correctly.

### Instinct Engine (`lib/instincts/instincts.py`)
- Observes tool calls, counts patterns in append-only JSONL
- `analyze()` distills patterns into L2 `instinct` episodes when threshold (20 obs) is met
- `reinforce()`, `weaken()`, `evolve()`, `promote()` — all implemented
- `evolve()` emits a skill file. `promote()` writes cross-project global instinct.

### Ebbinghaus Decay (`lib/consolidate/prune.py`)
- `compute_decay_score()`: `0.5^(days_since / half_life)` + access boost
- `update_all_decay_scores()` — iterates all episodes, updates `decay_score` field
- `detect_conflicts()` — cosine similarity between embeddings to find conflicting episodes
- `archive_low_value()` — prunes episodes with decay < threshold

### 5-Layer Episodic Search (`lib/retrieve/episodic_search.py`)
- Composite score: `vector_sim×0.4 + recency×0.3 + importance×0.2 + access_freq×0.1`
- Implemented correctly

### Monitor Dashboard (`lib/monitor/`)
- `server.py` FastAPI server: `/api/events`, `/api/sessions`, `/api/projects`, `/api/episodes/{proj_id}`, `/api/episode/{proj_id}/{ep_id}`
- `dashboard.html`: Live Feed with auto-scroll, Memory Store tab with layer/category filters, two-level sidebar (project → sessions), scroll-to-bottom FAB

### Tree-Sitter Extraction (`lib/code_index/treesitter_strategy.py`)
- `tree_sitter_python` and `tree_sitter` are installed and working
- Extracts `CodeElement` objects: name, type, signature, docstring, body, start_line, end_line
- 33 elements correctly extracted from hooks.py including full class body

---

## 3. What Exists But Is Dead / Never Called

### `update_all_decay_scores()` — never called in live path
- Only called from `cli.py` (manual command)
- Not called at session_end, not called at PreCompact
- All `decay_score` fields on episodes are stale/wrong from the moment they're written
- Consequence: `archive_low_value()` cannot function correctly; nothing ever gets pruned by decay

### `access_count` — never incremented on retrieval
- The field exists on `MemoryEpisode`
- `compute_decay_score()` uses it for the access boost
- The `access_freq×0.1` factor in `episodic_search.py` reads it
- **No code path anywhere increments it when an episode is retrieved**
- Result: every episode has `access_count=0`, access boost is always zero, access_freq factor is always zero

### `EpisodicSearch` (`lib/retrieve/episodic_search.py`) — never called from live path
- `build_context_block()` in hooks.py does NOT use it
- It manually filters `self._ep_cache` with a Python list comprehension
- The full reranker (composite score) is dead code from the perspective of what actually gets injected pre-tool

### `reinforce()` / `weaken()` in InstinctEngine — never called
- No feedback loop adjusts instinct confidence based on whether injected content was useful

### `evolve()` and `promote()` in InstinctEngine — never called from hooks
- Both are implemented and correct
- Neither is triggered automatically

### Correction/Frustration Detection (`_detect_correction`, `_detect_frustration`) — barely fires
- Only called from `handle_stop()` which fires on the Stop hook event (user clicks Stop)
- Not called on regular PostToolUse
- Patterns are too broad: `\bno\b`, `\bfix\b`, `\bstill\b`, `\bagain\b` would fire on nearly every normal message if they were in the right hook

---

## 4. What Is Implemented Wrong / Producing Garbage Output

### L1 Summary "Lessons" — lists function signatures, not lessons
- `generate_l1_summary()` in `reflector.py` pulls lessons with:
  ```python
  lesson = ep.lesson or ep.correction_delta or self._first_sentence(ep.content)
  ```
- For `code_element` episodes (320 of 512 L0 episodes): `ep.lesson` is None, `ep.correction_delta` is None, `ep.content` is ` ```py\nfunction foo(): bar\n``` `
- So `_first_sentence()` returns `"function foo(): bar"` as the lesson
- L1 summaries' "Lessons / observations" section is a list of function signatures
- This poisons L2 clustering and L3 arc

### `code_index_dir` placeholder episodes — stored as real memory
- Session-start indexer creates directory-level stub episodes:
  ```
  DIR: /path/to/dir
  (placeholder — awaiting semantic summary from /index skill)
  ```
- The `/index` skill is supposed to fill these in. It never runs automatically
- 10 of 15 L1 episodes in the huh project are this placeholder text
- These feed into L2 clustering as real content, producing clusters built from placeholder strings

### L2 Cluster — garbage in, garbage out
- Groups L1 summaries by `context_snapshot["categories"][0]`
- Most sessions are dominated by code_element, so the category is "general"
- One massive "general" cluster containing 10 placeholder-text L1 summaries
- Date range in the cluster output is **reversed** (first_seen 2026-08-26 > last_seen 2026-08-13)

### L3 Arc — wrong framing, hardcoded title
- Title is hardcoded as `"Personal Development"` for every arc
- Takes first 3 L2 clusters — which are garbage inputs
- Meta-Lessons section is hardcoded template text:
  ```
  - User preferences and working style evolution
  - Recurring challenges and solutions
  - Skill development trajectory
  - Decision-making patterns
  ```
- None of these are derived from actual data

### L2 Instincts — tool frequency, not behavioral preferences
- Content: `"Habitually runs \`lsof\` (Bash) in this project."`
- The model already knows it runs lsof. This tells it nothing about how to behave differently.
- `instincts` x26 in huh project, all of this form

### `build_context_block()` — injects nothing the model doesn't already know
- Section 1 (instincts): "Habitually runs grep" — useless
- Section 2 (episodic memory for file): function signatures of what's in the file — the model already reads the file
- No preferences injected. No corrections injected. No architectural decisions injected.

### `code_indexer.py` line 235 — throws away the body
```python
"content": f"```{lang}\n{symbol['signature']}\n```\n\n{symbol.get('docstring', 'No documentation.')}",
```
- The `symbol["body"]` (500 chars of actual implementation) is extracted by tree-sitter and then discarded
- Episodes store only the signature line

---

## 5. What Is Completely Missing (Never Built)

### `lib/affect.py` — DOES NOT EXIST
- Was supposed to extract preferences, corrections, and explicit instructions from conversation turns
- "don't use dark theme", "always use uv", "stop using Artifact" → L2 `preference` or `correction` episodes with `is_permanent=True`
- Without this, no preferences are ever captured from conversation
- Without this, `build_context_block()` has nothing useful to inject (existing injection is tool-frequency noise)

### Call Graph / Code Knowledge Graph — NOT BUILT
- Tree-sitter extracts `CodeElement` objects with `calls=[]` and `dependencies=[]` fields
- Neither field is ever populated — the tree-sitter strategy finds function definitions but never walks call expressions inside function bodies
- No cross-file symbol resolution — `foo()` call in `hooks.py` is never linked to the definition of `foo` in `bus.py`
- No NetworkX or any graph structure
- Result: 320 isolated function signature nodes with zero edges between them
- Graphify (https://github.com/Graphify-Labs/graphify) built a complete, correct, cross-file call graph with typed edges (`calls`, `imports`, `inherits`, `contains`) using the same tree-sitter + NetworkX approach in 48 hours

### OpenCode injection — NOT BUILT
- `experimental.chat.system.transform` shim described in architecture
- No code exists for it

### Pi injection — NOT BUILT
- Pi has no pre-tool hook
- Session-start or CLAUDE.md injection needed
- No code exists for it

### Preference extraction from L1 summaries — NOT BUILT
- L1 summaries don't extract "user prefers X" or "user rejected Y approach" into structured fields
- Even if corrections are detected at Stop, they're never surfaced into L2 instincts or L1 summaries

### A-MEM typed link categories — NOT BUILT
- `linked_ids` exists on episodes and is populated (L0 links into L1, L1 into L2)
- But link types (caused, contradicts, corrected_by, depends_on) are never stored
- All links are generic parent-child hierarchy, not semantic relationships

### LLM summarization — NOT BUILT (was claimed)
- The architecture mentions "using Ollama/HuggingFace model to summarize"
- `reflector.py` uses pure string concatenation and template filling
- No LLM call exists in the consolidation path
- Embeddings use Ollama/HF but summarization does not

---

## 6. Priority Fix List (Ordered by Impact)

1. **Build `lib/affect.py`** — extract preferences/corrections/instructions from PostToolUse transcript, store as L2 `preference`/`correction` episodes. This is the single most important missing piece. Without it, context injection is noise.

2. **Fix `build_context_block()`** — add preferences and corrections as a third section. Filter `category in ("preference", "correction", "instinct")` ordered by importance descending. This is what makes the injection actually useful.

3. **Wire `update_all_decay_scores()`** into session_end and PreCompact hooks. Without this, decay is never computed and the prune path cannot function.

4. **Increment `access_count` on retrieval** — in `build_context_block()` and `episodic_search.py`, update `access_count` and `last_accessed` on every episode returned. Required for the access boost in decay and the access_freq factor in reranking.

5. **Fix L1 consolidation** — in `generate_l1_summary()`, skip `code_element` and `code_index_dir` episodes when building the lessons section. Only `conversation`, `correction`, `frustration`, `git_commit` episodes should contribute lessons.

6. **Fix or remove `code_index_dir` placeholders** — either run a real summarizer at index time (concatenate child signatures into a meaningful description) or don't create the episode until it has real content. Storing placeholder text as L2 input is actively harmful.

7. **Use `EpisodicSearch` in `build_context_block()`** — replace the manual list filter with a call to the existing reranker. It exists, it's correct, it's just bypassed.

8. **Fix `code_indexer.py` line 235** — include `symbol.get("body", "")` in episode content. Tree-sitter extracts it, it should be stored.

9. **Build call graph edges in `treesitter_strategy.py`** — walk CST for `call` nodes inside each function body, collect callee names, resolve cross-file against known symbol table, write to `calls[]` and `dependencies[]`. Store edges as `linked_ids` with relationship type between `code_element` episodes. Or: integrate Graphify as the code graph backend.

10. **Fix L3 arc** — derive arc name from dominant project/category, not hardcoded "Personal Development". Fix reversed date calculation in L2 clustering.

11. **Wire `reinforce()`/`weaken()`** — after context injection, if the model uses the injected instinct correctly, reinforce it. If the user corrects the behavior the instinct suggested, weaken it. Needs the affect.py detection loop.

12. **Trigger `evolve()` and `promote()` automatically** — at session_end, check if any instincts exceed the evolve threshold. Write the skill file. Check if any instincts appear in 2+ project stores and promote to global.

---

## 7. Architecture vs Reality Summary

| Claimed | Reality |
|---|---|
| CoALA 4-layer taxonomy (L0/L1/L2/L3) | Layer structure exists; consolidation runs; L1/L2/L3 content is garbage due to code_element noise and placeholder text |
| A-MEM linked episode graph | `linked_ids` exists as parent-child hierarchy only; no typed edges (caused/contradicts/corrected_by) |
| Ebbinghaus decay | Formula correct in prune.py; never called in live path; all decay scores are stale |
| 5-layer retrieval + reranking | Implemented in episodic_search.py; bypassed in live path; access_freq factor always zero |
| Affect detection (preferences/corrections) | `_detect_correction` runs only on Stop hook with broken regex patterns; `affect.py` does not exist |
| Code knowledge graph | Tree-sitter extracts definitions; calls=[], dependencies=[] never populated; no graph, no edges |
| LLM summarization | Embedding uses Ollama/HF; summarization is string concatenation only |
| Context injection (pre-tool) | Works mechanically; content is tool-frequency noise ("Habitually runs grep") |
| OpenCode injection | Not built |
| Pi injection | Not built |
| Instinct evolution and promotion | Implemented; never triggered |
| Preference extraction from conversation | Not built; requires affect.py |
