# /memory instinct — continuous-learning behaviors

An **instinct** is a reinforced behavioral memory distilled from tool-use: an episode
with `category="instinct"` and a `confidence` score in [0, 1]. They form automatically
as patterns recur, and can graduate into real skills/commands/agents.

## How it works

1. **Observe** — `PreToolUse`/`PostToolUse` hooks log each tool call cheaply (append-only
   JSONL under `~/.claude/memory/observations/`). No LLM, no blocking.
2. **Distill** — on `Stop`/`SessionEnd` (or `crisp instinct analyze --force`), once the
   buffer reaches `min_observations` (default 20), recurring signatures (e.g. `Bash:git`,
   `Edit:.go`) become instincts at confidence `base_confidence` (0.3).
3. **Reinforce** — seeing the pattern again raises confidence (`+reinforce_step`), bumps
   access, and resets decay. Absence lets the normal L2 half-life erode it.
4. **Evolve** — `crisp instinct evolve` clusters instincts ≥ `evolve_threshold` (0.8) and
   emits a `SKILL.md`/command/agent under `~/.claude/memory/evolved/`.
5. **Promote** — `crisp instinct promote <id>` graduates a project instinct to global scope
   once its signature is seen in ≥ `promote_min_projects` (2) distinct projects.

Instincts are stored at **layer L2** (30-day half-life) so a half-learned habit doesn't
evaporate overnight; promoted/global ones are marked permanent.

## Subcommands

| Command | Purpose |
|---|---|
| `crisp instinct list [--scope S] [--min-confidence X]` | Show instincts ranked by confidence |
| `crisp instinct analyze [--force]` | Distill the observation buffer now |
| `crisp instinct show <id>` | Inspect one instinct (confidence, projects, context) |
| `crisp instinct reinforce <id>` / `weaken <id>` | Manually nudge confidence |
| `crisp instinct forget <id>` | Delete an instinct |
| `crisp instinct evolve [ids…] [--kind skill\|command\|agent] [--name N]` | Emit an artifact |
| `crisp instinct promote <id>` | Project → global |

## When to surface this to the user

- They ask "what have you learned" / "what are my habits here" → `crisp instinct list`.
- A behavior is clearly stable and reusable → suggest `crisp instinct evolve`.
- Don't run `evolve`/`promote`/`forget` without telling the user what it will produce.

## Config (in `~/.claude/memory/config/config.json` under `instincts`)

`enabled, min_observations, min_pattern_count, base_confidence, reinforce_step,
decay_step, evolve_threshold, promote_min_projects`.
