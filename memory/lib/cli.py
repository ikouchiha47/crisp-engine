#!/usr/bin/env python3
"""CLI tool for memory inspection and management.

Usage:
    crisp save <content>              Save explicit memory
    crisp search <query>              Search memory
    crisp show <id>                   Show episode details
    crisp forget <id>                 Delete episode
    crisp stats                       Show statistics
    crisp reflect                     Run consolidation
    crisp prune                       Run pruning
    crisp export                      Export to JSON
    crisp list [--layer=L]            List episodes
    crisp index <file> [--json]       Structural index of a file
    crisp save-index                  Save semantic index entry (file/dir/module/feature/project)
    crisp search-path <path>          Find existing index entries for a file/dir
    crisp status <path>               Show indexed/stale/pending for files under path
    crisp checkpoint                  Save session midpoint snapshot with git diff
    crisp changelog                   Save a changelog entry
    crisp projects                    List all projects
    crisp switch <project_id>         Switch to project's memory
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.store import (
    MemoryEpisode,
    MemoryStore,
    ProjectMemoryManager,
    get_memory_store,
    is_code_index_category,
)
from lib.consolidate import MemoryReflector, PruningService
from lib.retrieve import RetrievalOrchestrator
from lib import config as _cfg
from lib.log import get_logger as _get_logger, log_path as _log_path

_log = _get_logger("cli")


def get_store(project_root: str = None):
    """Get memory store, auto-detecting project from cwd if not specified."""
    if project_root:
        manager = ProjectMemoryManager()
        return manager.get_store_for_project(project_root)
    else:
        # Auto-detect from current directory
        return get_memory_store()


def cmd_save(args):
    """Save explicit memory."""
    store = get_store()

    episode_id = f"manual_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    episode = MemoryEpisode(
        id=episode_id,
        session_id="cli_session",
        timestamp=datetime.now(timezone.utc).isoformat(),
        layer=0,
        title=args.title or "Manual memory",
        content=args.content,
        category=args.category or "manual",
        importance=args.importance or 0.7,
        tags=args.tags or [],
        trigger_type="manual",
        is_permanent=args.permanent or False,
    )

    if store.save_episode(episode):
        print(f"✓ Saved episode: {episode.id}")
    else:
        print(f"⚠ Duplicate detected, not saved")


def cmd_search(args):
    """Search memory (default: keyword/structured; --semantic: embeddings)."""
    store = get_store()
    emb_cfg = getattr(args, "_embedding_config", None)
    if getattr(args, "semantic", False):
        return _semantic_search(store, args.query, args.limit, emb_cfg)
    from lib.retrieve import RetrievalOrchestrator

    orchestrator = RetrievalOrchestrator(store, emb_cfg)
    results = orchestrator.search(args.query, limit=args.limit)

    if not results:
        print("No results found.")
        return

    print(f"\nFound {len(results)} results:\n")
    for episode, score in results:
        print(f"--- [{episode.layer}] Score: {score:.2f} ---")
        print(f"ID: {episode.id}")
        print(f"Title: {episode.title}")
        print(f"Category: {episode.category}")
        print(f"Tags: {', '.join(episode.tags) if episode.tags else 'none'}")
        print(f"Importance: {episode.importance:.2f}")
        print(f"Decay: {episode.decay_score:.2f}")
        if episode.correction_applied:
            print(f"⚠️ CORRECTION: {episode.correction_delta}")
        if episode.lesson:
            print(f"Lesson: {episode.lesson}")
        print(f"\n{episode.content[:300]}{'...' if len(episode.content) > 300 else ''}")
        print()


def cmd_show(args):
    """Show episode details."""
    store = get_store()
    episode = store.get_episode(args.id)

    if not episode:
        print(f"Episode not found: {args.id}")
        return

    print(f"\n{'='*60}")
    print(f"Episode: {episode.id}")
    print(f"{'='*60}")
    print(f"Layer: {episode.layer}")
    print(f"Session: {episode.session_id}")
    print(f"Timestamp: {episode.timestamp}")
    print(f"Title: {episode.title}")
    print(f"Category: {episode.category}")
    print(f"Tags: {', '.join(episode.tags) if episode.tags else 'none'}")
    print(f"Importance: {episode.importance:.2f}")
    print(f"Frustration: {episode.frustration_score:.2f}")
    print(f"Correction: {episode.correction_applied}")
    print(f"Permanent: {episode.is_permanent}")
    print(f"Access count: {episode.access_count}")
    print(f"Decay score: {episode.decay_score:.2f}")

    if episode.source_type:
        print(f"Source: {episode.source_type}")
    if episode.source_path:
        print(f"Source path: {episode.source_path}")
    if episode.source_hash:
        print(f"Source hash: {episode.source_hash}")
    if episode.root_cause:
        print(f"Root cause: {episode.root_cause}")
    if episode.impact:
        print(f"Impact: {episode.impact}")
    if episode.lesson:
        print(f"Lesson: {episode.lesson}")

    # Links
    links = store.get_links(episode.id)
    if links:
        print(f"\nLinked episodes:")
        for link in links:
            print(f"  → {link[0]} ({link[1]}, strength: {link[2]:.2f})")

    if episode.parent_id:
        print(f"\nParent: {episode.parent_id}")
    if episode.linked_ids:
        print(f"Linked IDs: {', '.join(episode.linked_ids)}")

    print(f"\n{'='*60}")
    print("Content:")
    print(f"{'='*60}")
    print(episode.content)
    print(f"{'='*60}\n")


def cmd_forget(args):
    """Delete episode. Requires --force for permanent episodes."""
    store = get_store()
    ep = store.read_episode(args.id)
    if ep and ep.is_permanent and not args.force:
        print(f"⛔ Episode {args.id} is permanent. Use --force to delete it.")
        return
    if store.delete_episode(args.id):
        print(f"✓ Deleted episode: {args.id}")
    else:
        print(f"⚠ Episode not found: {args.id}")


def cmd_stats(args):
    """Show statistics."""
    store = get_store()
    stats = store.get_stats()

    print(f"\nMemory Store Statistics")
    print(f"{'='*40}")
    print(f"Total episodes: {stats['total']}")
    print(f"\nBy layer:")
    for layer, count in stats["layers"].items():
        print(f"  {layer}: {count}")
    print(f"\nHash cache size: {stats.get('hash_cache_size', 0)}")
    print(f"File states: {stats.get('file_states_size', 0)}")
    print(f"Links: {stats.get('links', 0)}")

    # Recent episodes
    print(f"\nRecent L0 episodes:")
    recent = store.list_episodes(layer=0)[:5]
    for ep in recent:
        print(f"  {ep.id[:16]}... | {ep.title[:30]}... | {ep.category}")
    print()


def cmd_list(args):
    """List episodes."""
    store = get_store()
    layer = int(args.layer) if args.layer else None
    episodes = store.list_episodes(layer=layer)

    print(f"\nEpisodes ({len(episodes)} total):\n")
    for ep in episodes:
        marker = "⭐" if ep.is_permanent else "⚠" if ep.correction_applied else ""
        print(
            f"{marker} [{ep.layer}] {ep.id[:16]}... | {ep.title[:40]} | {ep.category} | decay: {ep.decay_score:.2f}"
        )
    print()


def cmd_reflect(args):
    """Run consolidation."""
    store = get_store()
    reflector = MemoryReflector(store)

    print("Running consolidation...")
    result = reflector.consolidate(max_l0_per_batch=args.batch_size)

    print(f"\nConsolidation complete:")
    print(f"  L1 created: {result['l1_created']}")
    print(f"  L2 created: {result['l2_created']}")
    print(f"  L3 created: {result['l3_created']}")


def cmd_prune(args):
    """Run pruning."""
    store = get_store()
    pruner = PruningService(store)

    print("Running pruning...")
    result = pruner.prune()

    print(f"\nPruning complete:")
    print(f"  Decay scores updated: {result['updated_decay']}")
    print(f"  Conflicts resolved: {result['conflicts_resolved']}")
    print(f"  Episodes archived: {result['archived']}")
    print(f"  Ancient deleted: {result['deleted']}")


def cmd_export(args):
    """Export memory to JSON."""
    store = get_store()

    all_episodes = []
    for layer in range(4):
        episodes = store.list_episodes(layer=layer)
        for ep in episodes:
            all_episodes.append(
                {
                    "id": ep.id,
                    "layer": ep.layer,
                    "timestamp": ep.timestamp,
                    "title": ep.title,
                    "category": ep.category,
                    "tags": ep.tags,
                    "importance": ep.importance,
                    "content": ep.content,
                    "decay_score": ep.decay_score,
                }
            )

    output = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "total": len(all_episodes),
        "episodes": all_episodes,
    }

    path = (
        Path(args.output)
        if args.output
        else Path.home()
        / ".claude"
        / "memory"
        / "exports"
        / f"export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"✓ Exported {len(all_episodes)} episodes to {path}")


def cmd_index(args):
    """Structural index of a file — extracts symbols via tree-sitter/regex.

    With --json outputs raw IndexResult for the skill to feed to Claude.
    Without --json saves symbol episodes to the store.
    """
    from lib.indexers import IndexerRegistry

    file_path = Path(args.file_path).resolve()
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    registry = IndexerRegistry()
    indexer = registry.get_indexer(file_path)

    if indexer is None:
        print(f"No indexer available for: {file_path.name}")
        print(f"Supported types: {', '.join(registry.list_supported_types())}")
        sys.exit(1)

    try:
        result = indexer.index(file_path)
    except Exception as e:
        print(f"Indexing failed: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        # Extract imports via tree-sitter
        try:
            from lib.code_index.treesitter_strategy import parse_imports
            raw_imports = parse_imports(file_path)
        except Exception:
            raw_imports = []

        # Resolve and deduplicate imports (same path may appear multiple times)
        seen: dict[str, dict] = {}
        for imp in raw_imports:
            path = imp["path"]
            if path not in seen:
                entry = dict(imp)
                if imp.get("local") and path:
                    base = file_path.parent
                    for ext in ("", ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".cpp", ".h"):
                        candidate = (base / (path + ext)).resolve()
                        if candidate.exists():
                            entry["resolved"] = str(candidate)
                            break
                seen[path] = entry
            else:
                # merge names from duplicate import lines
                seen[path]["names"] = list(set(seen[path].get("names", []) + imp.get("names", [])))
        imports = list(seen.values())

        # Language detection is separate from symbol extraction (the indexer
        # already extracted symbols above regardless) — this is purely the
        # signal for whether a caller should improve indexing quality later.
        # When it can't be determined at all, the message travels WITH the
        # tool result so any agent consuming this output (Claude, OpenCode,
        # whatever) has the concrete next step right where it noticed the
        # gap, instead of needing a separately-maintained skill instruction
        # telling it to check proactively.
        from lib.lang_detect import detect_language
        store = get_store()
        detected_language = detect_language(
            str(file_path), cache_dir=store.cache_path
        )

        out = {
            "file": str(file_path),
            "media_type": result.media_type,
            "summary": result.summary,
            "symbols": result.symbols,
            "hierarchy": result.hierarchy,
            "metadata": result.metadata,
            "imports": imports,
            "detected_language": detected_language,
        }
        if detected_language is None:
            out["message"] = (
                f"Could not determine the language of {file_path.name} from its "
                "extension or content heuristics. If you can tell what it is, run: "
                f"crisp classify-language {file_path} <language>"
            )
        print(json.dumps(out, indent=2))
        return

    print(f"Indexer:  {type(indexer).__name__}")
    print(f"File:     {file_path}")
    print(f"\nSummary:\n{result.summary}\n")
    print(f"Symbols:  {len(result.symbols)}")

    if not args.dry_run:
        store = get_store()
        session_id = f"index_cli_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        episodes = indexer.extract_episodes(result)
        saved = 0
        for ep_data in episodes:
            ep = MemoryEpisode(session_id=session_id, **ep_data)
            if store.save_episode(ep):
                saved += 1
        print(f"Saved:    {saved}/{len(episodes)} episodes")
        if args.verbose:
            for ep_data in episodes:
                print(f"  [{ep_data.get('importance', 0):.1f}] {ep_data.get('title', '')}")
    else:
        print("(dry run — nothing saved)")
        if args.verbose:
            for ep_data in indexer.extract_episodes(result):
                print(f"  [{ep_data.get('importance', 0):.1f}] {ep_data.get('title', '')}")


def cmd_classify_language(args):
    """Record a language classification an agent (or human) figured out
    for a file Crisp Engine's own detection (languages.yml + heuristics.yml)
    could not resolve. Writes into the per-project cache so every future lookup
    of this file (--scope path, default) or every file sharing its
    extension (--scope ext) resolves statically from then on.
    """
    from lib.lang_detect import classify_language

    file_path = Path(args.file_path).resolve()
    store = get_store()
    classify_language(store.cache_path, str(file_path), args.language, scope=args.scope)
    print(f"Classified {file_path.name} as {args.language} (scope={args.scope})")


# Index level → (crisp layer, importance)
_INDEX_LEVELS = {
    "symbol":  (0, 0.5),
    "file":    (0, 0.7),
    "dir":     (1, 0.7),
    "module":  (1, 0.8),
    "feature": (2, 0.8),
    "project": (2, 0.9),
}


def cmd_save_index(args):
    """Save a semantic index entry generated by Claude.

    Levels: symbol | file | dir | module | feature | project
    Higher levels map to higher crisp layers (file→L0, dir/module→L1, feature/project→L2).
    """
    level = args.level
    if level not in _INDEX_LEVELS:
        print(f"Unknown level '{level}'. Choose from: {', '.join(_INDEX_LEVELS)}", file=sys.stderr)
        sys.exit(1)

    layer, importance = _INDEX_LEVELS[level]
    source_path = str(Path(args.path).resolve()) if args.path else ""
    content = args.content

    store = get_store()
    session_id = f"save_index_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    name = Path(source_path).name if source_path else "project"
    episode_id = f"idx_{level}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{name}"

    # If updating an existing entry for this path+level, delete the stale one first
    # Never delete permanent episodes — they require explicit `crisp forget --force`
    if source_path:
        existing = _find_index_episodes(store, source_path, level)
        for ep in existing:
            if ep.is_permanent:
                print(f"⚠ Skipping permanent episode {ep.id} — use `crisp forget {ep.id} --force` to remove it first")
                return
            store.delete_episode(ep.id)

    ep = MemoryEpisode(
        id=episode_id,
        session_id=session_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        layer=layer,
        title=f"[{level}] {name}",
        content=content,
        source_type="code_index",
        source_path=source_path,
        category=f"code_index_{level}",
        importance=importance,
        tags=["code_index", level] + (args.tags or []),
        is_permanent=args.permanent,
    )
    if store.save_episode(ep):
        print(f"saved  {ep.id}")
    else:
        print(f"duplicate — skipped")


def cmd_search_path(args):
    """Find existing index entries for a file or directory path."""
    store = get_store()
    target = str(Path(args.path).resolve())
    level_filter = args.level  # optional

    episodes = store.list_episodes()
    matches = [
        ep for ep in episodes
        if ep.source_path and (ep.source_path == target or ep.source_path.startswith(target))
        and is_code_index_category(ep.category)
        and (not level_filter or f"code_index_{level_filter}" == ep.category)
    ]

    if not matches:
        print(f"No index entries for: {target}")
        return

    for ep in matches:
        stale = "stale" in (ep.tags or [])
        marker = " [STALE]" if stale else ""
        print(f"\n{'='*60}")
        print(f"{ep.category}{marker}  {ep.id}")
        print(f"Path: {ep.source_path}")
        print(f"{'='*60}")
        print(ep.content)


def cmd_status(args):
    """Show indexed/stale/pending status for files under a path."""
    import os
    from lib.indexers.indexer_common import is_source_file

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"Path not found: {root}", file=sys.stderr)
        sys.exit(1)

    store = get_store()
    all_episodes = store.list_episodes()

    # Build lookup: source_path → episode list
    indexed: dict = {}
    for ep in all_episodes:
        if ep.source_path and is_code_index_category(ep.category):
            indexed.setdefault(ep.source_path, []).append(ep)

    # Walk files under root (max depth from args)
    max_depth = args.depth or 4
    counts = {"indexed": 0, "stale": 0, "pending": 0}

    def _status(ep_list):
        if not ep_list:
            return "pending"
        if any("stale" in (e.tags or []) for e in ep_list):
            return "stale"
        return "indexed"

    print(f"\nIndex status: {root}\n")
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth > max_depth:
            dirnames.clear()
            continue
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            if not is_source_file(fpath) and fpath.suffix not in {".md"}:
                continue
            rel = fpath.relative_to(root)
            st = _status(indexed.get(str(fpath), []))
            counts[st] += 1
            marker = {"indexed": "✓", "stale": "~", "pending": "·"}[st]
            print(f"  {marker} {rel}")

    print(f"\n  ✓ {counts['indexed']} indexed  ~ {counts['stale']} stale  · {counts['pending']} pending")


def cmd_checkpoint(args):
    """Save a session midpoint snapshot with git diff summary."""
    import subprocess

    store = get_store()
    session_id = args.session_id or f"checkpoint_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    # Git diff stat
    try:
        diff_stat = subprocess.run(
            ["git", "diff", "HEAD", "--stat"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "no changes"
    except Exception:
        diff_stat = "git unavailable"

    # Recent episodes this session
    all_eps = store.list_episodes(layer=0)
    recent = [ep for ep in all_eps if session_id in ep.session_id]

    content_lines = [
        f"SESSION CHECKPOINT — {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Git diff",
        "```",
        diff_stat,
        "```",
        "",
        f"## Episodes this session: {len(recent)}",
    ]
    for ep in recent[-10:]:
        content_lines.append(f"  - {ep.title}")

    ep = MemoryEpisode(
        id=f"checkpoint_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        session_id=session_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        layer=0,
        title=f"Session checkpoint",
        content="\n".join(content_lines),
        category="checkpoint",
        importance=0.8,
        tags=["checkpoint", "session"],
        trigger_type="scheduled",
    )
    if store.save_episode(ep):
        print(f"checkpoint saved: {ep.id}")
    else:
        print("checkpoint skipped (duplicate)")


def cmd_changelog(args):
    """Save a changelog entry — what changed, why, outcome."""
    import subprocess

    store = get_store()

    trigger = args.trigger or "manual"
    outcome = args.outcome or ""

    # Full diff or stat depending on flag
    try:
        diff_cmd = ["git", "diff", "HEAD", "--stat"] if args.stat_only else ["git", "diff", "HEAD"]
        diff_out = subprocess.run(
            diff_cmd, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        if not diff_out:
            diff_out = subprocess.run(
                ["git", "diff", "--stat"], capture_output=True, text=True, timeout=10,
            ).stdout.strip() or "no changes"
    except Exception:
        diff_out = "git unavailable"

    content_lines = [
        f"CHANGELOG — {datetime.now(timezone.utc).isoformat()}",
        f"TRIGGER: {trigger}",
    ]
    if outcome:
        content_lines += [f"OUTCOME: {outcome}", ""]
    content_lines += [
        "## Changes",
        "```diff" if not args.stat_only else "```",
        diff_out[:4000],
        "```",
    ]
    if args.note:
        content_lines += ["", f"## Note", args.note]

    ep = MemoryEpisode(
        id=f"changelog_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        session_id=f"changelog_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        layer=0,
        title=f"Changelog: {trigger}" + (f" — {outcome}" if outcome else ""),
        content="\n".join(content_lines),
        category="changelog",
        importance=0.85,
        tags=["changelog", trigger] + (["milestone"] if trigger == "milestone" else []),
        trigger_type=trigger,
        is_permanent=(trigger in ("milestone", "finally_works")),
    )
    if store.save_episode(ep):
        print(f"changelog saved: {ep.id}")
    else:
        print("changelog skipped (duplicate)")


def cmd_tree(args):
    """Print annotated project tree — like GNU tree but respecting .gitignore.

    Uses `git ls-files` so .gitignore is handled correctly at all levels.
    Caps at 3 directory levels. Skills use this to decide what to index.
    """
    import subprocess
    from lib.indexers.indexer_common import is_source_file

    root = Path(args.root or ".").resolve()
    if not root.exists():
        print(f"Path not found: {root}", file=sys.stderr)
        sys.exit(1)

    max_depth = min(args.depth or 3, 3)
    show_status = not args.no_status

    # Use git ls-files — tracked files only by default (clean signal).
    # Pass --untracked to also include untracked-but-not-ignored files.
    try:
        tracked = subprocess.run(
            ["git", "ls-files"],
            capture_output=True, text=True, cwd=str(root), timeout=10,
        ).stdout.splitlines()
        all_rel: set = set(tracked)
        if getattr(args, "untracked", False):
            untracked = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                capture_output=True, text=True, cwd=str(root), timeout=10,
            ).stdout.splitlines()
            all_rel.update(untracked)
    except Exception:
        # Not a git repo — fall back to simple walk excluding common noise
        SKIP = {"node_modules", ".git", "__pycache__", ".venv", "build",
                "dist", ".expo", "android", "ios", ".gradle", ".kotlin"}
        all_rel = set()
        for p in root.rglob("*"):
            if p.is_file() and not any(s in p.parts for s in SKIP):
                try:
                    all_rel.add(str(p.relative_to(root)))
                except ValueError:
                    pass

    tree: dict = {}  # rel_dir_str → {"dirs": set, "files": list}

    def node(d: str) -> dict:
        if d not in tree:
            tree[d] = {"dirs": set(), "files": []}
        return tree[d]

    for rel_str in sorted(all_rel):
        p = Path(rel_str)
        # Depth check
        if len(p.parts) > max_depth + 1:
            # Still register the top-level dirs so they appear
            top = str(Path(*p.parts[:max_depth]))
            parent = str(Path(*p.parts[:max_depth - 1])) if max_depth > 1 else ""
            node(parent)["dirs"].add(Path(*p.parts[:max_depth]).name)
            continue
        parent_dir = str(p.parent) if str(p.parent) != "." else ""
        node(parent_dir)["files"].append(p.name)
        # Register all parent dirs
        parts = p.parts[:-1]
        for i in range(len(parts)):
            d = str(Path(*parts[:i+1]))
            par = str(Path(*parts[:i])) if i > 0 else ""
            node(par)["dirs"].add(parts[i])

    # Index status lookup
    indexed_paths: dict = {}
    if show_status:
        store = get_store()
        for ep in store.list_episodes():
            if ep.source_path and is_code_index_category(ep.category):
                stale = "stale" in (ep.tags or [])
                cur = indexed_paths.get(ep.source_path, "pending")
                if cur != "stale":
                    indexed_paths[ep.source_path] = "stale" if stale else "indexed"

    def marker(abs_path: str) -> str:
        if not show_status:
            return ""
        st = indexed_paths.get(abs_path, "pending")
        return {"indexed": " ✓", "stale": " ~", "pending": ""}[st]

    def render(dir_key: str, prefix: str):
        n = tree.get(dir_key, {"dirs": set(), "files": []})
        subdirs = sorted(n["dirs"])
        files   = sorted(n["files"])
        items   = [(d, True) for d in subdirs] + [(f, False) for f in files]
        for i, (name, is_dir) in enumerate(items):
            connector  = "└── " if i == len(items) - 1 else "├── "
            ext_prefix = "    " if i == len(items) - 1 else "│   "
            rel_path   = str(Path(dir_key) / name) if dir_key else name
            abs_path   = str(root / rel_path)
            m = marker(abs_path)
            print(f"{prefix}{connector}{name}{'/' if is_dir else ''}{m}")
            if is_dir:
                render(rel_path, prefix + ext_prefix)

    print(f"{root}")
    render("", "")

    if show_status:
        n_idx   = sum(1 for v in indexed_paths.values() if v == "indexed")
        n_stale = sum(1 for v in indexed_paths.values() if v == "stale")
        print(f"\n  ✓ {n_idx} indexed  ~ {n_stale} stale")


def _find_index_episodes(store, source_path: str, level: str):
    """Find existing index episodes for a given path and level."""
    return [
        ep for ep in store.list_episodes()
        if ep.source_path == source_path
        and ep.category == f"code_index_{level}"
    ]


def cmd_projects(args):
    """List all projects with memory stores."""
    # Use global store to list projects
    manager = ProjectMemoryManager()
    projects = manager.list_projects()
    
    if not projects:
        print("No projects found.")
        return
    
    print(f"\nProjects ({len(projects)} total):\n")
    for p in projects:
        print(f"  📁 {p['name']}")
        print(f"     ID:       {p['project_id']}")
        print(f"     Root:     {p['root']}")
        print(f"     Episodes: {p['episode_count']}")
        if p.get('created_at'):
            print(f"     Created:  {p['created_at']}")
        print()


def cmd_switch(args):
    """Switch memory context to a project."""
    manager = ProjectMemoryManager()
    store = manager.get_store_for_project_id(args.project_id)
    
    if not store:
        print(f"Project not found: {args.project_id}")
        print("Use 'memory projects' to see available projects.")
        return
    
    stats = store.get_stats()
    print(f"\nSwitched to project: {args.project_id}")
    print(f"  Episodes: {stats['total']} total")
    print(f"  Layers:  L0={stats['layers'].get('l0',0)}, L1={stats['layers'].get('l1',0)}, "
          f"L2={stats['layers'].get('l2',0)}, L3={stats['layers'].get('l3',0)}")
    print()


def _semantic_search(store, query, limit, embedding_config: dict = None):
    """USER-TRIGGERED embedding search.

    embedding_config overrides store.config for provider/model/url/dim — CLI flags
    and env vars are already merged in before this is called.
    """
    from lib.embeddings import get_provider, cosine

    merged = {**store.config, **(embedding_config or {})}
    provider = get_provider(merged)
    try:
        qv = provider.embed(query)
    except Exception as e:
        print(f"Embedding unavailable ({e}); use plain `search` (keyword/rg).", file=sys.stderr)
        sys.exit(1)

    scored = []
    for ep in store.list_episodes():
        text = f"{ep.title}\n{ep.content}".strip()
        if not text:
            continue
        try:
            scored.append((ep, cosine(qv, provider.embed(text))))
        except Exception as e:
            print(f"Embedding failed ({e}); falling back to keyword search.", file=sys.stderr)
            for ep2, score in RetrievalOrchestrator(store, merged).search(query, limit=limit):
                print(f"[{ep2.layer}] {score:.2f}  {ep2.id}  {ep2.title}")
            return
    scored.sort(key=lambda t: t[1], reverse=True)

    pname = type(provider).__name__
    print(f"\nSemantic results via {pname} ({len(scored[:limit])}):\n")
    for ep, score in scored[:limit]:
        print(f"--- [{ep.layer}] cos {score:.3f} ---")
        print(f"ID: {ep.id}")
        print(f"Title: {ep.title}")
        print(f"{ep.content[:200]}{'...' if len(ep.content) > 200 else ''}\n")


def cmd_config(args):
    """Get or set persistent Crisp config."""
    import os
    action = getattr(args, "cfg_action", None)
    if action == "get" or action is None:
        merged = _cfg.load(cwd=os.getcwd())
        emb_keys = ["embedding_provider", "embedding_model", "embedding_api_url", "embedding_dim",
                    "store_backend", "db_path"]
        print(f"Global config : {_cfg.GLOBAL_CONFIG_PATH}")
        print(f"Project config: {os.getcwd()}/.crisp.json\n")
        print("Merged embedding/store config:")
        for k in emb_keys:
            print(f"  {k} = {merged.get(k, '(unset)')}")
        return
    if action == "set":
        updates = {}
        for pair in args.pairs:
            if "=" not in pair:
                print(f"Skipping malformed pair (expected KEY=VALUE): {pair!r}", file=sys.stderr)
                continue
            k, _, v = pair.partition("=")
            updates[k.strip()] = v.strip()
        if not updates:
            print("Nothing to set.", file=sys.stderr)
            return
        if args.global_scope:
            _cfg.write_global(updates)
            print(f"Written to {_cfg.GLOBAL_CONFIG_PATH}")
        else:
            import os
            _cfg.write_project(os.getcwd(), updates)
            print(f"Written to {os.getcwd()}/.crisp.json")
        for k, v in updates.items():
            print(f"  {k} = {v}")
        return
    print("Usage: crisp config [get | set KEY=VALUE ...]", file=sys.stderr)


def cmd_logs(args):
    """Show or tail the Crisp Engine log file."""
    lp = _log_path()
    if not lp.exists():
        print(f"Log file not yet created: {lp}")
        print("It will appear after the first hook fires or CLI command runs.")
        return
    if args.tail:
        import subprocess
        n = str(args.lines)
        try:
            subprocess.run(["tail", f"-{n}", "-f", str(lp)])
        except KeyboardInterrupt:
            pass
    elif args.last:
        lines = lp.read_text(encoding="utf-8").splitlines()
        for line in lines[-args.lines:]:
            print(line)
    else:
        print(lp)


def cmd_reindex_vecs(args):
    """Rebuild the sqlite-vec sidecar from all .md episodes in the store.

    Reads every episode, embeds title+content, and upserts into vec_sidecar.db.
    Use after: changing embedding model, first-time setup, or after manual .md edits.
    """
    import os
    store = get_store()
    embedding_cfg = getattr(args, "_embedding_config", None) or _cfg.load(cwd=os.getcwd())

    try:
        from lib.embeddings import get_provider
        provider = get_provider(embedding_cfg)
    except (ImportError, ValueError) as e:
        print(f"Cannot load embedding provider: {e}", file=sys.stderr)
        sys.exit(1)

    episodes = store.list_episodes()
    total = len(episodes)
    if total == 0:
        print("No episodes found.")
        return

    sidecar = store._get_vec()
    ok = 0
    skipped = 0
    for i, ep in enumerate(episodes, 1):
        try:
            text = f"{ep.title}\n{ep.content}"
            vec = provider.embed(text)
            sidecar.upsert(ep.id, vec)
            ok += 1
        except Exception as exc:
            skipped += 1
            if args.verbose:
                print(f"  skip {ep.id}: {exc}", file=sys.stderr)
        if i % 50 == 0 or i == total:
            print(f"\r  {i}/{total} embedded ({ok} ok, {skipped} skipped)", end="", flush=True)

    print(f"\nDone. {ok} episodes indexed, {skipped} skipped.")
    print(f"Vec sidecar now holds {sidecar.count()} entries.")


def cmd_observe(args):
    """(internal) Ingest a tool-use event (JSON on stdin) into the instinct engine.

    Called by hooks. Quiet and non-blocking by design — never fails the tool.
    """
    from lib.instincts import InstinctEngine

    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}
    if getattr(args, "phase", None):
        event.setdefault("phase", args.phase)

    try:
        ie = InstinctEngine(get_store())
        ie.observe(event)
        if getattr(args, "analyze", False):
            res = ie.analyze(force=getattr(args, "force", False))
            if res.get("created") or res.get("reinforced"):
                print(json.dumps(res))
    except Exception:
        pass  # never block the tool on a memory hiccup
    sys.exit(0)


def cmd_instinct(args):
    """Inspect and manage continuous-learning instincts."""
    from lib.instincts import InstinctEngine

    ie = InstinctEngine(get_store())
    action = getattr(args, "action", None)

    if action == "list":
        items = ie.list_instincts(scope=args.scope, min_confidence=args.min_confidence)
        if not items:
            print("No instincts yet — they form as tool-use patterns recur.")
            return
        print(f"\n{len(items)} instinct(s):\n")
        for e in items:
            ctx = e.context_snapshot or {}
            projects = len(ctx.get("projects", []))
            obs = ctx.get("observations", "?")
            perm = " *global" if e.is_permanent else ""
            print(f"  [{e.confidence:.2f}] {e.title:18} obs={obs} projects={projects}{perm}  {e.id}")
    elif action == "analyze":
        print(json.dumps(ie.analyze(force=getattr(args, "force", False)), indent=2))
    elif action == "show":
        e = ie.store.get_episode(args.id)
        if not e:
            print("Not found.")
            return
        print(json.dumps(
            {"id": e.id, "title": e.title, "confidence": e.confidence,
             "content": e.content, "context": e.context_snapshot, "tags": e.tags},
            indent=2))
    elif action == "reinforce":
        c = ie.reinforce(args.id)
        print(f"confidence -> {c:.2f}" if c is not None else "Not found.")
    elif action == "weaken":
        c = ie.weaken(args.id)
        print(f"confidence -> {c:.2f}" if c is not None else "Not found.")
    elif action == "forget":
        print("✓ removed" if ie.store.delete_episode(args.id) else "Not found.")
    elif action == "evolve":
        path = ie.evolve(instinct_ids=args.ids or None, kind=args.kind, name=args.name)
        print(f"✓ evolved -> {path}" if path else "No instincts met the evolve threshold.")
    elif action == "promote":
        print("✓ promoted to global" if ie.promote(args.id)
              else "Not enough distinct projects to promote yet.")
    else:
        print("Usage: crisp instinct <list|analyze|show|reinforce|weaken|forget|evolve|promote>")


def main():
    parser = argparse.ArgumentParser(description="Memory management CLI")

    # ── Global embedding flags (override env vars / config files) ─────────────
    _eg = parser.add_argument_group(
        "embedding",
        "Override embedding provider for this invocation. "
        "Env vars: CRISP_EMBEDDING_PROVIDER, CRISP_EMBEDDING_MODEL, "
        "CRISP_EMBEDDING_URL, CRISP_EMBEDDING_DIM. "
        "Persistent defaults: ~/.config/crisp/config.json or .crisp.json in project root.",
    )
    _eg.add_argument("--embedding-provider", metavar="PROVIDER",
                     help="huggingface | ollama | dspy | word2vec")
    _eg.add_argument("--embedding-model", metavar="MODEL",
                     help="e.g. qllama/bge-large-en-v1.5:latest  or  BAAI/bge-small-en-v1.5")
    _eg.add_argument("--embedding-url", metavar="URL",
                     help="Ollama API URL (default: http://localhost:11434/api/embeddings)")
    _eg.add_argument("--embedding-dim", metavar="N", type=int,
                     help="Vector dimension (inferred from model if omitted)")

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Save
    save_parser = subparsers.add_parser("save", help="Save memory")
    save_parser.add_argument("content", help="Memory content")
    save_parser.add_argument("--title", help="Title")
    save_parser.add_argument("--category", help="Category")
    save_parser.add_argument("--importance", type=float, help="Importance (0-1)")
    save_parser.add_argument("--tags", nargs="*", help="Tags")
    save_parser.add_argument("--permanent", action="store_true", help="Mark permanent")

    # Search
    search_parser = subparsers.add_parser("search", help="Search memory")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--limit", type=int, default=20, help="Result limit")
    search_parser.add_argument(
        "--semantic",
        action="store_true",
        help="USER-TRIGGERED embedding search (configurable provider; mock by default). "
        "Plain search uses keyword/structured retrieval.",
    )

    # Show
    show_parser = subparsers.add_parser("show", help="Show episode")
    show_parser.add_argument("id", help="Episode ID")

    # Forget
    forget_parser = subparsers.add_parser("forget", help="Delete episode")
    forget_parser.add_argument("id", help="Episode ID")
    forget_parser.add_argument("--force", action="store_true", help="Delete even if permanent")

    # Stats
    subparsers.add_parser("stats", help="Show statistics")

    # List
    list_parser = subparsers.add_parser("list", help="List episodes")
    list_parser.add_argument("--layer", help="Filter by layer")

    # Reflect
    reflect_parser = subparsers.add_parser("reflect", help="Run consolidation")
    reflect_parser.add_argument(
        "--batch-size", type=int, default=20, help="L0 batch size"
    )

    # Prune
    subparsers.add_parser("prune", help="Run pruning")

    # Export
    export_parser = subparsers.add_parser("export", help="Export memory")
    export_parser.add_argument("--output", help="Output file path")

    # Index
    index_parser = subparsers.add_parser("index", help="Structural index of a file (tree-sitter/regex)")
    index_parser.add_argument("file_path", help="Path to file to index")
    index_parser.add_argument("--json", action="store_true", help="Output raw IndexResult as JSON for skill consumption")
    index_parser.add_argument("--dry-run", action="store_true", help="Show what would be saved without saving")
    index_parser.add_argument("--verbose", "-v", action="store_true", help="List individual episodes")

    # Classify-language
    cl_lang_parser = subparsers.add_parser(
        "classify-language",
        help="Record a language classification Crisp Engine's own detection couldn't resolve",
    )
    cl_lang_parser.add_argument("file_path", help="Path to the file")
    cl_lang_parser.add_argument("language", help="Language name (e.g. Rust, TOML)")
    cl_lang_parser.add_argument(
        "--scope", choices=["path", "ext"], default="path",
        help="path=only this file, ext=every file with this extension (default: path)",
    )

    # Save-index
    si_parser = subparsers.add_parser("save-index", help="Save a semantic index entry (Claude-generated)")
    si_parser.add_argument("--path", required=True, help="File or directory path this entry describes")
    si_parser.add_argument("--level", required=True,
                           choices=list(_INDEX_LEVELS.keys()),
                           help="Granularity: symbol|file|dir|module|feature|project")
    si_parser.add_argument("--content", required=True, help="The semantic summary content")
    si_parser.add_argument("--tags", nargs="*", help="Additional tags")
    si_parser.add_argument("--permanent", action="store_true", help="Mark as permanent (won't decay)")

    # Search-path
    sp_parser = subparsers.add_parser("search-path", help="Find index entries for a file/dir path")
    sp_parser.add_argument("path", help="File or directory path to look up")
    sp_parser.add_argument("--level", choices=list(_INDEX_LEVELS.keys()), help="Filter by level")

    # Status
    status_parser = subparsers.add_parser("status", help="Show indexed/stale/pending for files under a path")
    status_parser.add_argument("path", nargs="?", default=".", help="Root path to check (default: cwd)")
    status_parser.add_argument("--depth", type=int, default=4, help="Max directory depth (default: 4)")

    # Checkpoint
    ckpt_parser = subparsers.add_parser("checkpoint", help="Save session midpoint snapshot with git diff")
    ckpt_parser.add_argument("--session-id", help="Session ID to associate checkpoint with")

    # Changelog
    cl_parser = subparsers.add_parser("changelog", help="Save a changelog entry with git diff")
    cl_parser.add_argument("--trigger", default="manual",
                           choices=["manual", "milestone", "finally_works", "major_change",
                                    "pre_compact", "session_end", "checkpoint"],
                           help="What caused this changelog entry")
    cl_parser.add_argument("--outcome", help="Short outcome description (e.g. 'calibration overlay fixed')")
    cl_parser.add_argument("--note", help="Additional free-text note")
    cl_parser.add_argument("--stat-only", action="store_true", help="Use git diff --stat instead of full diff")

    # Tree
    tree_parser = subparsers.add_parser("tree", help="Print annotated project tree (like GNU tree + index status)")
    tree_parser.add_argument("root", nargs="?", default=".", help="Root directory (default: cwd)")
    tree_parser.add_argument("--depth", type=int, default=3, help="Max depth, capped at 3 (default: 3)")
    tree_parser.add_argument("--no-status", action="store_true", help="Omit index status markers")
    tree_parser.add_argument("--untracked", action="store_true", help="Include untracked (non-ignored) files")

    # Config
    cfg_parser = subparsers.add_parser("config", help="Get/set persistent embedding/store config")
    cfg_sub = cfg_parser.add_subparsers(dest="cfg_action")
    cfg_get = cfg_sub.add_parser("get", help="Show current merged config")
    cfg_set = cfg_sub.add_parser("set", help="Persist key=value pairs")
    cfg_set.add_argument("pairs", nargs="+", metavar="KEY=VALUE",
                         help="e.g. embedding_provider=ollama embedding_model=qllama/bge-large-en-v1.5:latest")
    cfg_set.add_argument("--global", dest="global_scope", action="store_true",
                         help="Write to ~/.config/crisp/config.json (default: .crisp.json in cwd)")

    # Projects
    subparsers.add_parser("projects", help="List all projects")

    # Switch
    switch_parser = subparsers.add_parser("switch", help="Switch to project memory")
    switch_parser.add_argument("project_id", help="Project ID")

    # Logs
    logs_parser = subparsers.add_parser("logs", help="Show or tail the Crisp Engine log file")
    logs_group = logs_parser.add_mutually_exclusive_group()
    logs_group.add_argument("--tail", "-f", action="store_true", help="Follow log in real-time (like tail -f)")
    logs_group.add_argument("--last", action="store_true", help="Print the last N lines")
    logs_parser.add_argument("--lines", "-n", type=int, default=50, help="Number of lines (default: 50)")

    # Reindex vecs
    rv_parser = subparsers.add_parser("reindex-vecs", help="Rebuild sqlite-vec sidecar from all stored episodes")
    rv_parser.add_argument("--verbose", "-v", action="store_true", help="Print skipped episodes")

    # Observe (internal: hooks pipe a tool-use event as JSON on stdin)
    obs_parser = subparsers.add_parser("observe", help="(internal) ingest a tool-use event from stdin")
    obs_parser.add_argument("--phase", help="pre | post")
    obs_parser.add_argument("--analyze", action="store_true", help="distill after observing")
    obs_parser.add_argument("--force", action="store_true", help="force distillation")

    # Instinct (continuous learning)
    inst_parser = subparsers.add_parser("instinct", help="Continuous-learning instincts")
    inst_sub = inst_parser.add_subparsers(dest="action")
    il = inst_sub.add_parser("list", help="List instincts by confidence")
    il.add_argument("--scope", help="Filter by scope tag (project key or 'global')")
    il.add_argument("--min-confidence", type=float, default=0.0, dest="min_confidence")
    ia = inst_sub.add_parser("analyze", help="Distill observation buffer now")
    ia.add_argument("--force", action="store_true", help="ignore min_observations")
    ish = inst_sub.add_parser("show", help="Show an instinct")
    ish.add_argument("id")
    ir = inst_sub.add_parser("reinforce", help="+confidence")
    ir.add_argument("id")
    iw = inst_sub.add_parser("weaken", help="-confidence")
    iw.add_argument("id")
    ifg = inst_sub.add_parser("forget", help="Delete an instinct")
    ifg.add_argument("id")
    iev = inst_sub.add_parser("evolve", help="Emit a skill/command/agent from instincts")
    iev.add_argument("ids", nargs="*", help="Instinct IDs (default: all >= evolve_threshold)")
    iev.add_argument("--kind", default="skill", choices=["skill", "command", "agent"])
    iev.add_argument("--name", help="Artifact name")
    ip = inst_sub.add_parser("promote", help="Promote an instinct project->global")
    ip.add_argument("id")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Build merged embedding config: global file → .crisp.json → env vars → CLI flags
    _cli_overrides = {
        "embedding_provider": getattr(args, "embedding_provider", None),
        "embedding_model":    getattr(args, "embedding_model", None),
        "embedding_api_url":  getattr(args, "embedding_url", None),
        "embedding_dim":      getattr(args, "embedding_dim", None),
    }
    args._embedding_config = _cfg.load(overrides=_cli_overrides)

    commands = {
        "save": cmd_save,
        "search": cmd_search,
        "show": cmd_show,
        "forget": cmd_forget,
        "stats": cmd_stats,
        "list": cmd_list,
        "reflect": cmd_reflect,
        "prune": cmd_prune,
        "export": cmd_export,
        "index": cmd_index,
        "classify-language": cmd_classify_language,
        "save-index": cmd_save_index,
        "search-path": cmd_search_path,
        "status": cmd_status,
        "checkpoint": cmd_checkpoint,
        "changelog": cmd_changelog,
        "tree": cmd_tree,
        "config": cmd_config,
        "logs": cmd_logs,
        "reindex-vecs": cmd_reindex_vecs,
        "projects": cmd_projects,
        "switch": cmd_switch,
        "observe": cmd_observe,
        "instinct": cmd_instinct,
    }

    _log.info("cmd=%s", args.command, extra={"session_id": "-", "project": "-"})

    try:
        commands[args.command](args)
    except Exception as e:
        _log.error("cmd=%s failed: %s", args.command, e, extra={"session_id": "-", "project": "-"})
        print(f"Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
