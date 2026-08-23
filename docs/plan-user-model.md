# Plan: Cross-Session User Model

**Status:** Ready to build  
**Date:** 2026-08-22

---

## What it is

A single L3 episode (`id=user_model`, `category=user_model`, `is_permanent=True`)
that lives in the store and gets **replaced in place** each time it updates.
It is a synthesized developer profile -- not a log, not an episode list.
Claude reads it at SessionStart and adjusts behavior accordingly without the user
having to say anything.

Four sections:
- **Preferences** -- what this developer consistently reaches for
- **Anti-patterns** -- things they explicitly rejected or corrected
- **Recurring struggles** -- what causes friction repeatedly
- **Working style** -- session rhythm, tooling habits, emotional signals

---

## Input signals

### Existing (already captured)

| Signal | Source | Field |
|---|---|---|
| Explicit corrections | `category=correction`, `is_permanent=True` | `correction_delta`, `lesson` |
| Frustration moments | `category=frustration` | `frustration_score`, `content` |
| Tool failures | `category=failure` | `content`, `tags` |
| Behavioral patterns | `crisp instinct list` | confidence, count, tool+prefix |
| Session summaries | L1/L2 episodes | `content` |

### New signals to add

#### 1. Profanity / high-affect language detection

**Signal:** existence of profanity in a user message, not the words themselves.
The presence matters. It means something broke the user's composure.
The specific words do not need to be stored -- only the flag.

Detected in `_read_transcript()` during PreCompact / SessionEnd.
Stored as a field on the conversation episode:

```python
context_snapshot={
    "turns": turn_count,
    "profanity_detected": True,   # bool -- existence only
    "profanity_turn_count": 3,    # how many turns contained it
}
```

Detection: a simple word list check on user turns only (not assistant).
The list should be broad -- profanity in any language counts.
What matters is the count and distribution across the session, not which words.

Implementation: `lib/affect.py` -- `detect_affect(turns: list[dict]) -> AffectSignal`

```python
@dataclass
class AffectSignal:
    profanity_detected: bool
    profanity_turn_count: int      # turns with profanity
    profanity_density: float       # profanity_turns / total_turns
    abrupt_end: bool               # session ended without natural completion
    disagreement_end: bool         # last user message before stop was a correction/pushback
    forced_termination: bool       # session ended via ctrl+c / PreCompact without Stop
    affect_score: float            # 0.0-1.0 composite
```

`affect_score` formula:
```
profanity_density * 0.4
+ (0.3 if abrupt_end else 0)
+ (0.2 if disagreement_end else 0)
+ (0.1 if forced_termination else 0)
```

#### 2. Session termination classification

Three termination types, detected at the boundary event:

| Type | How detected | What it means |
|---|---|---|
| `natural` | Stop fires with turn_count >= 3, no disagreement signals | Session completed normally |
| `abrupt` | PreCompact fires but no Stop preceded it; or Stop fires with turn_count < 3 | User interrupted or gave up |
| `frustrated_exit` | Stop fires AND (profanity detected OR last user message matches disagreement patterns OR frustration_score > 0.6) | Session ended in friction |
| `disagreement_exit` | Stop fires AND last user message is a correction or pushback without resolution | User left mid-disagreement |

Stored as a `category=session_signal` L0 episode on every Stop/PreCompact:

```python
MemoryEpisode(
    id=f"session_signal_{ts}",
    session_id=session_id,
    category="session_signal",
    layer=0,
    title=f"Session end: {termination_type}",
    content=f"Session ended with type={termination_type}. ...",
    importance=0.9 if termination_type in ("frustrated_exit", "disagreement_exit") else 0.3,
    frustration_score=affect.affect_score,
    tags=["session_signal", termination_type],
    context_snapshot={
        "termination_type": termination_type,
        "profanity_detected": affect.profanity_detected,
        "profanity_density": affect.profanity_density,
        "turn_count": turn_count,
    },
)
```

