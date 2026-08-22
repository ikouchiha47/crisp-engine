# /memory index — Index a file into memory

Index a file at symbol + file level. Optionally create a dir-level summary if the parent isn't indexed yet.

## Steps

### 1. Get structure

```bash
crisp index --json <path>
```

Output is JSON: `summary`, `symbols`, `hierarchy`, `metadata`. Use it as raw material — do not copy it verbatim.

### 2. Write a file-level summary

Using the symbols, hierarchy, and any file content already in context, write a concise semantic summary:

- One sentence: what is this file's role in the codebase?
- Classes: name + what they encapsulate (one phrase each)
- Key functions: name + what they do (one phrase each)
- Notable constants or config values

Keep it under 150 words. This is what future sessions read instead of opening the file.

Valid `--level` values and what they represent:

| Level | Scope | Crisp layer | When to use |
|---|---|---|---|
| `symbol` | Single function/class | L0 | Fine-grained: what does this specific symbol do |
| `file` | One source file | L0 | What does this file do, its exports and key logic |
| `dir` | A directory | L1 | What does this package/folder contain as a whole |
| `module` | A cross-dir module | L1 | A named subsystem spanning multiple dirs (e.g. `ble`, `gestures`) |
| `feature` | A product feature | L2 | End-to-end capability (e.g. "calibration flow", "device discovery") |
| `project` | The whole repo | L2 | Top-level architecture, major components, purpose |

```bash
crisp save-index --path <path> --level file --content "<your summary>"
```

### 3. Dir-level summary (if not yet indexed)

```bash
crisp search-path <parent_dir> --limit 1
```

If no results → write a dir-level summary:
- One sentence: what does this directory contain as a whole?
- Key files and their roles (one phrase each)

```bash
crisp save-index --path <parent_dir> --level dir --content "<your dir summary>"
```

### 4. Consider higher levels

After indexing a file, check whether a module or feature summary should be updated:
- If most files in a directory are now indexed, write or refresh the `module` summary for that directory.
- If a feature spans multiple directories and its behavior changed, write a `feature`-level summary.
- Use `crisp search-path <path> --limit 10` to see what's already indexed at higher levels before writing a new one.

### 5. Report

Show: path indexed, level(s) saved, episode IDs returned by CLI.
