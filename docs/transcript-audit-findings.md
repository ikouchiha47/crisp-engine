# Transcript audit: frustration signals Crisp's L1/distill pipeline must not drop

**Sources:** `tmp/cc588451-9ac2-4267-b2e2-d323f9db2811.jsonl` (~186 clean user turns), `tmp/f2c4051a-95f0-4c17-97c9-61ab4f1658fa.jsonl` (~44 clean user turns)
**Related:** `docs/prompt-critique-brief.md`, `memory/lib/narrate.py`, `memory/lib/affect.py`
**Audit method:** full user-turn extraction from jsonl (not opening-line / L1 similarity). Keyword greps alone are insufficient; substance was read from the full turn list, plus a second independent pass, cross-checked against what `narrate_l1`/`distill_transcript` actually generated from the same content.

---

## 1. Core failure mode (confirmed)

Summarization/extraction reliably keeps **technical work** (files, bugs, features named) and drops:

1. **Process / methodology directives** (often stated once, angry, as an aside)
2. **Decision reversals** made under pressure
3. **Undelivered asks** (requested ≠ done)
4. **Frustration / disagreement exits** (almost always **accompanied by swearing** in these sessions)
5. **Trust-collapse rules** ("don't believe the agent — read code/db/jsonl")

L1 was later patched for (1). L2, L3, TITLE, DISTILL were not transcript-audited the same way and share the blind spot (or worse: they never receive process text if L1/DISTILL dropped it first).

## 2. Signal taxonomy (what memory should capture)

Not "the user swore." Swearing is a **co-occurring intensity marker**, not the signal itself — the signal is one of these classes, and losing the class loses a real, actionable directive, not just tone.

| Class | Definition | Store as | Example shape |
|---|---|---|---|
| **Process rule** | How work must be done going forward | preference + L1 process bullet | TDD/BDD before claims; test-first; behavioural acceptance |
| **Standing ban** | always/never/stop | preference | no mock-as-real; no OpenAI embedder; no stylized dashes |
| **Correction** | rejects this turn's agent action | correction (+ maybe preference) | import at top of file |
| **Decision reversal** | earlier plan flipped | L1 + preference/decision episode | skip OpenCode for now; retire old summarizer; park Ollama |
| **Undelivered ask** | user asked; not shipped | L1 "asked, not done" | webui traces, real prefs inject, treesitter graphs |
| **Trust collapse** | stop trusting agent narrative | preference + frustration signal | verify code + memories + semanticity |
| **Frustration exit** | session/compact ends unresolved + heat | session_signal / affect | forced compact under fire; delete-and-restart threat |
| **Wrong-layer rage** | agent building wrong component | L1 priority lesson | retriever before indexer |
| **Profanity co-occurrence** | swearing near any of the above | marker only (boost extract attention) | do not store insults as "lessons" |

**Swearing note:** In this corpus, high-value process rules and reversals are **usually co-located with profanity**. Treat profanity as a **high-recall marker** that a durable directive or exit signal is nearby — not as the thing to store. User intent: *existence of profanity / disagreement matters more than the specific swear words.* A resigned, profanity-light line ("no fuck it then, there's no differentiator, no point building this anymore") carries the same weight as an overt angry one ("I will fucking set the whole neighborhood on fire") — confirmed directly this session: the L1 prompt initially only caught the loud version, not the resigned one, until corrected to cover both registers explicitly.

## 3. Standing rules that ride inside frustration (must survive)

These appeared as **angry** user turns; memory must keep the **rule**, not the insult.

