# Target Nervous System — Crisp Memory

**Status:** Design north star  
**Date:** 2026-08-26  
**Related:** `docs/adr-memory-mvp-rebuild.md`, `memory/AUDIT.md`  
**Does not claim:** current code implements this

This document is the intended architecture for the whole memory agent —
independent of the broken bits of the current repo. Implementation should
converge toward this; the ADR phases say in what order.

---

## 0. One-sentence thesis

Three loops, one body:

```
SENSORS → ROUTER → SPECIALIZED STORES → CONSOLIDATION → WORKING MEMORY → AGENT
   ▲                                                                        │
   └────────────── feedback (helped? corrected? ignored?) ──────────────────┘
```

If stores are not specialized, you get a spinal cord that records everything
and a brain that summarizes noise.

**Hermes influence (adopt patterns, do not “integrate hermes”):** boring
markdown + light metadata as the durable substrate; evolve high-confidence
habits into skill/rule artifacts. Hermes is *not* a 5-layer cognition engine —
we keep MD simplicity from Hermes and add layered *policy* ourselves.

**Graphify influence:** structural world model (code/doc graph) is a separate
tract, never dumped into episodic “lessons.”

---

## 1. Five layers of cognition (jobs, not five clones of one object)

| Layer | Job | Lifetime | Example |
|---|---|---|---|
| **L_w Working** | What the agent needs *this turn* | seconds–minutes | Injected packet + open files + current goal |
| **L0 Episodic raw** | What happened | days | User rejected dark theme; diff; failed test; commit |
| **L1 Session semantic** | What this session meant | weeks | “Chose uv over pip; fixed hook async injection” |
| **L2 Topical / semantic** | Stable knowledge about a subject | months | “In this repo, tests run via `uv run pytest`” |
| **L3 Procedural + identity** | How to behave; who this user/project is | permanent until revised | Habits, standing prefs, “never force-push main” |

MemGPT-style paging is **not** a second taxonomy. It is the **policy** that
moves content between jobs:

- **promote** when reinforced / important / corrected  
- **compress** when many related L0s exist  
- **demote/decay** when unused (Ebbinghaus per layer)  
- **pin** when user says always/never or a correction fires  

Storage stays Hermes-simple: markdown files + YAML frontmatter + optional
sidecars (`links.json`, vec DB). Cognition lives in **policies and links**.

---

## 2. Two nervous tracts (mandatory split)

### Tract A — Structural world model (optional if nothing to parse)

| Repo kind | Parse | Nodes | Edges |
|---|---|---|---|
| Code | tree-sitter / Graphify | functions, classes, files, tests | calls, imports, contains, inherits, tested_by |
| Non-code | markdown/AST outline, wikilinks, frontmatter | sections, entities, docs | references, depends, supersedes, child_of |

**Answers:** What exists? What connects?  
**Never answers:** What did the user prefer yesterday?

### Tract B — Experiential memory (always on)

Conversation, tools, outcomes, explicit saves, git “why.”

**Answers:** What did we learn? How should we act?

**Hard rule:** Tract A nodes are not L0 lessons. Episodes may *link* `about` a
graph node (“we changed `auth.ts::verify`”) without *being* that node.

---

## 3. Sensors → Router (afferent path)

Every agent event becomes one normalized pulse (adapters already aim here):

```text
NormalizedEvent {
  agent, session, project,
  kind: tool | utterance | git | file_change | outcome | explicit_save,
  payload, timestamp
}
```

Router fans out by **kind**:

| Pulse | Destination |
|---|---|
| Source file change | Tract A delta reindex **and** optional low-importance L0 diff audit |
| User utterance | Affect extractor → preference / correction / decision / noise |
| Tool success/fail | Short outcome buffer; promote only if surprising or user-reacted |
| Git commit | L0; subject line is a lesson *candidate* (high signal) |
| Explicit “remember…” | Pin to L0 and/or L2/L3 immediately |
| Session end / pre-compact | Consolidation (“sleep”) |

