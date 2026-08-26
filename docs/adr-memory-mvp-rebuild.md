# ADR: Memory MVP Rebuild — Keep Pipes, Cut Fake Cognition

**Status:** Proposed  
**Date:** 2026-08-26  
**Supersedes (in intent):** inflated claims in `memory/ARCHITECTURE.md`, `memory/HOW_IT_WORKS.md`, `memory/IMPLEMENTATION_SUMMARY.md`, `memory/VALIDATION_AND_INTEGRATION.md`  
**Companion evidence:** `memory/AUDIT.md`, live store audit of `~/.claude/memory` (2026-08-26)

---

## Context

Crisp Engine aimed to be a persistent memory agent for Claude Code and OpenCode, combining:

1. CoALA-style layered memory (working → episodic → semantic → procedural)
2. MemGPT-style hierarchical paging (L0 raw → L1 summaries → L2 clusters → L3 arcs)
3. Ebbinghaus decay, access promotion, conflict detection
4. A-MEM Zettelkasten links (`similar` / `caused` / `contradicts` / `corrected_by`)
5. 5-layer zoom retrieval with composite reranking
6. Code knowledge graph (tree-sitter → call/import/inherit edges; Graphify-class)
7. Multi-agent capture (Claude Code + OpenCode + Pi)

### What is actually true

**Capture plumbing works.** Hooks fire, watchers save file/git events, conversation transcripts land at SessionEnd, adapters normalize agent payloads, the event bus records activity.

**The memory product does not.** Live stores (e.g. `huh`, `memory`, global layers) are dominated by `code_element` signature dumps and `code_index_dir` placeholders. L1 “lessons” are function signatures. L2 is `"Cluster: general"` built from placeholders. L3 is hardcoded `"Personal Development"` with template meta-lessons. Corrections captured: **0**. Preferences: **none**. `links.json`: **missing**. Vec sidecar: **empty**. Decay scores: stuck at **1.0**. Instincts are tool-frequency noise (“Habitually runs `lsof`”). Pre-tool injection is wired but PreToolUse is documented as `async: true`, so `additionalContext` likely never reaches the model.

**Root cause:** the system treats **code indexing** as **episodic memory**. Higher layers summarize symbol noise. Injection has nothing useful to inject.

**Not in repo:** hermes-agent integration. Graphify integration. Real call-graph edges (`calls[]` / `dependencies[]` never populated). LLM summarization on the consolidate path.

### Constraint

Do **not** rewrite from zero. Salvage working infrastructure. Delete pretend cognition from the live path. Rebuild only the loop that changes agent behavior:

> capture useful events → store cleanly → retrieve what matters → inject before action

---

## Decision

### D1 — Product boundary: two systems, not one

Split what is currently one confused store into two explicit products:

| System | Purpose | Storage | Feeds L0→L3? | Injected pre-tool? |
|---|---|---|---|---|
| **Episodic memory** | Preferences, corrections, decisions, lessons, session takeaways | MD episodes under project store | Yes | Yes (primary) |
| **Code index** | Symbols, structure, later call graph | Separate index (local files or Graphify backend) | **No** | Only as optional “file map”, never as “lessons” |

**Rule:** `category in (code_element, code_index, code_index_dir, documentation-as-index)` must not enter consolidation, instinct distillation, or preference injection.

### D2 — MVP memory loop (only this is in scope for v1)

```
CAPTURE
  - explicit crisp save / /memory save
  - user corrections + preferences (from transcript / Stop / affect)
  - git commits (already good signal)
  - file diffs as audit trail (optional L0; not “lessons”)
  - conversation checkpoints at SessionEnd / PreCompact

STORE
  - MemoryStore MD + frontmatter (keep)
  - categories: preference | correction | decision | lesson |
                conversation | git_commit | code (diff audit) | summary
  - no placeholder dir episodes

CONSOLIDATE (narrow)
  - L0 → L1 only, from clean categories
  - L2/L3 disabled until L1 quality is proven
  - no LLM required for v1; templates OK if inputs are real

RETRIEVE
  - keyword always
  - vector optional (user-triggered + injection path once sidecar works)
  - EpisodicSearch composite rerank on the live injection path
  - update access_count + last_accessed on every retrieve

INJECT (the product)
  - Claude Code PreToolUse: SYNC (not async) → additionalContext
  - OpenCode: system.transform / get_instincts path returns same block
  - Content: preferences + corrections + decisions (+ top L1 if relevant)
  - Never: tool-frequency instincts, symbol dumps of the file being read
```

