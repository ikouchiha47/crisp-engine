# Next steps sequence (after AST call-extract fix)

**Status:** roadmap  
**Depends on:** call edges collected from full tree-sitter body node before any `[:2000]` truncate  
**Related:** `docs/adr-memory-mvp-rebuild.md`, `docs/target-nervous-system.md`

Assume: Tract A call edges are trustworthy. Then do work in this order — **product loop first**, graph polish second, academic layers last.

---

## Phase 0 — Lock the graph fix (same day)

1. **Verify** on a known long function (`hooks.main` / anything >2k chars): expected callees appear in `elem.calls`.
2. **Keep truncate only for stored/display body**; hash + calls stay full-fidelity.
3. **Don’t** write every symbol as L0 “lessons” yet — graph stays structural.

**Exit:** `walk_calls` / graph query shows edges that used to be past the cutoff.

---

## Phase 1 — Stop the bleeding (1–2 days)

*Highest ROI — without this, distill and inject stay poisoned.*

| # | Step | Why |
|---|---|---|
| 1.1 | `memory_policy.py` allowlists (episodic vs code-index vs injectable) | One gate for reflect/inject/search |
| 1.2 | SessionStart/index: symbols → **code index / graph only**, not consolidatable L0 | Stops landfill |
| 1.3 | Kill `_ensure_dir_entries` placeholders | Stops L1/L2 garbage |
| 1.4 | Reflector: L0→L1 only from allowlisted cats; **disable L2/L3 auto** | Honest cascade |
| 1.5 | L1 never uses `code_element` as “lesson” | Clean session journals |
| 1.6 | `build_context_block`: prefs/corrections/hot only — no tool-frequency, no symbol dump | Inject can help |
| 1.7 | PreToolUse **sync** in HOOKS_CONFIG | `additionalContext` can reach the model |
| 1.8 | Bus event `context_injected` | Prove put-back |

**Exit:** New session doesn’t mint consolidatable `code_element` spam; inject isn’t “Habitually runs grep.”

---

## Phase 2 — Hot memory + put-back (1–2 days)

*Hermes-shaped always-on behaviour.*

| # | Step |
|---|---|
| 2.1 | Per-project capped **USER** + **MEMORY** (hot MD, ~1.3–2.2k chars each) |
| 2.2 | Inject order: hot → corrections → ranked cold → optional graph snippet |
| 2.3 | Hard inject token budget |
| 2.4 | `update_access` on every injected episode |

**Exit:** One saved preference shows up on the next PreToolUse.

---

## Phase 3 — Local distill (the real “learn” step) (2–3 days)

*Host-independent learning — local Qwen/Ollama chat.*

| # | Step |
|---|---|
| 3.1 | `lib/generate.py`: Ollama `/api/chat` → HF causal → none (**keep** embed chain separate) |
| 3.2 | `lib/distill.py`: bundle transcript + signals → JSON (prefs/corrections/L1/hot patches/habits) |
| 3.3 | Wire SessionEnd / PreCompact / `crisp reflect` → distill (background OK; don’t block forever) |
| 3.4 | Live **rules** affect as fast path for clear “always/never” |
| 3.5 | Apply hot patches under char caps (Hermes-style consolidate when full) |

**Exit:** Session with “always use uv” → preference episode + hot patch **without** Claude writing memory.

**Note:** Ollama/HF/W2V **embed** chain stays for semantic search. Chat/generate is a separate path. W2V = embed last resort only.

---

## Phase 4 — Search that works (1–2 days)

| # | Step |
|---|---|
| 4.1 | **FTS5** over episode/transcript text (Hermes session_search job) |
| 4.2 | Fix/rebuild **vec sidecar** for allowlisted categories only (Ollama/HF/W2V embed) |
| 4.3 | `crisp search` = FTS default; `--semantic` = vec |
| 4.4 | Inject retrieve = FTS ∪ vec → existing composite rerank |

**Exit:** `crisp search "uv"` and semantic both find the preference; inject uses the same path.

---

## Phase 5 — Feedback loop (1 day)

*Closed nervous system.*

| # | Step |
|---|---|
| 5.1 | Correction → `corrected_by` + weaken related habit |
| 5.2 | Successful turn with habit in inject context → reinforce (or delayed SessionEnd credit) |
| 5.3 | Optional `write_approval` / pending for auto hot writes |
| 5.4 | Strong habits → `SKILL.md` evolve (manual or threshold) |

