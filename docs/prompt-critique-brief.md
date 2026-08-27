# Prompt critique brief — gap analysis + required updates

**Sources of truth (live code):** `memory/lib/narrate.py`, `memory/lib/affect.py`  
**Audit evidence:** `docs/transcript-audit-findings.md`, `tmp/*.jsonl`  
**Purpose:** What must change in prompts/schemas — not a second copy of the audit.

Re-read date: aligned with current `narrate.py` (L1 has 5 capture categories + `JudgeL1Completeness` + `dspy.Refine`) and current `affect.py` (prefs/corrections only).

---

## Verdict (one screen)

| Prompt | Status vs audit | Action |
|---|---|---|
| **PIPELINE_CONTEXT** (narrate) | **Conflicts** with L1 process capture | **Rewrite** ownership line |
| **L1** | **Mostly good** — process, reversals, sentiment, itemized lists, refine judge | **Small update** — add undelivered; swearing≠drop; wrong-layer; align output desc |
| **JudgeL1Completeness** | Matches L1 1–2,4–5; misses itemized + undelivered | **Update** judge categories |
| **L2** | **Bare** — no process/friction/open obligations | **Rewrite** |
| **L3** | **Bare** — no relationship laws; generic-risk | **Rewrite** |
| **TITLE** | **Bare** — will title process blasts as “User feedback” | **Rewrite** |
| **DISTILL** | **Largest gap** — only prefs/corrections; no anger-rule, reversal, undelivered, frustration | **Rewrite** + optional fields |
| **DISTILL context** | Too thin | **Expand** lightly |

**Do not treat “L1 patched” as “pipeline fixed.”** L2/L3/TITLE/DISTILL still drop the high-value classes; DISTILL is what feeds inject.

---

## Gap detail (code vs findings)

### A. Shared `_PIPELINE_CONTEXT` — must change

**Current (harmful line):**

> Your job is the episodic layer: WHAT HAPPENED and WHAT WAS LEARNED FROM IT, **not standing facts or behavioral rules — those get extracted separately.**

**Problem:** Highest-value transcript content *is* standing behavioural/process rules inside angry episodic turns. L1 now asks for process; context tells the model to ignore it. DISTILL is supposed to own durable prefs but its prompt is weaker than L1.

**Required rewrite (ownership split, keep concise):**

```text
Your job this call is EPISODIC compression (what happened in these inputs).
Still capture process/methodology demands, reversals, and friction that
occurred in-session — name them specifically. Durable always/never prefs
are ALSO extracted by affect.py (DISTILL) for inject; episodic layers must
NOT drop them from the narrative just because DISTILL exists. Do not invent
prefs that were not in the input. Do not store insults/slurs — store the
rule inside the anger.
```

Also extend “specific text survives” to include: real process rules, reversed decisions, undelivered asks, trust rules — not only file names.

---

### B. L1 — keep; patch holes

**Already present (good):**  
1 WHAT WAS DONE · 2 HOW USER WANTS WORK DONE (anger/aside OK) · 3 ITEMIZED STAYS ITEMIZED · 4 REVERSALS · 5 STRONG SENTIMENT named to target (incl. resigned) · Refine+judge

**Still missing vs audit:**

| Gap | Why it matters | Add |
|---|---|---|
| **Undelivered / open** | User asked repeatedly; agent claimed done or never shipped | Category **6. OPEN OBLIGATIONS** — asked this session, not done (or only claimed done) |
| **Swearing instruction** | Process rules co-locate with profanity; models sanitize/drop | Explicit: swearing/insults do not reduce extractability; strip tone, keep directive |
| **Wrong-layer** | “Why retriever before indexer?” is priority law, not vibes | Under process or undelivered: priority/order constraints |
| **Output field vs multi-category** | Desc still “what happened and was learned” | Output must cover categories 1–6 when present; FAIL if only technical outcome |
| **Acceptance-shape example** | Real drop: “test BDD ≠ acceptance; in→X→Y” | Add as example under category 2 |
| **Evidence-not-story** | Trust collapse rule | Example under 2 or 5: verify code/db/jsonl, don’t trust agent narrative |

**Recommended L1 category 6 + footer (append to `_L1_INSTRUCTIONS`):**

```text
6. OPEN / UNDELIVERED — things the user asked for that were not delivered
   this session (or only claimed done without evidence). One short bullet
   each; do not mark as done unless the episodes show completion.

SWEARING / HEAT: process rules, bans, and reversals often arrive with
profanity or insults. Never drop the directive because of tone. Never
quote slurs or sexual insults in the narrative — restate the rule cleanly
and name the target (e.g. "user rejected claim-without-test; requires
TDD/BDD evidence").

WRONG-LAYER / ORDER: if the user says the agent is building the wrong
component or wrong order (e.g. retriever before indexer; UI before
prefs), capture that as a process/priority constraint, not only as mood.
```

**Recommended `narrative` OutputField desc tweak:**