**Gate:** if not surprising, not corrective, not preferential, not decisive,
and not explicit → drop or keep only in a rolling session buffer (not durable).

---

## 4. Learnings, instincts, habits (three grades — not one bucket)

```text
observation ──► learning ──► instinct ──► habit ──► skill/rule artifact
     │              │            │           │
  droppable      durable      scored      pinned / auto-inject
```

### Learnings (declarative)

| Type | Example |
|---|---|
| Fact | “API base URL is X” |
| Preference | “Always use uv” |
| Decision | “RS256 over HS256 because …” |
| Correction | “Don’t use Artifact here” (supersedes prior via `corrected_by`) |

### Instincts (procedural hypotheses)

- “When editing Python here, run `uv run pytest`”  
- Confidence 0–1, scope project|global, evidence count  
- Born from **repeated learnings + successful outcomes**  
- **Not** from “Bash was invoked 20 times”

### Habits (compiled instincts)

- Crossed confidence threshold **and** survived corrections  
- Auto-included in working memory for matching contexts  
- May evolve into a SKILL.md / rule file (Hermes-like artifact)

Current failure mode to avoid: observation → fake instinct (tool frequency).

---

## 5. Affect (cranial nerve)

From user text + recent tool context:

| Signal | Memory type | Default permanence |
|---|---|---|
| always / never / prefer / don’t | preference | permanent |
| no / wrong / stop doing X | correction | permanent + link |
| we decided / go with | decision | high → L1+ |
| remember that | explicit | as tagged |
| frustration markers | friction | medium; boost related |
| pure chatter | discard | — |

Without affect, injection has nothing worth saying.

---

## 6. Consolidation (“sleep”)

Triggers: SessionEnd, PreCompact, idle/manual `reflect`.  
Not on every keystroke.

### L0 → L1 (session)

**Inputs only:** preferences, corrections, decisions, git, meaningful outcomes,
explicit saves. High-importance diffs as *references*, not lesson text.

L1 should read like a short human journal:

- what we tried  
- what worked / failed  
- what the user taught us  
- open threads  

### L1 → L2 (topic)

Cluster by topic tags / entities / paths — never by `category=general` junk.  
L2 = wiki card for a subject (“Testing”, “Auth”, “Release”).

### L2 → L3 (procedural / identity)

Promote only: repeated preferences, high-confidence habits, decisions that
still hold. **L3 stays small.** Large L3 means broken promotion.

**Ebbinghaus:** half-life per layer; access and correction reset decay;
conflicts resolve primarily via `corrected_by`, not similarity alone.

---

## 7. A-MEM links (associative tissue)

Durable beside MD (`links.json` and/or frontmatter):

| Type | Meaning |
|---|---|
| `similar` | same topic/entity |
| `caused` | A led to B |
| `contradicts` | unresolved tension |
| `corrected_by` | B replaces A (A demoted) |
| `supports` | evidence for an instinct |
| `about` | episode → structural node (cross-tract) |

Retrieval: keyword/vector seed → expand strong edges → composite rerank.

```text
score = 0.35*relevance + 0.25*recency + 0.25*importance
      + 0.10*access + 0.05*link_boost
      then ×2 if correction/preference in scope
```

---

## 8. Working memory assembly (only thing the agent feels)

Budgeted packet (target ~1–3k tokens), priority order:

1. Habits matching this context (procedural L3)  
2. Standing preferences (project → global)  
3. Fresh corrections (session + recent)  
4. Relevant L2 topic cards (1–3)  
5. Pointed L0 evidence if needed (1–5)  
6. Optional structural snippet  
   - code: graph neighborhood of current file  
   - non-code: outline + backlinks of current path  

**Never inject:** tool-frequency noise, full symbol dump of the file about to
be read. **Honest empty > fake full.**

Working memory **is** L_w. Everything else exists to fill L_w well.

---

## 9. Feedback loop

| Outcome | Effect |
|---|---|
| User corrects behavior a habit suggested | weaken instinct; write correction; `corrected_by` |
| Affirm / success with habit in play | reinforce |
| Never retrieved | slow decay |
| Often retrieved + no correction | promote toward skill artifact |