Low-importance natural exits do not produce an episode -- only `frustrated_exit`
and `disagreement_exit` always do. `abrupt` produces one only if turn_count > 5
(so failed session starts don't generate noise).

---

## Architecture

```
affect.py
  detect_affect(turns) -> AffectSignal

hooks.py
  _read_transcript()          -- already reads turns; extend to call detect_affect()
  handle_claude_transcript()  -- save session_signal episode using AffectSignal
  handle_stop()               -- classify termination type, save session_signal

lib/user_model.py
  UserModelBuilder
    .collect(store, since_ts)
      -- corrections (L3, is_permanent)
      -- frustrations (L0, category=frustration)
      -- session_signals (L0, category=session_signal, termination_type != natural)
      -- top instincts via crisp instinct list
      -- L1/L2 session summaries
    .synthesize(signals, existing_model) -> str
      -- LLM call: diff old vs new signals, update profile
    .save(store, profile_text)
      -- upsert L3 episode id=user_model, is_permanent=True
    .load(store) -> Optional[MemoryEpisode]

hooks.py (auto-trigger)
  handle_claude_transcript()  -- after cascade, check update threshold, call builder

cli.py
  crisp user-model show
  crisp user-model update [--force]
  crisp user-model diff      -- show signals used in last synthesis

skills/memory/user-model.md  -- skill: how Claude should read and use the model
SKILL.md                     -- add user-model subcommand + auto-invoke triggers
```

---

## Update threshold

The model updates when ALL of the following are true:

1. At least one of these since last update:
   - 3+ new correction episodes
   - 2+ frustrated_exit or disagreement_exit sessions
   - 5+ new frustration episodes with `frustration_score > 0.5`

2. Model is either absent or older than 3 days

The threshold is intentionally high for the first two criteria -- a single bad
session should not rewrite the model. Patterns need to repeat.

The threshold is stored in `context_snapshot.signal_counts_at_update` so the
trigger logic can diff cleanly:

```json
{
  "last_updated": "2026-08-22T...",
  "signal_counts_at_update": {
    "corrections": 4,
    "frustrated_exits": 2,
    "frustration_episodes": 7,
    "instincts_above_threshold": 5
  }
}
```

---

## LLM synthesis prompt

```
You are updating a persistent developer profile based on new behavioral signals.

This profile is read by an AI assistant at the start of every session to calibrate
its behavior to this specific developer. It must be accurate, evidence-grounded,
and free of flattery or generic advice.

Existing profile:
{existing_model or "(none -- first build)"}

New signals since last update:

Corrections ({n} new):
{correction_summaries}

Session terminations ({n} new):
{session_signal_summaries}
-- Note: "frustrated_exit" means the session ended with profanity or high friction.
-- "disagreement_exit" means the last user message was a correction or pushback
   without resolution. These are the most diagnostic signals.

Frustration moments ({n} new):
{frustration_summaries}

Top instincts (behavioral patterns, confidence >= 0.6):
{instinct_list}

Rules:
- Only update sections where new signals provide evidence. Do not change what
  you do not have evidence to change.
- Do not remove existing entries unless a new signal directly contradicts them.
- Each entry must cite the signal type in parentheses: (correction), (frustration),
  (instinct), (frustrated_exit), (disagreement_exit).
- "Recurring struggles" must list patterns that appeared in 2+ sessions, not
  one-off events.
- If profanity_detected=True appeared in 2+ sessions, note in Working Style that
  this developer signals frustration vocally -- calibrate your approach before
  friction escalates.
- Do not store or quote profanity. Note only that it occurred and what it signals.
- Keep the entire profile under 800 words.
- Use plain markdown. Four sections only:
  Preferences / Anti-patterns / Recurring struggles / Working style

Output the updated profile text only. No preamble.
```

---

## What Claude does with it

At SessionStart, if `user_model` exists the hook logs it at DEBUG. The episode
content appears in the session context. Claude reads it like CLAUDE.md -- it
shapes behavior without the user asking.

Practical effects:
- If anti-patterns list "over-explains obvious decisions": Claude skips rationale
  and just acts.
- If struggles list "auth middleware keeps breaking": Claude checks auth-related
  files proactively when working near that code.
- If working style notes vocal frustration: Claude shortens responses and stops
  asking clarifying questions when affect signals rise.
- If disagreement_exit occurred twice on the same topic: Claude flags the topic
  and asks before proceeding rather than assuming.

---

## Task list

### Phase 1: Affect detection

- [ ] `lib/affect.py` -- `AffectSignal` dataclass + `detect_affect(turns)`
  - word list: broad, covers common profanity in English (and any other languages
    seen in the project's conversation episodes). Store list in `lib/langdata/`.
  - detection: scan user turns only, count turns with any match
  - abrupt_end: True if PreCompact fires (not Stop), or Stop fires with turn_count < 3
  - disagreement_end: last user turn before stop matches correction/pushback patterns
    (reuse `_detect_correction` logic)
  - forced_termination: set by caller (PreCompact path sets it True, Stop path False)

- [ ] `hooks.py` -- extend `_read_transcript()` to return affect signal
  - return `(text, turn_count, turns_raw, affect)` where `turns_raw` is the list
    of `{role, content}` dicts for affect analysis

- [ ] `hooks.py` -- `_session_signal_episode()` -- build and save session_signal episode
  - called from `handle_claude_transcript()` (PreCompact/SessionEnd) and `handle_stop()`
  - only saves if termination_type != "natural" OR affect_score > 0.4

### Phase 2: UserModelBuilder

- [ ] `lib/user_model.py`
  - `collect(store, since_ts)` -- query all signal types
  - `synthesize(signals, existing)` -- call embedding provider's LLM endpoint
    (or fall back to Claude via subprocess if no LLM provider configured)
  - `save(store, text, signal_counts)` -- upsert via `store.save_episode()` with
    stable `id=user_model`; if episode exists with that id, delete then re-save
  - `load(store)` -- `store.get_episode("user_model")`
  - `should_update(store) -> bool` -- check threshold against stored signal_counts

- [ ] `lib/store/memory_store.py` -- use `get_episode(id)` (already exists; no new method needed)

### Phase 3: Auto-trigger + CLI

- [ ] `hooks.py` -- after cascade in `handle_claude_transcript()`:
  call `UserModelBuilder(store).should_update()` and if True, run update silently

- [ ] `cli.py` -- `crisp user-model show|update|diff`

- [ ] `cli.py` -- `crisp stats` -- include user_model last_updated date

### Phase 4: Session consumption

- [ ] `hooks.py` -- `handle_claude_session_start()`: if user_model episode exists,
  include its content in the session start response so Claude sees it in context

### Phase 5: Skills

- [ ] `skills/memory/user-model.md` -- how to read the model, when to cite it,
  how to act on frustrated_exit / disagreement_exit history
- [ ] `SKILL.md` -- add subcommand, auto-invoke triggers

---

## Files changed

| File | Change |
|---|---|
| `lib/affect.py` | new |
| `lib/user_model.py` | new |
| `lib/langdata/profanity_en.txt` | new (word list, existence detection only) |
| `lib/hooks.py` | extend `_read_transcript`, add `_session_signal_episode`, auto-trigger in `handle_claude_transcript` |
| `lib/store/memory_store.py` | use existing `get_episode(id)` — no changes needed |
| `lib/cli.py` | add `user-model` subcommand |
| `skills/memory/user-model.md` | new |
| `skills/memory/SKILL.md` | add subcommand + trigger |
| `CONTRIBUTING.md` | section on user model signals |