1. Validate claims with **TDD/BDD / real tests** — do not claim it works without evidence (cc L486).
2. **Acceptance criteria** = behavioural pipeline: consume X → do Y → result Z — not "we did BDD" (cc L896–913).
3. **Features / behaviour first** — do not take garbage code and fit features into it (cc L668).
4. **Code indexer / ingest before retriever** (cc L1003, L36-ish arc).
5. **Don't trust agent analysis** — look at code, memories, semanticity, sqlite, jsonl (f2 L40; cc L390, L7074, L7990).
6. **Test first**; each module tested (cc L778).
7. **Config-driven** model names/URLs/params — not hardcoded (cc L3412–3424; f2 L1568).
8. Embed path: **HF + Ollama (+ DSPy)** — user rejected OpenAI add; prefer configurable/DSPy (cc L3271).
9. **No mock** provider dressed as real (cc L3307).
10. Summarizer: **retire conflicting old design**; clarify code-summary vs episode-summary; **park Ollama** if agent+git+treesitter can cover (cc L2732–2743).
11. **SessionStart after code indexing** is finished (cc L1297) — order constraint.
12. **Skip OpenCode for now** when focusing summarizer (cc L2548) — temporary scope reverse.
13. L1 consolidation: every **20–40 turns, async**, don't block normal ops (cc L7103).
14. Fix **root cause**, not symptoms (cc L6828).
15. **Real e2e tests**, not fake/partial theatre (f2 L2859–2866).
16. **Little/no template L3** — "Used X tool" instincts are useless (f2 L2560; cc L7990).
17. Naming: project **Crisp** not huh mismatch (cc L2586); commits **without** co-authored-by (cc L4583); plain ASCII punctuation in skills/readme (cc L4613, L6018).
18. Prefer **DSPy** typed signatures over hand-rolled multi-provider JSON stripping (f2 L2912–2954).
19. Graph: don't index `.venv`; graph must be **queryable** (f2 L532).
20. Paginate **all** raw transcript turns into L0 (every 20–30 messages) — sparse snapshots are a structural bug (f2 L3487–3509).

## 4. Decision reversals (explicit flips)

| Session | Approx | Was / toward | Became |
|---|---|---|---|
| cc | L652 | keep fixing in place | threat: **delete all, start fresh** if not a real product |
| cc | L009→L1297 | finish SessionStart early | **wire SessionStart only after code indexing done** |
| cc | L2548 | multi-host (OpenCode) in parallel | **skip OpenCode for now** — summarizer first |
| cc | L055–067 | agent does summaries; Ollama path | agent doesn't call huh → need reliable path; then **retire old summarizer**, split code vs episode, **park Ollama** |
| cc | L3271 | embed providers including OpenAI | **HF + Ollama + DSPy/config** — who asked for OpenAI? |
| cc | L3307 | mock embed fallback | **why mock?** word2vec only if nothing else |
| cc | L5108 | broad Hermes salvage | **cross-session user model only** — don't need the rest |
| cc | L7103 | pre-turn L1? | **every 20–40 turns, async** |
| f2 | L92 | continue on bad code | consider **delete codebase, start new** |
| f2 | L1754 | Phase 2 hot inject first | **affect.py first** — nothing to inject without prefs |
| f2 | L1905 | custom stack pride | README: **deprecated in favor of supermemory** exploration |
| f2 | L2137 | larger model for SM analysis | **0.8b** — only need to trace workflows |
| f2 | L2912 | custom ollama/openai chat providers | **just use DSPy** |

**Prompt implication:** L1/DISTILL need an explicit "**reversed or superseded decisions**" bullet. L2 should surface **recurring reversals** (e.g. "agent keeps building UI before memory meaning").

## 5. Frustration signals (with swearing co-occurrence)

### 5.1 How to score frustration (without storing insults)

```text
frustration_signal = {
  intensity: low|med|high|extreme,   # high/extreme usually have swearing in this corpus
  markers: [profanity_present, caps, repetition, insult_agent, delete_threat],
  exit_type: none|compact_under_fire|disagreement|delete_threat|session_abandon,
  payload: [process_rules[], corrections[], reversals[], undelivered[], trust_rules[]],
}
```

- **Do store:** `payload` + `exit_type` + coarse `intensity`.
- **Do not store:** slur lists, sexual insults, as "lessons" or inject text.
- **Do use:** `profanity_present=true` as extract-attention boost (re-read turn for rules).

### 5.2 Clusters in `cc588451`

