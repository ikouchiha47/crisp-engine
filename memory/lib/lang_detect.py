"""Language detection backed by GitHub Linguist's real data (languages.yml +
heuristics.yml, vendored under lib/langdata/, MIT licensed).

Tiered, mechanical, no LLM involved for the vast majority of files:

  Tier 0  classification cache (exact path, then extension) — a prior
          agent/human classification, checked first so it can override
          anything below.
  Tier 1  languages.yml: extension -> language. Single match = done.
  Tier 2  languages.yml gave multiple candidates (or heuristics.yml has
          rules for this extension anyway) -> heuristics.yml's real,
          content-based regex disambiguation rules, evaluated in order,
          first match wins.
  Tier 3  no extension at all -> shebang line against languages.yml's
          `interpreters` field.
  Tier 4  still unresolved -> None. Caller (SessionStart) collects these
          and surfaces them via additionalContext for Claude to classify
          opportunistically; `classify_language()` writes the answer back
          into the Tier 0 cache so it's a static lookup from then on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml

_LANGDATA_DIR = Path(__file__).parent / "langdata"

_cache_loaded = False
_ext_to_langs: Dict[str, List[str]] = {}
_interpreter_to_lang: Dict[str, str] = {}
_heuristics_by_ext: Dict[str, List[dict]] = {}
_named_patterns: Dict[str, str] = {}
_lang_type: Dict[str, str] = {}  # language name -> "programming"|"markup"|"data"|"prose"
_source_extensions: set = set()  # extensions where every candidate language is programming/markup


def _compile_named_pattern(value) -> str:
    if isinstance(value, list):
        return "(?:" + "|".join(value) + ")"
    return str(value)


def _load() -> None:
    global _cache_loaded
    if _cache_loaded:
        return

    languages = yaml.safe_load((_LANGDATA_DIR / "languages.yml").read_text())
    for lang_name, spec in languages.items():
        if not isinstance(spec, dict):
            continue
        _lang_type[lang_name] = spec.get("type", "")
        for ext in spec.get("extensions") or []:
            _ext_to_langs.setdefault(ext, []).append(lang_name)
        for interp in spec.get("interpreters") or []:
            _interpreter_to_lang.setdefault(interp, lang_name)

    # An extension counts as "source" if ANY language it could be is
    # programming/markup — err toward indexing when ambiguous rather than
    # silently skipping a file that might be real code.
    for ext, lang_names in _ext_to_langs.items():
        if any(_lang_type.get(n) in ("programming", "markup") for n in lang_names):
            _source_extensions.add(ext)

    heuristics = yaml.safe_load((_LANGDATA_DIR / "heuristics.yml").read_text())
    for name, pattern in (heuristics.get("named_patterns") or {}).items():
        _named_patterns[name] = _compile_named_pattern(pattern)

    for block in heuristics.get("disambiguations") or []:
        rules = block.get("rules") or []
        for ext in block.get("extensions") or []:
            _heuristics_by_ext.setdefault(ext, []).extend(rules)

    _cache_loaded = True


def _resolve_pattern(rule: dict) -> Optional[re.Pattern]:
    if "named_pattern" in rule:
        raw = _named_patterns.get(rule["named_pattern"], "")
    elif "pattern" in rule:
        raw = _compile_named_pattern(rule["pattern"])
    else:
        return None
    try:
        return re.compile(raw, re.MULTILINE)
    except re.error:
        return None


def _rule_matches(rule: dict, content: str) -> bool:
    if "and" in rule:
        return all(_rule_matches(sub, content) for sub in rule["and"])
    if "negative_pattern" in rule:
        pat = re.compile(_compile_named_pattern(rule["negative_pattern"]), re.MULTILINE)
        return not pat.search(content)
    pat = _resolve_pattern(rule)
    if pat is None:
        # No pattern/named_pattern/and/negative_pattern at all -> unconditional
        # default rule (e.g. plain "language: C" as the final fallback for .h).
        return True
    return bool(pat.search(content))


def _disambiguate(ext: str, content: str) -> Optional[str]:
    for rule in _heuristics_by_ext.get(ext, []):
        if _rule_matches(rule, content):
            return rule.get("language")
    return None


# ---------------------------------------------------------------------------
# Tier 0: classification cache — exact path or extension, human/agent-written
# ---------------------------------------------------------------------------

def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / "lang_classifications.json"


def _read_cache(cache_dir: Path) -> dict:
    p = _cache_path(cache_dir)
    if not p.exists():
        return {"by_path": {}, "by_ext": {}}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"by_path": {}, "by_ext": {}}


def classify_language(cache_dir: Path, file_path: str, language: str, scope: str = "path") -> None:
    """Record a classification so future lookups skip straight to Tier 0.

    scope="path": only this exact file is affected (safe default for a
                  genuinely one-off/ambiguous file).
    scope="ext":  every file with this extension is affected (use when the
                  classification generalizes, e.g. a project-specific but
                  extension-consistent DSL).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    data = _read_cache(cache_dir)
    if scope == "ext":
        ext = Path(file_path).suffix
        data["by_ext"][ext] = language
    else:
        data["by_path"][str(Path(file_path).resolve())] = language
    _cache_path(cache_dir).write_text(json.dumps(data, indent=2))


def detect_language(file_path: str, cache_dir: Optional[Path] = None, content: Optional[str] = None) -> Optional[str]:
    """Detect a file's language. Returns None if genuinely unresolved (Tier 4)."""
    _load()
    path = Path(file_path)

    if cache_dir is not None:
        cache = _read_cache(cache_dir)
        exact = cache["by_path"].get(str(path.resolve()))
        if exact:
            return exact
        by_ext = cache["by_ext"].get(path.suffix)
        if by_ext:
            return by_ext

    ext = path.suffix
    if ext:
        candidates = _ext_to_langs.get(ext, [])
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            # Ambiguous per languages.yml itself -> need content.
            text = content if content is not None else _safe_read(path)
            if text is not None:
                resolved = _disambiguate(ext, text)
                if resolved:
                    return resolved
            # Heuristics didn't resolve it either -> languages.yml still
            # lists candidates; take the first as a documented, visible
            # best-effort rather than silently picking nothing.
            return candidates[0]
        # Extension unknown to languages.yml at all, but heuristics.yml
        # might still have rules keyed on it (rare, but real in the data).
        if ext in _heuristics_by_ext:
            text = content if content is not None else _safe_read(path)
            if text is not None:
                resolved = _disambiguate(ext, text)
                if resolved:
                    return resolved
        return None

    # No extension -> shebang.
    text = content if content is not None else _safe_read(path)
    if text:
        first_line = text.splitlines()[0] if text.splitlines() else ""
        if first_line.startswith("#!"):
            interp = Path(first_line[2:].strip().split()[-1] if first_line[2:].strip() else "").name
            lang = _interpreter_to_lang.get(interp)
            if lang:
                return lang
    return None


def is_source_extension(ext: str) -> bool:
    """True if `ext` belongs to any programming/markup language per Linguist.

    Replaces hand-maintained extension sets like hooks.py's old
    SOURCE_EXTENSIONS (11 entries) with the real ~hundreds-strong Linguist
    set — same "derive from one source of truth" fix already applied to
    grammar detection, extended to the capture-gating question of "is this
    worth indexing at all" rather than "which language is it."
    """
    _load()
    return ext in _source_extensions


def _safe_read(path: Path, max_bytes: int = 4096) -> Optional[str]:
    try:
        with path.open("rb") as f:
            raw = f.read(max_bytes)
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None