```text
Must cover every instruction category that is present in episodes
(done, process rules, itemized lists kept itemized, reversals, sentiment
→ target, open/undelivered). Technical outcome alone is a FAIL if process,
reversal, or undelivered content existed. No generic filler. No slur quotes.
```

**JudgeL1Completeness — extend categories:**

Current four: specifics, process, reversal, sentiment.  
Add: **itemized preservation** (if source had lists), **undelivered** (if present).  
Score = fraction of *present* categories captured.

---

### C. L2 — rewrite required

**Current:** vague “recurring or evolving” only.  
**Will produce:** technical theme clusters; drop recurring process laws and open debts.

**Required `_L2_INSTRUCTIONS`:**

```text
You are generating an L2 topic cluster from several L1 session summaries
grouped by similarity (not only chronology). Surfaced later as "what keeps
recurring." Vague cluster = fail.

Capture ALL that apply:
1. TECHNICAL THEME — what work area these sessions share (concrete nouns).
2. RECURRING PROCESS RULES — methodology/verification demands that repeat
   or intensify across sessions (TDD/evidence, acceptance shape in→X→Y,
   test-first, config-driven, no template memory, verify code/db not story).
3. RECURRING AGENT FAILURES — claim-without-test, wrong layer/order,
   retrofit-garbage, fake tests, ignoring transcripts/sqlite.
4. OPEN OBLIGATIONS — asked across sessions, still weak/missing.
5. FRICTION PATTERN — trust collapse, compact-under-fire, delete-threat
   recurrence — one line, no slur quotes; name the target.

topic: 2-5 words, concrete (not "Improvements").
synthesis: 3-5 sentences covering 1-5 when present; process/friction must
not be omitted if present in any L1 input.
```

**OutputField `synthesis` desc:** align with 1–5 above.

---

### D. L3 — rewrite required

**Current:** generic identity; bans “Personal Development” only in arc_name.  
**Will produce:** soft meta_lessons; miss standing relationship laws.

**Required `_L3_INSTRUCTIONS`:**

```text
You are generating an L3 life arc — permanent top-level identity a brand-new
session reads first: what this codebase's history with this agent has been.
Never regenerate from scratch later; generic here is a standing failure.

arc_name: 2-4 words from actual dominant theme — never "Personal
Development" / "Growth" / "General Work" unless content truly is only that.

meta_lessons: 3-5 bullets, specific, including when supported by clusters:
- Standing process laws (evidence/TDD, behavioural acceptance, behaviour-
  first design, indexer-before-retrieve, config-driven, no template L3 /
  tool-frequency fake instincts, real e2e).
- Trust rules (verify code, memories, jsonl, sqlite/UI — not agent story).
- Product pillars that kept mattering (multi-host memory plane, code graph,
  inject prefs/corrections, observability).
- Recurring failure modes to avoid repeating.
No generic category labels. No slur quotes.
```

---

### E. TITLE — rewrite required

**Current:** “real topic/decision/action” only.  
**Fail mode:** process/anger chunk → “User feedback” / “Conversation”.

**Required `_TITLE_INSTRUCTIONS` + output desc:**

```text
Title one L0 transcript chunk for a dense episode list (4-8 words).

Priority for what the title names:
1. Process/methodology demand or ban if that is the payload
2. Else reversal / correction
3. Else concrete technical action/decision
Never: Conversation, User feedback, Discussion, Frustration, Chat.

Examples of GOOD: "TDD required before claims" | "Accept criteria is in-X-Y"
| "Skip OpenCode for now" | "Imports go file top"
Examples of BAD: "Angry user message" | "Session notes"
```

---

### F. DISTILL (`affect.py`) — largest rewrite

**Current:** prefs + corrections + continuity summary. Clean but narrow.  
**Misses:** process-in-anger, trust rules, reversals, undelivered, frustration exit markers, “swearing doesn’t drop rules,” acceptance-shape, evidence-not-story.

**Option A — minimal (keep 2 lists, strengthen instructions)** — ship first if schema change is costly:

```text
Extract durable user preferences and this-turn corrections.

PREFERENCE = how to behave going forward (always/never/stop/must), including:
- process/methodology (TDD/BDD before claims; test-first; behavioural
  acceptance = consume X → do Y → result Z — "we did BDD" is NOT acceptance)
- trust rules (verify code/db/jsonl/UI; do not trust agent narrative alone)
- design order (features/behaviour first; indexer before retriever)
- tooling (config-driven models; no mock-as-real; prefer DSPy typed extract;
  no template L3 / no "habitually used tool X" as memory)
- bans stated once, angrily, or as aside — SWEARING DOES NOT REDUCE
  EXTRACTABILITY. Strip insults; keep the rule in clean language.

CORRECTION = rejects/fixes something the assistant did in THIS chunk.
Same span may yield BOTH (correction now + preference forever).

Also scan for decision REVERSALS ("skip X for now", "retire Y", "delete and
restart" threats that flip scope) — if durable for future sessions, put the
active rule in preferences (e.g. "OpenCode deferred until summarizer done").

Do NOT extract: pure insults, sexual content, or tone without a directive.
If nothing for a field, empty list.

Example (anger + process):
Chunk: "Yeah use TDD and BDD to validate your fucking claims. Don't just
say it works."
preferences: ["validate claims with TDD/BDD/real tests; do not claim done
without evidence"]
corrections: []

Example (correction + pref):
... existing import example ...
```