---

## 10. Code vs non-code (same system, different Tract A)

### Code

```text
delta parse → update graph (not flood L0 with symbols)
On Edit(path):
  WM = habits/prefs + graph neighbors(path) + episodes about(path)
```

### Non-code (docs, research, notes)

```text
delta parse → section/entity graph
On Edit/Read(path):
  WM = habits/prefs + outline/backlinks(path) + decisions on that topic
```

Episodic / instinct layers are identical. Only the structural sidebar changes.

---

## 11. End-to-end diagram

```text
                 ┌─────────────────────────────┐
  Claude/OpenCode│         AGENT               │
                 │  plans, tools, talks        │
                 └─────────────┬───────────────┘
                               │ tools / utterances
                               ▼
                 ┌─────────────────────────────┐
                 │ SENSORS + ROUTER            │
                 │ hooks, watchers, normalize  │
                 └──────┬──────────────┬───────┘
                        │              │
           structural   │              │ experiential
                        ▼              ▼
              ┌──────────────┐  ┌──────────────────┐
              │ WORLD MODEL  │  │ EXPERIENCE STORE │
              │ graph/tree   │  │ MD episodes L0   │
              └──────┬───────┘  └────────┬─────────┘
                     │          affect, promote, decay
                     │                   ▼
                     │          ┌──────────────────┐
                     │          │ KNOWLEDGE STORE  │
                     │          │ L1 session       │
                     │          │ L2 topics        │
                     │          │ learnings        │
                     │          │ instincts/habits │
                     │          │ L3 identity      │
                     │          └────────┬─────────┘
                     │                   │
                     └─────────┬─────────┘
                               ▼
                 ┌─────────────────────────────┐
                 │ WORKING MEMORY ASSEMBLER    │
                 │ budgeted, ranked, typed     │
                 │ → inject pre-tool / system  │
                 └─────────────────────────────┘
```

---

## 12. Behavioral definition of done

1. Preference said once → later sessions obey without re-telling.  
2. Correction once → old behavior suppressed and linked.  
3. “Why did we do X?” answered from L1/L2, not a function list.  
4. “What calls / references X?” answered from Tract A.  
5. Empty injection allowed; fake injection forbidden.  
6. L3 small and human-recognizable.  
7. Code and non-code feel the same for prefs/habits; structural sidebar differs.

---

## 13. Build order

1. Sensors + router + affect  
2. Working memory assembler (injection)  
3. Clean L0 + L1  
4. Learnings → instincts → habits + feedback  
5. Structural tract (Graphify / doc graph)  
6. L2 topics + typed links  
7. L3 identity + skill evolution  
8. Only then: fancier paging, heavy LLM summarizers, cross-project promotion

---

# Part B — Local small-model generation + separate embed index

## Design intent (corrected)

The Ollama / local-model stack is **not** “embeddings only.”

Crisp must distill bulk transcripts and tool traces into **sensible behaviour
memory** (preferences, corrections, lessons, L1 journals, habit candidates)
**without depending on Claude (or any host agent) being present or smart enough
to curate memory.**

That job is **local chat/generate** on a small model (e.g. Qwen 3B/7B via
Ollama). Embeddings are a **second** job: index what was written so it can be
found later.

```text
HOST AGENT (Claude / OpenCode / …)     optional — may be offline
        │
        │  hooks capture raw pulses
        ▼
LOCAL DISTILLER (always available if Ollama/HF generate is up)
        │  bulk messages → structured memory / behaviour
        ▼
MD + SQLite (FTS/vec)                  durable store
        │
        ▼
INJECT back into whatever host is running
```

**Constraint:** generation must work from a $0 local box. Host LLM is never
required for the learning loop.

## B1. Two different model jobs (same machine, different APIs)

