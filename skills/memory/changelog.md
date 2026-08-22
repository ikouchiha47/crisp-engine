# /memory changelog — Record a changelog event

Capture what changed, why it matters, and the outcome at significant moments.

## Triggers

| Trigger | When to use |
|---|---|
| `milestone` | A planned deliverable is complete |
| `finally-works` | Something that was broken/hard is now working |
| `major-change` | Significant architectural or behavioral shift |
| `pre-compact` | Context window near 50% — capture current state before compaction |
| `session-end` | End of session summary |
| `checkpoint` | Manual snapshot at any point |

## Steps

### 1. Get git context

```bash
git diff HEAD --stat
```

### 2. Compose the record

- **What changed**: bullet list of concrete changes (files, functions, behavior)
- **Why**: motivation or problem being solved
- **Outcome**: what works now that didn't before, or what's unblocked

### 3. Save

```bash
crisp changelog --trigger <trigger> --outcome "<outcome>" --note "<full note>"
```

Where `--note` contains the what/why/outcome in one paragraph.

### 4. Report

Show the episode ID and a one-line confirmation.