### D3 — Keep (do not rewrite)

| Component | Path | Role after rebuild |
|---|---|---|
| Episode model + MD store | `lib/store/memory_store.py` | Source of truth for episodic memory |
| Project resolution | `lib/store/project_memory.py` | Per-project stores |
| Hook entry + fail-open | `lib/hooks.py` | Capture + inject orchestration (slimmed) |
| Agent adapters | `lib/adapters/*` | Claude / OpenCode / Pi normalization |
| Agent shims | `docs/agent-shims/*` | OpenCode / Pi bridges |
| Watchers | `lib/watchers/*` | File / git / bash capture |
| Tree-sitter / ctags / regex | `lib/code_index/*` | **Code index only**, not memory cascade |
| Embeddings providers | `lib/embeddings.py` | Semantic search when configured |
| Event bus | `lib/bus.py` | Observability |
| Decay / prune math | `lib/consolidate/prune.py` | Wire into SessionEnd |
| Retrieval math | `lib/retrieve/episodic_search.py` | Wire into injection |
| CLI + skill shell | `lib/cli.py`, `skills/memory/` | Operator UX |
| Monitor | `lib/monitor/*` | Keep; fix session registry separately |

### D4 — Cut from live path (delete or quarantine)

| Item | Action |
|---|---|
| `code_element` → L0 cascade / L1 lessons | Stop writing into episodic cascade; move to code-index path |
| `code_index_dir` placeholders | Stop creating; delete existing or ignore in all queries |
| L2 cluster + L3 arc auto-generation | Disable in `reflector.consolidate()` until L1 is real |
| Tool-frequency instincts as injectable memory | Do not inject; optional analytics only |
| Instinct `evolve` / `promote` auto | Leave manual CLI only; not hooks |
| In-repo call-graph half-implementation | Do not expand; integrate Graphify later as external backend |
| hermes-agent mashup | Out of scope until MVP loop works |
| `memory/unlib/*` | Leave ignored; delete in cleanup phase |
| Fantasy docs (PageIndex/Windsurf roadmap as “done”) | Mark historical; ADR + AUDIT are truth |

### D5 — Build (new, minimal)

| Item | Why |
|---|---|
| `lib/affect.py` (or equivalent) | Extract preferences / corrections / instructions from user turns |
| Category gate in consolidate + inject | Single allowlist; one place |
| Sync PreToolUse injection + log injected block hash to bus | Prove injection reaches the model |
| `update_access` on retrieve; `update_all_decay_scores` on SessionEnd | Make decay/rerank real |
| Code-index write path separate from `save_episode` cascade | Stop poisoning memory |
| Store quarantine / wipe script for `code_element` mass | Existing corpus is mostly landfill |
| Graphify integration spike (phase 2) | Real code graph; do not reimplement |

### D6 — Explicit non-goals (v1)

- MemGPT-style hierarchical paging as a runtime pager
- Full CoALA working-memory manager
- A-MEM typed link inference at write time (schema may stay; population is phase 2)
- L2/L3 “life arcs” and meta-cognition
- Fine-tuned retrieval models / PageIndex tree reasoning
- Multi-machine sync, cloud store
- Image/audio indexing as core path
- hermes-agent orchestration

### D7 — Multi-agent policy

| Agent | v1 support |
|---|---|
| Claude Code | Primary. Full hooks. **PreToolUse sync for inject**; PostToolUse may stay async for observe. |
| OpenCode | Secondary. Keep adapter + shim; injection via `get_instincts` / system.transform. Verify end-to-end once Claude path is proven. |
| Pi | Adapter kept; no new Pi work in v1. |