| Job | API / stack | Purpose |
|---|---|---|
| **Generate / distill** | Ollama `/api/chat` or `/api/generate` → optional HF **causal LM** local generate | Turn bulk turns into prefs, corrections, lessons, L1 text, habit proposals |
| **Embed / index** | Ollama `/api/embeddings` → HF **sentence-transformers** → word2vec | Vectors for ANN search after write |
| **Rerank** | Formula first; optional cross-encoder later | Order candidates for WM |

```text
EXPERIENCE GENERATION (local small LM)     RETRIEVAL (embed chain)
─────────────────────────────────────     ─────────────────────────
bulk transcript / tool log                query
        │                                        │
        ▼                                        ▼
 rules gate (cheap) ──┐                   FTS5 keyword
 local generate  ─────┼─► structured      embed(query) → vec
 templates fallback ──┘   MD episode             │
        │                                   merge + rerank → WM
        └── then embed(episode) for index ──┘
```

**Word2Vec is embed-only.** It never generates memory text.  
**Sentence-transformers is embed-only.**  
**Ollama is dual-use:** chat model for distill, embed model for index —  
configure them as **separate** model names.

## B1b. Why local generate is first-class (not “optional polish”)

| Requirement | Implication |
|---|---|
| Works without Claude | Distill on SessionEnd / idle via local model |
| Works on OpenCode / Pi / CLI-only | Same distiller; host only supplies pulses + inject surface |
| Bulk messages → behaviour | Prompt: “extract prefs/corrections/decisions/lessons as JSON” |
| Cheap / always on | Prefer 3B–8B class (Qwen, Llama, Phi) on Ollama |
| Fail soft | If generate down → rules/templates only; never block hooks |

Hermes’s background review is the closest cousin — except Hermes often uses
the main chat model; **we deliberately use a small local model** so the memory
plane is host-independent.

## B2. The local distiller (core learning loop)

### B2.1 What gets fed to the small model

On SessionEnd / PreCompact / idle / `crisp reflect`:

```text
INPUT BUNDLE (capped, e.g. 8–24k chars)
  - last N user/assistant turns (transcript)
  - recent corrections/frustration signals (if any)
  - git subjects this session
  - optional: high-importance diff titles (not full symbol dumps)
  - current hot USER.md + MEMORY.md (so model can merge, not duplicate)
```

Host agent is **not** required to be online for this step. Hooks only need to
have written the raw transcript/pulses earlier.

### B2.2 What the small model must return

Strict JSON (schema-validated; reject free prose):

```json
{
  "preferences": [{"lesson": "...", "permanence": "permanent", "confidence": 0.0}],
  "corrections": [{"lesson": "...", "supersedes_hint": "...", "confidence": 0.0}],
  "decisions":   [{"lesson": "...", "confidence": 0.0}],
  "lessons":     [{"lesson": "...", "confidence": 0.0}],
  "hot_memory_patches": [
    {"target": "user|memory", "action": "add|replace|remove", "old_text": "", "content": ""}
  ],
  "l1_summary": "short session journal markdown",
  "habit_candidates": [{"lesson": "...", "context": "...", "confidence": 0.0}]
}
```

Then Crisp code (not the model) writes MD / hot profile / links / instincts.

### B2.3 Generation provider chain (host-independent)

```text
lib/generate.py  (new; parallel to lib/embeddings.py)

primary:  Ollama chat/generate
          model e.g. qwen2.5:3b | qwen2.5:7b | llama3.2:3b
          POST http://localhost:11434/api/chat

fallback: HuggingFace local causal LM (transformers)
          small instruct model on CPU/GPU if installed

last:     rules + templates only
          (regex affect + deterministic L1 journal)
          never block; never invent via embeddings

NEVER for generation:
  - /api/embeddings
  - sentence-transformers
  - word2vec / gensim
```

Same machine can run **two** Ollama models:

| Config key | Example | Endpoint |
|---|---|---|
| `generation_model` | `qwen2.5:3b` | `/api/chat` |
| `embedding_model` | `qllama/bge-large-en-v1.5:latest` | `/api/embeddings` |

### B2.4 When to use rules vs small model

