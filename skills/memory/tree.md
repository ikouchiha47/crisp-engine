# /memory tree — Project tree with index status

Show the project's file structure alongside what is and isn't indexed in memory.

## Steps

### 1. Run tree

```bash
crisp tree [path]
```

If no path given, use the current working directory (`.`).

Output shows tracked files (via `git ls-files`) up to 3 levels deep, with markers:
- `✓` — file has a current index entry (not stale)
- `~` — file has an index entry but it is stale (file was modified after indexing)
- no marker — file has never been indexed

### 2. Interpret the output

- Files with no marker are candidates for `/memory index <path>`
- Files marked `~` have changed since their last index — re-index them before relying on cached summaries
- Use the tree to decide which directories need a `dir` or `module` level summary

### 3. Report

Summarize: how many files are indexed, how many are stale, which areas of the codebase have the least coverage.