### D8 — Data policy for existing stores

1. **Do not** trust existing L1/L2/L3 content.
2. Quarantine or bulk-delete episodes with `category in (code_element, code_index_dir)` from consolidation eligibility (soft filter first; hard delete optional).
3. Keep `git_commit`, `conversation`, `code` (file diffs), any future `preference` / `correction`.
4. Reset or ignore instincts below a new quality bar (default: do not inject any current instincts).
5. Rebuild vec sidecar only after category filter exists (otherwise we embed junk).

---

## Consequences

### Positive

- Injection can become useful without inventing new infrastructure.
- Capture/adapters/store investment is preserved.
- Code understanding can improve via Graphify without polluting memory.
- Scope is small enough to finish and verify.

### Negative / accepted

- Temporary loss of “always-on whole-repo symbol memory” inside the episode store (replace with code index).
- L2/L3 and A-MEM graph remain dormant (honest > theatrical).
- Existing stores need cleanup; stats will drop (good).
- Some docs and skills need rewriting so operators are not lied to.

### Risks if we ignore this ADR

- Continued growth of landfill episodes (already 1k+ L0 in large projects).
- Cascade keeps minting Personal Development arcs from `Bash:lsof`.
- Users correctly conclude the system is useless despite working hooks.

---

## Alternatives considered

**Full rewrite in a new package**  
Rejected. Store, hooks, adapters, and watchers are sound. Rewrite cost is mostly reintroducing bugs in plumbing.

**Keep code_element in L0 but “fix L1 prompts”**  
Rejected. Wrong abstraction. Symbols are not episodes; no template fix makes signature lists into lessons.

**Integrate hermes + Graphify + all five academic pillars in one milestone**  
Rejected. That is how the repo got overbuilt. Sequence: useful loop first, graph second, advanced memory third.

**Vector DB / new storage backend now**  
Rejected. MD store is fine at current scale. Fix what is written and retrieved first.

**Make instincts the primary memory**  
Rejected. Live instincts are tool-frequency, not preferences. Affect/corrections are the primary signal.

---

## First plan (phased)

### Phase 0 — Freeze truth (0.5 day)

**Goal:** One source of architectural truth; stop new junk design.

- [ ] Land this ADR as `Proposed` → `Accepted` after review.
- [ ] Add banner at top of `memory/ARCHITECTURE.md` and `HOW_IT_WORKS.md`:  
      “Historical / partially inaccurate — see `docs/adr-memory-mvp-rebuild.md`.”
- [ ] Point `skills/memory/SKILL.md` “what memory is” blurb at MVP categories only.
- [ ] No new features until Phase 1 gates pass.

**Exit:** Contributors know AUDIT + this ADR override older “fully implemented” claims.

---

### Phase 1 — Stop the bleeding (1–2 days)

**Goal:** Live path stops writing and consolidating poison.

| # | Task | Owner surface | Validation |
|---|---|---|---|
| 1.1 | Category allowlist module (`lib/memory_policy.py`): `EPISODIC_CATEGORIES`, `CODE_INDEX_CATEGORIES`, `INJECTABLE_CATEGORIES` | new small module | unit tests |
| 1.2 | SessionStart / Read indexer: write code symbols to **code-index path only** (or tag `index_only=True` and exclude from `list_episodes` default used by reflect) | `hooks.py`, `code_indexer.py` | new session does not increase consolidatable L0 code_element count |
| 1.3 | Stop `_ensure_dir_entries` placeholders | `hooks.py` | no new `code_index_dir` |
| 1.4 | `MemoryReflector.consolidate`: only L0 with allowlisted categories; **disable L2/L3 creation** | `reflector.py` | SessionEnd creates L1 from clean eps only; l2=l3=0 |
| 1.5 | `generate_l1_summary`: never use code_element body as lesson; skip index categories | `reflector.py` | L1 fixture test |
| 1.6 | `build_context_block`: inject only `INJECTABLE_CATEGORIES`; remove symbol dump section | `hooks.py` | unit tests in `test_context_injection.py` updated |
| 1.7 | HOOKS_CONFIG: PreToolUse **sync** (`async` false/absent); PostToolUse may remain async | `HOOKS_CONFIG.md`, README | manual: hook returns additionalContext and model sees it |
| 1.8 | Bus event `context_injected` with `{chars, categories, episode_ids}` | `hooks.py`, `bus.py` | appears in `events.db` |