**Exit:** Wrong auto-pref can be corrected and stops being injected; good habit confidence rises.

---

## Phase 6 — Corpus hygiene (0.5–1 day)

| # | Step |
|---|---|
| 6.1 | `crisp doctor` (category histogram, placeholders, vec/FTS health) |
| 6.2 | Quarantine existing `code_element` / `code_index_dir` / fake L2/L3 arcs |
| 6.3 | Tag old tool-frequency instincts `inject:false` |

**Check when we reach here:** decay scoring (`prune.py::compute_decay_score` / `update_all_decay_scores`) is implemented and correct but never called from any hook — `archive_low_value()` currently has nothing real to act on. Don't just wire the existing function into `session_end`/`PreCompact` as a stray call; decide first whether decay belongs as part of whatever store-maintenance module Phase 6 ends up being, and whether it's even still needed once 6.1–6.3 land (6.2's one-time quarantine may cover enough of the problem on its own). Revisit then, not now.

**Exit:** Stats reflect real memory, not symbol landfill.

---

## Phase 7 — Graph productize (after Phases 1–3)

*Extraction fixed; now use it.*

| # | Step |
|---|---|
| 7.1 | Persist graph (edges in store / Graphify export) |
| 7.2 | `crisp code-graph query "what calls X?"` |
| 7.3 | Inject **compact** neighborhood for current file only (`[crisp code-graph]`) |
| 7.4 | Still never dump full symbol tables into L0 lessons |

**Exit:** Structural questions answered from graph; episodic path stays clean.

---

## Phase 8 — Only when cold store is clean

- L2 topic cards from real learnings
- L3 identity from repeated prefs/habits
- A-MEM typed links beyond parent/child
- OpenCode E2E parity
- External providers (optional)

---

## What not to do next

- More L2/L3 template “intelligence”
- Hermes full runtime / gateway
- Embed-as-summarizer
- Expanding graph into episodic MD
- New abstractions before Phase 3 UAT

---

## Minimal critical path (if time is short)

```text
0  AST calls (done / in progress)
1  Stop code_element cascade + sync inject
2  Hot USER/MEMORY + clean inject
3  Local Qwen distill on SessionEnd
4  FTS + vec on allowlisted only
5  Correction → weaken feedback
```

That sequence turns Crisp from **recorder** into **learner that puts behaviour back** — the point of the nervous system design.

---

## Closed loop (target)

```text
capture → local distill (Qwen) → store/hot → inject into host → user corrects/succeeds → reinforce/weaken
```

| Direction | Now | After this sequence |
|---|---|---|
| In (afferent) | Strong | Keep; stop junk categories |
| Meaning (distill) | Missing | Phase 3 |
| Out (efferent inject) | Weak/noisy | Phases 1–2 |
| Feedback | CLI only | Phase 5 |

---

## Parked: migrate lib/generate.py to DSPy

Current `lib/generate.py` is a hand-rolled provider chain (`OllamaChatProvider`/`HFChatProvider`, manual JSON parsing via `json.loads()`+`.get()`+`isinstance` checks in `lib/narrate.py`) that copied `embeddings.py`'s existing hand-rolled-chain shape without questioning whether that shape was right for chat/generation. It works (verified live: clean JSON parse, correct field names, no text-stripping needed) but has no real output validation — a malformed field (e.g. `meta_lessons` returned as a string instead of a list) silently degrades to an empty result instead of surfacing as an error.

`dspy.Signature` with Pydantic-typed output fields would: give real validation instead of silent degradation, collapse the whole provider-chain (Ollama/HF/OpenAI/Anthropic/Gemini) into one `dspy.LM(...)` config string via LiteLLM, and support `Predict`/`ChainOfThought`/`ReAct` module upgrades later without touching call sites. `embeddings.py` already has `DSPyEmbeddingProvider` as one of its fallback options, so this isn't a new dependency, just extending an already-accepted one to be the primary path for generation too.

Not blocking anything currently built on `lib/generate.py`/`lib/narrate.py` — parked until the L0-L3 + hot-memory + instinct-auto-trigger arc is proven working end-to-end.