| Cluster | Lines (approx) | Frustration type | Payload to keep |
|---|---|---|---|
| TDD fire | L486 | process rage + threat | TDD/BDD validate claims; indexer should work from day one |
| Garbage workflow | L662–668 | product aim + process | features/behaviour first, not retrofit garbage; codeindex + 3-stage memory + multi-host |
| Acceptance | L896–935 | process + wrong work | 1-line behavioural acceptance; stop side quests |
| Indexer vs retrieve | L1003 | wrong-layer | indexer/ingest before retriever |
| Git not watched | L4721–4748 | broken capture | GitWatcher not just commit; why cat not git |
| UI/truth | L6114–7182 | observability | live bus UI; L0–L3 audit; feedback story missing |
| SQLite fear | L7074–7103 | trust + async L1 | look at DB; async L1 20–40 turns |
| Academic lie | L7487–7531 | claimed vs real | layers missing; UI broken; prefs not learned |
| Semantic audit | L7990–8126 | trust + missing graph | check pre-compact store semantically; no treesitter graphs |
| Abuse spiral | L8251–8320 | exit only | frustration_exit; **no product payload** |

### 5.3 Clusters in `f2c4051a`

| Cluster | Lines | Type | Payload |
|---|---|---|---|
| Investment damage | L4 | trust/damage | salvage honesty: near Hermes+graphify+instincts? |
| Don't believe Claude | L40 | trust rule | code + memories + semanticity |
| Delete? | L92 | reversal threat | bad code contaminates |
| Affect first | L1754 | priority under despair | Phase 3 affect before hot inject |
| Missed supermemory | L1905 | research trust hit | study SM consolidation; deprecate note |
| Template L3 | L2560 | quality rage | no template arcs; memory graph like code graph |
| Fake tests | L2859–2866 | process | real e2e |
| L1 not firing | L3430–3539 | pipeline broken | check jsonl; paginate all turns; useless titles; stop context-budget excuse |

### 5.4 Compact / disagreement exits

PreCompact often ran while the user was mid-conflict (`/compact` with angry args). Treat as:

- `exit_type=compact_under_fire` when recent turns have high frustration markers
- Still run distill on those turns — **highest yield for process rules**

## 6. Undelivered asks (requested repeatedly, weak or missing in product)

Use these as L1 "open obligations" and as eval cases:

- Working **code index + treesitter graphs** queryable to the agent
- **Cursor-like search** / telescope-class code search
- **Tool-call + args history** memory (CLI invocations)
- **WebUI traces** for commits/diffs/inferences + episodic layers
- Real **preference/correction inject** (not "habitually runs grep")
- **GitWatcher** catching add/commit during session
- **Cross-host** adapters (Claude / OpenCode / pi) without hard couple — OpenCode deferred then resumed
- **User model** from frustration/disagreement sessions
- **Semantic verification** of L0/L1 against raw jsonl
- Paginated full-transcript L0 (not sparse 6 episodes / 2 weeks)

## 7. Prompt / schema suggestions

### 7.1 Shared pipeline context — fix the contradiction

Current narrate context says episodic must **not** own standing behavioural rules (those go to affect/instincts). But the highest-value drops **are** standing behavioural/process rules, and they often appear only inside angry episodic turns.

**Suggestion:**

- Episodic L1 **must still narrate** process directives and reversals that occurred *in this session* (what was demanded, what flipped).
- DISTILL **must extract** durable prefs/corrections/bans into structured lists for inject.
- L2/L3 **promote recurrence** of process rules and trust failures across sessions — not only "what was built."
- Instincts stay frequency/procedural promotion — not a dumping ground for tool-name noise.

### 7.2 L1 — keep dual capture; tighten output field

Already asks WHAT WAS DONE + HOW USER WANTS WORK DONE. Also require:

3. **REVERSALS** — decisions changed this session
4. **OPEN / UNDELIVERED** — asked but not done
5. **FRICTION** — one line if session had high frustration/disagreement (without quoting slurs)

Output field text should not say only "what happened and was learned" in a way that fights categories 2–5.

### 7.3 L2 — add recurring process + friction

Synthesis must answer:

- What **process rules** keep reappearing?
- What **failures of agent behaviour** recur (claim without test, wrong layer, template memory)?
- What **product obligations** stay open across sessions?

### 7.4 L3 — identity = working relationship, not "Personal Development"

`meta_lessons` should include standing relationship laws, e.g.:

- User requires evidence from code/DB/transcripts over agent narrative
- Template memory and tool-frequency instincts are rejected
- Multi-host memory plane + code graph are product pillars

### 7.5 TITLE — process-aware

If a chunk is mostly a process blast, the title should say so:

- Good: `TDD required before claims`
- Bad: `User feedback` / `Conversation`

### 7.6 DISTILL — expand extraction shape