**Exit criteria:**

- New session on a test project: consolidatable L0 is not majority `code_element`.
- Pre-tool injection bus events fire with non-empty preference/correction **or** empty (honest), never “Habitually runs grep”.
- `crisp reflect` does not create L2/L3.

---

### Phase 2 — Make memory real (2–4 days)

**Goal:** Capture and inject the only things that change agent behavior.

| # | Task | Validation |
|---|---|---|
| 2.1 | `lib/affect.py`: extract preference / correction / instruction candidates from user text (rules first; optional LLM later) | golden tests on sample utterances |
| 2.2 | Wire affect into Stop + SessionEnd transcript path; save `preference` / `correction` with `is_permanent=True` where appropriate | live: say “always use uv”; episode appears |
| 2.3 | Tighten correction/frustration regexes (current patterns too broad if ever fed real messages) | precision tests |
| 2.4 | Injection ranking: permanent prefs → corrections → recent decisions → optional L1 | ordering test |
| 2.5 | Call `store.update_access` for every injected episode | access_count increments |
| 2.6 | SessionEnd: `PruningService.update_all_decay_scores()` then optional archive | decay_score ≠ 1.0 after time travel test |
| 2.7 | Use `RetrievalOrchestrator` for query-shaped injection when tool input has a path/query; keep cheap path for pure prefs | search used on live path |
| 2.8 | Fix vec sidecar init / `reindex-vecs` for allowlisted categories only | `ep_vecs` count > 0 after reindex |

**Exit criteria:**

- At least one real preference and one correction round-trip: capture → store → inject → visible in PreToolUse context.
- `corrections > 0` possible in a deliberate UAT session.
- Decay updates run on SessionEnd without blocking > timeout budget.

---

### Phase 3 — Corpus hygiene (1 day)

**Goal:** Existing stores stop lying in stats and search.

| # | Task | Validation |
|---|---|---|
| 3.1 | `crisp doctor` (or `crisp audit-store`): category histogram, placeholder count, decay staleness, links presence, vec count | runs on `~/.claude/memory/projects/*` |
| 3.2 | `crisp quarantine --categories code_element,code_index_dir` moves files to `layers/l0/_quarantine/` (reversible) | huh/memory stores shrink consolidatable set |
| 3.3 | Optional hard-delete flag after quarantine review | documented |
| 3.4 | Disable or bulk-tag existing instincts `inject:false` | not injected |
| 3.5 | Delete or archive all L2 clusters / L3 arcs minted under old logic | no Personal Development arcs in active layers |

**Exit criteria:** `crisp doctor` on huh shows consolidatable corpus dominated by conversation/git/diff/preference — or empty and honest.

---

### Phase 4 — Code graph via Graphify (spike → integrate) (2–3 days)

**Goal:** Code understanding without polluting episodic memory.

| # | Task | Validation |
|---|---|---|
| 4.1 | Spike: run Graphify-Labs/graphify on `memory/` or a sample repo; document CLI/API and artifact format | spike note in `docs/` |
| 4.2 | Decision: subprocess CLI vs library import vs index export we read | short ADR addendum |
| 4.3 | `crisp code-graph build \| query` thin wrapper | query returns call/import edges |
| 4.4 | Optional: inject **compact** graph neighborhood for current file (not full symbol dump) | separate section `[crisp code-graph]` |
| 4.5 | Do **not** write graph nodes as L0 episodes | policy test |

**Exit criteria:** Agent can answer “what calls X?” from graph backend without new `code_element` L0 spam.

---

### Phase 5 — OpenCode parity + advanced memory (only after 1–3)