| Signal | Path |
|---|---|
| Clear “always/never/prefer…” one-liner | Rules can catch immediately (low latency) |
| Bulk session, nuanced prefs, multi-turn correction | **Local generate** on sleep |
| Generate down / timeout | Rules + template L1 only |
| Explicit `crisp save` | Direct write; no model |

Rules are a **fast path**, not the only path. Local generate is the **default
for session distillation** when Ollama (or HF generate) is available.

### B2.5 L0 raw capture — still mostly deterministic

| Source | Who writes | Small model? |
|---|---|---|
| Explicit save | CLI | No |
| File diff / git / tool fail | watchers | No |
| Raw conversation checkpoint | hooks | No (store raw for distiller input) |
| Preference/correction **from bulk** | distiller JSON | **Yes** |
| Hot USER/MEMORY patches | distiller JSON | **Yes** |
| L1 journal | distiller `l1_summary` or template fallback | **Yes** preferred |
| Instinct text | from learning.lesson after reinforce | Indirect (from distill) |
| Code/doc graph | parsers / Graphify | No |

### B2.6 Instincts / habits after distill

```text
distiller emits habit_candidates + lessons
  → store as learnings
  → reinforce on recurrence / successful outcome
  → habit threshold → auto-inject + optional SKILL.md
```

Still **not** “Bash ran 20 times.” Frequency of tools ≠ behaviour.

### B2.7 Structural tract

Unchanged: parsers / Graphify only. Small model may later one-line a file
purpose; never the sole index.

## B3. Embed chain stays separate (`lib/embeddings.py`)

After distiller (or rules) **writes** an episode / hot entry:

```text
embed(text) → vec_sidecar / FTS index
```

| Moment | Generate (chat)? | Embed? |
|---|---|---|
| SessionEnd distill bulk → JSON → MD | **Yes** | After write |
| Live one-liner affect (rules) | No | After write |
| Search / inject retrieve | No | Yes (query + docs) |
| Link near-dupe / cluster assist | No | Yes |
| Graph build | No | No |

### Embed fallback (index only)

```text
ollama /api/embeddings → sentence-transformers → word2vec → keyword-only
```

### Generate fallback (distill only)

```text
ollama /api/chat → HF causal generate → rules/templates
```

Do **not** cross-wire: a failed chat call must not “fall back” into the embed
provider and pretend a vector is a summary.

## B4. Pipeline: preference without Claude in the loop

```text
1. During day: hooks append transcript + pulses (any host, or none mid-flight)
2. SessionEnd / cron:
     bundle bulk messages
     local qwen/ollama chat → JSON behaviour extract     # DISTILL
     write preference/correction episodes + hot patches
     embed new texts                                     # INDEX
3. Later, any host (Claude or OpenCode or CLI agent):
     PreToolUse / system inject hot + retrieved cold     # USE
4. User corrects → next distill or rules → corrected_by  # FEEDBACK
```

Claude is an **optional consumer** of memory, not the **author** of memory.

## B5. Pipeline: session sleep (default)

```text
1. Build input bundle (transcript + git + hot profile)
2. local generate → JSON (prefs, corrections, l1_summary, hot patches)
3. Validate schema; drop low-confidence / empty lessons
4. Apply hot patches (USER/MEMORY caps; consolidate if full)
5. Save L0 learnings + L1 from l1_summary (or template if generate failed)
6. embed allowlisted new texts; FTS index update
7. decay + instinct reinforce from learnings
8. bus: distill_ran {provider, model, n_prefs, n_corrections, fallback}
```

## B6. What we will not do

- Use embeddings to invent memory meaning.  
- Use word2vec as a summarizer.  
- Require Claude/OpenCode to call a `memory` tool for learning to happen.  
- Run full distill on every PostToolUse (too heavy) — batch on sleep/idle.  
- Feed `code_element` dumps into the distiller input bundle.  
- Fall back from chat failure into embed APIs.

## B7. Config sketch (target)