Current: `preferences[]`, `corrections[]`, `summary`.

**Add (recommended):**

```text
preferences: List[str]           # durable always/never/how
corrections: List[str]           # this-turn rejects
reversals: List[str]             # superseded prior decisions (optional field)
undelivered: List[str]           # asked not done (optional)
frustration: {
  present: bool,
  intensity: low|med|high|extreme,
  exit_type: none|compact_under_fire|disagreement|delete_threat|abandon,
  profanity_present: bool,       # marker only
  directives_in_frustration: List[str]  # rules mined from angry turns
}
summary: str                     # continuity for next chunk
```

If keeping two lists only, **force**:

- Prefer extracting process bans from angry turns into `preferences`
- Prefer "no, stop X" into both `corrections` and `preferences` when durable
- Explicit instruction: **swearing does not reduce extractability** — strip tone, keep rule

### 7.7 Must-survive eval phrases (from real transcripts)

| Phrase / substance | Layers that must retain |
|---|---|
| TDD/BDD to validate claims; don't claim it works | L1, DISTILL pref, TITLE if chunk-local |
| Acceptance = history → X → Y | L1, DISTILL |
| Features/behaviour first, not fit garbage | L1, L2 if recurrent |
| Don't believe Claude — code + memories + semanticity | L1, DISTILL, L3 |
| Test first / each module tested | L1, DISTILL |
| Config-driven models; no static | L1, DISTILL |
| No OpenAI embedder; DSPy/config | L1, DISTILL |
| Retire old summarizer; park Ollama | L1 reverse |
| Skip OpenCode for now | L1 reverse |
| Async L1 every 20–40 turns | L1, DISTILL |
| No template L3 / no "used tool X" instincts | L1, L2, L3, DISTILL |
| Real e2e not fake tests | L1, DISTILL |
| Import at top of file | DISTILL correction+pref |
| Paginate all jsonl turns into L0 | L1, structural |

**FAIL examples:** "various improvements"; "user was frustrated"; "discussed testing"; "Habitually runs bash"; L3 "Personal Development".

## 8. Verification protocol (do not skip)

Never call L1/L2/L3/DISTILL "semantically correct" unless:

1. Extract clean user turns from `tmp/*.jsonl` (or the live session jsonl).
2. Grep + read for: `TDD|BDD|always|never|stop|verify|test first|acceptance|instead|don't believe|semantic|delete|skip|retire|config|dspy|template|e2e`.
3. Build a **must-capture list** (rules, reversals, undelivered, trust).
4. Note frustration turns: `profanity_present` + whether a **directive** sits in the same turn.
5. Diff against generated memory text — missing must-capture = **FAIL**.
6. Opening-line similarity or embed distance to summary is **not** verification.

## 9. Suggested ownership split

| Content | L0 raw | DISTILL | L1 | L2 | L3 | Inject hot |
|---|---|---|---|---|---|---|
| Transcript chunk | yes | — | — | — | — | no |
| Pref / ban / process rule | optional tag | **primary** | narrate | recur | identity | **yes** |
| Correction this turn | yes | **primary** | narrate | — | — | **yes** |
| Reversal | yes | optional | **primary** | recur | if standing | if still active |
| Undelivered | yes | optional | **primary** | open obligations | — | maybe |
| Frustration exit | session_signal | markers + directives | one line | trust pattern | relationship | no insults |
| Code graph facts | code index | — | — | — | — | compact neighborhood only |
| Tool-frequency "habit" | observe buffer | — | no | no | no | **no** until real lesson |

## 10. Files to update when acting on this

| File | Action |
|---|---|
| `docs/prompt-critique-brief.md` | Already has paste-ready external critique; keep in sync with §7 |
| `memory/lib/narrate.py` | L1–L3/TITLE instruction + output field descs |
| `memory/lib/affect.py` | DISTILL fields + "swearing doesn't drop rules" |
| `docs/next-steps-sequence.md` | Phase 3 distill acceptance includes this eval list |
| Tests / fixtures | Golden chunks from §7.7 must-survive phrases |

## 11. One-line summary

**These sessions prove the memory pipeline's job is not "summarize the coding." It is "keep the user's laws, flips, undelivered debts, and disagreement exits — which almost always arrive wrapped in swearing — and put those back into the next session."** Everything else is supporting evidence.