**Goal:** Second agent works; optional academic features return only if measured useful.

| # | Task | When |
|---|---|---|
| 5.1 | E2E OpenCode shim: capture + inject | after Phase 2 exit |
| 5.2 | A-MEM: write typed links on correction/preference conflicts into `links.json` | after affect works |
| 5.3 | L2 clustering from **preference/decision tags**, not categories of junk | after 100+ clean L1 |
| 5.4 | L3 only from human-promoted arcs or high-confidence repeated prefs | explicit, not automatic spam |
| 5.5 | hermes-agent: out of scope until 5.1–5.2 done; separate ADR |

---

## Implementation principles (binding)

1. **Honest empty > fake full.** Prefer injecting nothing over injecting tool habits.
2. **One allowlist.** All reflect/inject/search-default paths share `memory_policy.py`.
3. **Measure on live store.** Every phase ends with `crisp doctor` (or equivalent) on a real project.
4. **No new abstraction layers** until MVP injection UAT passes.
5. **Graphify over homemade graph.** Do not grow `treesitter_strategy` into a graph DB.
6. **Docs follow code.** If code disables L3, docs must not claim life-arcs work.

---

## Verification plan (MVP definition of done)

Manual UAT on a throwaway project:

1. Install hooks per updated HOOKS_CONFIG (sync PreToolUse).
2. User: “Always use `uv run` not bare python.”
3. Confirm L0/L2 episode `category=preference` saved.
4. New turn: Edit a file → PreToolUse additionalContext contains that preference.
5. User: “No, don’t use pytest-xdist here.”
6. Confirm `category=correction` saved and later injected.
7. SessionEnd: L1 summary mentions the preference/correction, **not** a list of function signatures.
8. `crisp doctor`: zero new placeholders; l2/l3 created = 0.
9. Bus: `context_injected` events present.

Automated:

- Extend `memory/tests/test_context_injection.py` for allowlist + no instincts-by-default.
- Add `tests/test_memory_policy.py`, `tests/test_affect.py`, `tests/test_reflector_allowlist.py`.

---

## File-level keep / cut map (quick reference)

```
KEEP & SLIM
  memory/lib/store/**
  memory/lib/hooks.py              # slim inject + gates
  memory/lib/adapters/**
  memory/lib/watchers/**
  memory/lib/code_index/**         # code index only
  memory/lib/embeddings.py
  memory/lib/bus.py
  memory/lib/retrieve/episodic_search.py
  memory/lib/consolidate/prune.py
  memory/lib/cli.py
  memory/lib/config.py
  memory/lib/log.py
  skills/memory/**                 # rewrite copy
  docs/agent-shims/**

REWRITE
  memory/lib/consolidate/reflector.py
  memory/lib/hooks.py::build_context_block
  memory/lib/indexers/code_indexer.py::extract_episodes
  memory/HOOKS_CONFIG.md
  README.md (memory claims)

ADD
  memory/lib/memory_policy.py
  memory/lib/affect.py
  memory/lib/code_index_store.py   # or graphify wrapper — phase 4
  crisp doctor / quarantine commands

DISABLE (feature flag or delete call sites)
  reflector L2/L3 auto
  instinct inject
  _ensure_dir_entries
  code_element → reflect

QUARANTINE / IGNORE
  memory/unlib/**
  historical ARCHITECTURE/HOW_IT_WORKS claims
  existing L2/L3 Personal Development arcs
```

---

## Decision log (to fill on accept)

| Date | Decision | Notes |
|---|---|---|
| 2026-08-26 | Proposed | From full codebase + `~/.claude/memory` audit |
| | | Accept / amend / reject |

---

## References

- `memory/AUDIT.md` — implementation vs claim matrix
- Live audit 2026-08-26: huh `proj_506218b2ddfb`, memory `proj_869473440100`, `~/.cache/crisp/events.db`
- Graphify-Labs/graphify — preferred code-graph backend (phase 4)
- Prior ADRs: `docs/adr-observability-bus.md`, `docs/plan-adapter-layer.md` (plumbing kept)