```json
{
  "generation_enabled": true,
  "generation_provider": "ollama",
  "generation_model": "qwen2.5:3b",
  "generation_api_url": "http://localhost:11434/api/chat",
  "generation_timeout_s": 120,
  "generation_max_input_chars": 24000,
  "distill_on": ["session_end", "pre_compact", "manual_reflect"],

  "embedding_provider": "ollama",
  "embedding_model": "qllama/bge-large-en-v1.5:latest",
  "embedding_api_url": "http://localhost:11434/api/embeddings",
  "embedding_dim": 1024,
  "embed_on_save": true,
  "embed_categories": [
    "preference", "correction", "decision", "lesson",
    "conversation", "git_commit", "summary", "instinct", "habit"
  ],

  "affect": {
    "mode": "rules_plus_distill",
    "rules_live": true,
    "distill_bulk": true
  },
  "hot_memory": {
    "user_char_limit": 1375,
    "memory_char_limit": 2200
  },
  "consolidation": { "l2_enabled": false, "l3_enabled": false },
  "injection": {
    "pre_tool_async": false,
    "include_tool_frequency_instincts": false,
    "max_tokens": 2500
  }
}
```

## B8. Implementation surface

| Module | Role |
|---|---|
| `lib/generate.py` | Provider chain: Ollama chat → HF causal → none |
| `lib/distill.py` | Bundle build, prompt, JSON parse, apply writes |
| `lib/embeddings.py` | Unchanged job: vectors only |
| `lib/affect.py` | Live rules fast-path |
| hooks SessionEnd | Call distill (async/background OK; don’t block forever) |
| `crisp reflect` | Manual distill + consolidate |

## B9. Where local generate sits in the cognitive layers

Use the **small local chat model at layer transitions and hot curation** —
not on every tool pulse, not inside L_w assembly.

```text
L_w  Working memory        NO generate on critical path
                           assemble hot + FTS/vec retrieve (must stay fast)

L0   Episodic raw          mostly NO generate
                           hooks/watchers write diffs, git, raw transcript
                           rules may tag obvious one-liner prefs live

        ╔══════════════════════════════════════════════╗
        ║  LOCAL DISTILL (SessionEnd / reflect / idle) ║  ← Qwen/Ollama chat
        ║  bulk L0 + transcript → structured JSON      ║
        ╚══════════════════════════════════════════════╝
              │
              ├─► hot USER / MEMORY patches
              ├─► preference / correction / decision learnings
              ├─► L1 session journal
              └─► habit_candidates → reinforce path → L3 / skills

L1   Session semantic      YES — primary home of distill `l1_summary`
                           fallback: template if generate down

L2   Topic / semantic      light generate later (merge topic cards)
                           clustering may use embeds; prose from generate or bullets

L3   Procedural / identity generate only to merge/word habit text
                           promotion rules decide *what* becomes habit/skill

Tract A code/doc graph     NO chat model required (parsers / Graphify)
```

### Layer × model matrix

| Layer / job | Local **chat** (Qwen) | Local **embed** | Rules/templates | Parsers |
|---|---|---|---|---|
| Capture L0 | — | after write | live affect | — |
| Sleep distill | **primary** | after write | fallback | — |
| Hot USER/MEMORY | **patches from distill** | optional | consolidate on full | — |
| L1 | **yes** | yes | fallback | — |
| L2 | optional merge | cluster assist | group by tag | — |
| L3 / habits | optional wording | — | promote thresholds | — |
| L_w inject | — | retrieve | rank/budget | graph snippet |
| Code graph | — | — | — | **yes** |

## B10. Learning and feedback — Hermes vs Crisp (honest)

### Does Hermes learn and Crisp not?

**Hermes learns in production** (by design of the runtime):

1. Agent (or background review LLM) writes USER.md / MEMORY.md / skills  
2. Those are injected next session → behaviour can change  
3. User can correct; agent can replace/remove memory  
4. Session FTS lets it recall past chats on demand  

**Crisp today mostly records; it barely learns:**