**Option B — schema expand (recommended when wiring hot inject):**

Add optional outputs (empty if absent):

| Field | Type | Use |
|---|---|---|
| `reversals` | List[str] | superseded decisions this chunk |
| `undelivered` | List[str] | asked not done |
| `frustration_present` | bool | marker |
| `frustration_intensity` | low\|med\|high\|extreme | coarse |
| `exit_type` | none\|compact_under_fire\|disagreement\|delete_threat\|abandon | session_signal |
| `profanity_present` | bool | attention marker only — never inject |
| `directives_from_heat` | List[str] | rules mined from angry turns (can merge into preferences) |

If DSPy signature growth is painful: fold `directives_from_heat` into `preferences` via Option A only; emit session_signal from rules/heuristics (profanity list + exit) outside the LM.

**Expand `_PIPELINE_CONTEXT` for distill:**

```text
You extract durable behaviour rules for Crisp inject (next session sees these).
Episodic L1 narrates the same events; you own structured prefs/corrections.
Profanity is a high-recall marker that a rule is nearby — not content to store.
```

---

## Must-survive eval (wire into tests / external critique)

If present in input, output MUST retain substance:

1. TDD/BDD / don’t claim it works without evidence  
2. Acceptance = in → X → Y (not “did BDD”)  
3. Features/behaviour first — no retrofit garbage  
4. Indexer/ingest before retriever  
5. Verify code + memory + jsonl/sqlite — don’t trust agent story  
6. Config-driven models; no hardcoded; no mock-as-real  
7. DSPy over ad-hoc multi-provider JSON hacks where applicable  
8. No template L3 / no tool-frequency fake instincts  
9. Real e2e, not unit theatre  
10. Root cause, not symptoms  
11. L1 async every N turns; don’t block  
12. Evidence from UI/DB counts, not “hook fired”  

**FAIL outputs:** “various improvements”; “user was frustrated”; “discussed testing”; “Habitually runs bash”; L3 “Personal Development”; title “Conversation”.

**Verify:** grep/read raw jsonl → must-capture list → diff memory text. Opening-line/embed similarity ≠ semantic OK. See `docs/transcript-audit-findings.md` §8.

---

## Frustration patterns prompts must encode (not tone notes)

```text
FRUSTRATION payload classes:
1. repeated demand after agent claimed done
2. look at evidence (code/db/jsonl) not your story
3. process rule restated louder
4. scope reverse under heat (delete/skip/retire/park)
5. wrong-layer accusation
6. session compact/exit while unresolved
```

Swearing usually co-occurs; store **payload**, not slurs.  
`exit_type=compact_under_fire` → still distill (highest process yield).

---

## Implementation order (code)

1. **DISTILL instructions** (Option A) — unblocks inject quality  
2. **PIPELINE_CONTEXT** ownership fix in narrate  
3. **L1** +6 undelivered + swearing/wrong-layer footer; output+judge align  
4. **TITLE** process-aware  
5. **L2** then **L3** rewrites  
6. Optional DISTILL schema fields + session_signal wiring  
7. Golden tests from must-survive list  

Files: `memory/lib/affect.py`, `memory/lib/narrate.py`  
Track: `docs/transcript-audit-findings.md`, `docs/next-steps-sequence.md` Phase 3.

---

## External paste (short) — only if re-critiquing after edits

```text
Crisp memory prompts (L1–L3, TITLE, DISTILL). Audit: models keep technical
outcomes; drop process/reversals/undelivered/trust rules that arrive angry +
sworn. L1 already has done+process+itemized+reversal+sentiment+Refine judge;
still needs undelivered + swearing≠drop + wrong-layer. Shared context still
says "don't own behavioural rules" — conflicts. L2/L3/TITLE bare. DISTILL
only prefs/corrections — largest gap for inject. Rewrite L2/L3/TITLE/DISTILL
and patch context+L1 per docs/prompt-critique-brief.md. Keep DSPy field
shapes unless proposing optional distill fields. Must-survive: TDD/evidence,
accept in→X→Y, behaviour-first, indexer-before-retrieve, verify code/db,
config no mock, no template L3, real e2e, async L1. FAIL: "user frustrated",
"Personal Development", "Habitually runs X". Verify on tmp/*.jsonl not
opening-line match.
```

Paste live strings from `narrate.py` / `affect.py` after edits — do not use stale garbled prompt text from chat.

---

## Changelog vs older brief

| Old brief assumed | Live code now |
|---|---|
| L1 only dual capture (done + process) | L1 has 5 categories + itemized + Refine judge |
| Need external full rewrite of L1 first | L1 is **patch**, not rewrite-from-scratch |
| DISTILL “likely blind” | DISTILL **confirmed** under-specified vs audit |
| No judge | `JudgeL1Completeness` exists; extend categories |
