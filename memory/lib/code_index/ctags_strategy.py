"""Universal-ctags based structure extraction — the middle rung of the
tree-sitter -> ctags -> regex fallback chain. One binary, ~164 languages
(`ctags --list-languages`), zero per-language install — the broad safety
net under tree-sitter's narrower, higher-fidelity coverage.

Schema verified against a real installed `ctags --version` (Universal
Ctags 6.2.1) before writing any parsing code — not guessed. Key finding:
ctags' `kind` field is not a reliable function/method signal by itself
(e.g. Go tags a struct field "member", same kind Python uses for methods)
— the `signature` field (present only on genuinely callable tags, emitted
via --fields=+S) is the reliable one, verified against both a Go struct
field (no signature) and a Go method (has signature) side by side.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from . import CodeElement

_CTAGS_BIN: Optional[str] = None
_checked = False


def _ctags_binary() -> Optional[str]:
    """Path to a working universal-ctags binary, or None if unavailable.

    macOS ships a legacy BSD ctags at /usr/bin/ctags with none of the flags
    below — shutil.which() alone would find that and silently misbehave, so
    this actually runs --version and checks it self-identifies as Universal
    Ctags rather than assuming any "ctags" on PATH is the right one.
    """
    global _CTAGS_BIN, _checked
    if _checked:
        return _CTAGS_BIN
    _checked = True

    for candidate in ("ctags", "universal-ctags", "/opt/homebrew/bin/ctags"):
        path = shutil.which(candidate) if not candidate.startswith("/") else (
            candidate if Path(candidate).exists() else None
        )
        if not path:
            continue
        try:
            out = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=5
            )
            if "Universal Ctags" in out.stdout:
                _CTAGS_BIN = path
                return path
        except Exception:
            continue

    return None


def has_ctags() -> bool:
    return _ctags_binary() is not None


# ctags' own `language` field is Title Case, close to Linguist's naming —
# normalized the same way as analyzer.py's regex-fallback / tree-sitter's
# canonical language field, so a CodeElement's `language` is consistent
# regardless of which of the three strategies produced it.
_CANONICAL_LANGUAGE: dict[str, str] = {
    "Python": "python",
    "JavaScript": "javascript",
    "TypeScript": "typescript",
    "TSX": "typescript",
    "Go": "go",
    "C": "c_cpp",
    "C++": "c_cpp",
    "Rust": "rust",
    "Java": "java",
}


def _elem_id(file_path: str, name: str, line: int) -> str:
    h = hashlib.md5(f"{file_path}:{name}:{line}".encode()).hexdigest()[:8]
    return f"ctags_{name}_{h}"


def parse_file(file_path: Path) -> Optional[List[CodeElement]]:
    """Parse a file with universal-ctags. None if ctags unavailable or the
    file produced no recognizable tags (caller falls through to regex)."""
    ctags_bin = _ctags_binary()
    if ctags_bin is None:
        return None

    try:
        result = subprocess.run(
            [ctags_bin, "--output-format=json", "--fields=+neSl", "-f", "-", str(file_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    try:
        source_lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError:
        source_lines = []

    elements: List[CodeElement] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            tag = json.loads(line)
        except json.JSONDecodeError:
            continue
        if tag.get("_type") != "tag":
            continue

        kind = tag.get("kind", "")
        has_signature = "signature" in tag
        if has_signature:
            elem_type = "method" if tag.get("scope") else "function"
        elif kind in ("class", "struct", "interface", "enum"):
            elem_type = "class"
        else:
            continue  # field/variable/package/import/etc. — not interesting here

        start_line = int(tag.get("line", 1)) - 1  # ctags is 1-indexed
        end_line = int(tag.get("end", tag.get("line", 1))) - 1
        body = "".join(source_lines[start_line:end_line + 1]) if source_lines else ""

        lang_raw = tag.get("language", "")
        language = _CANONICAL_LANGUAGE.get(lang_raw, lang_raw.lower())

        name = tag.get("name", "<anon>")
        sig = f"{kind} {name}{tag.get('signature', '')}"

        elements.append(CodeElement(
            id=_elem_id(str(file_path), name, start_line),
            name=name,
            type=elem_type,
            language=language,
            file_path=str(file_path),
            start_line=start_line,
            end_line=end_line,
            signature=sig,
            body=body,
            full_content=body,
            hash=hashlib.sha256(body.encode()).hexdigest()[:16] if body else "",
        ))

    return elements if elements else None