| Loop piece | Hermes | Crisp **code now** | Crisp **target** |
|---|---|---|---|
| Capture raw events | yes (is the agent) | **yes** (hooks/watchers) | yes |
| Turn bulk → meaning | background review LLM | **no** (templates + tool counts) | local Qwen distill |
| Write durable prefs/lessons | yes | **almost never** (0 corrections in live store) | distill + rules |
| Put knowledge **back** into next turn | system prompt freeze | **partial** `build_context_block` (often junk/async) | hot + ranked inject |
| Feedback if inject helped | agent conversation + memory tool | **no automatic loop** | correct → weaken; success → reinforce |
| `reinforce` / `weaken` | via memory/skills edits | **API exists, never auto-called** | wire to outcomes |
| `update_access` on retrieve | N/A (always-on hot) | **exists, not on inject path** | call on every inject hit |

So: **Hermes has a closed learning loop. Crisp has open pipes (in + weak out) and almost no “meaning” step in the middle.**

It is not that Crisp is *incapable* of feedback — pieces exist — they are **not wired into one loop**.

### What “put things back” means (efferents)

```text
CAPTURE ──► DISTILL ──► STORE ──► INJECT ──► HOST ACTS
                              ▲                │
                              └── FEEDBACK ────┘
                                   user corrects / task ok / ignored
```

| Direction | Crisp now | Needed |
|---|---|---|
| **In** (afferent) | Strong: hooks, watchers, transcripts, embeds | Keep; stop junk categories |
| **Out** (efferent inject) | Weak: PreToolUse may be async; content is instincts/symbols | Sync inject; hot prefs/corrections only |
| **Feedback** | Manual CLI `instinct reinforce/weaken` only | Auto: correction episode → weaken related habit; successful use → reinforce; access_count on inject |

Without **out + feedback**, capture is a diary, not a nervous system.

### Target closed loop (Crisp)

```text
1. Host works; hooks capture pulses + transcript          (IN)
2. SessionEnd: local Qwen distills → hot + L1 + learnings (MEANING)
3. Next turn: inject hot + retrieved cold into host       (OUT / put back)
4. If user says "no/wrong":
     correction episode + corrected_by + weaken habit     (FEEDBACK)
5. If behaviour succeeded with habit in context:
     reinforce habit / bump access                        (FEEDBACK)
6. Strong habits → SKILL.md artifact                      (COMPILE)
```

Hermes does 2–4 largely **inside one agent**.  
Crisp must do 2 with a **local model** and 3–5 across **whatever host** is attached — that is the product difference, not “we don’t learn.”

### Bottom line

| Claim | Verdict |
|---|---|
| Layer placement of local generate is in this doc | **Yes — §B9** (added) |
| Hermes learns today | **Yes** |
| Crisp learns today | **Barely** — records + weak/noisy inject; no real distill; feedback APIs idle |
| Crisp has *no* way to put things back | **False** — inject path exists; it is incomplete and poorly fed |
| After target loop | Crisp learns **without** needing Claude to be the teacher |

## B11. Summary

| Question | Answer |
|---|---|
| How are experiences generated? | **Local small chat model** on bulk messages (+ rules fast-path + templates fallback) |
| Why not rely on Claude? | Memory plane must work from anywhere; host may be absent or dumb about memory |
| Role of Ollama? | **Dual:** chat model distills; embed model indexes |
| Role of HF? | Causal LM generate fallback **or** sentence-transformers embed fallback — different code paths |
| Role of W2V? | Embed last resort only |
| When does distill run? | SessionEnd / PreCompact / idle / `crisp reflect` — not every tool call |
| Where in layers? | Sleep boundary: L0 bulk → hot + L1 + learnings/habits (**§B9**) |
| Feedback today? | Mostly missing in the live path (**§B10**) |

---

## 14. References

- Salvage / phasing: `docs/adr-memory-mvp-rebuild.md`  
- Live failure evidence: `memory/AUDIT.md`  
- Embed implementation today: `memory/lib/embeddings.py` (generate is **new**)  
- Hermes background review: cousin pattern; we use **local small model**, not host LLM  
- Graphify: Tract A backend candidate  
