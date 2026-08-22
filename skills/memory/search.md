# /memory search-path — Check if a file or directory is already indexed

Before reading a large file, check whether a summary already exists in memory.

## Steps

### 1. Check for existing index

```bash
crisp search-path <path> --limit 5
```

### 2. Interpret results

- **Results found**: read the summaries. If they are fresh (not marked stale), use them as context instead of opening the full file. Report what was found.
- **No results**: proceed to read the file normally. Consider running `/memory index <path>` after reading it.
- **Stale results**: the episode has the tag `stale` — meaning the file was written or edited after the index was saved, so the summary no longer matches the current content. Read the file and re-index it with `/memory index <path>`. A result is stale if any episode for that path contains `"stale"` in its tags list.

### 3. Report

Tell the user whether an existing summary was found and whether it was used.

---

## Search-tool discipline (when grepping the store or repo yourself)

The structured paths above (`crisp search`, `crisp search-path`) are preferred. When you
must scan raw text yourself, use the right tool in this order — and **never assume a
flag exists**:

**Tool hierarchy:** `rg` (ripgrep) → `sed`/`awk` → `grep` (last resort).
- `rg` is fastest, respects `.gitignore`, and has consistent flags across platforms — prefer it.
- Reach for `sed`/`awk` for line-ranged or field extraction where `rg` doesn't fit.
- Fall back to `grep` only when neither is available.

**Probe before you rely on flags.** macOS ships **BSD** userland; Linux ships **GNU** —
their `grep`/`sed`/`awk` flags differ (e.g. `grep -P`, `sed -i` syntax). Before using a
non-trivial flag on *any* CLI tool:
1. Check the platform: `uname` (Darwin = BSD/macOS, Linux = GNU).
2. Confirm the flag exists: `<tool> --help` or `<tool> --version` (e.g. `grep --help | rg -- -P`).
3. If unsure, prefer `rg`, which sidesteps the BSD/GNU split entirely.

Treat `--help` as the source of truth for the tool actually installed, not memory.
