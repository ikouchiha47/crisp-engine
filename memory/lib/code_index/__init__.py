"""Code structure extraction: tries tree-sitter, then ctags (planned), then
regex, in order — first strategy to succeed wins. CodeElement is the shared
type all three strategies produce.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class CodeElement:
    """Represents a code element (function, class, method, etc.)."""

    id: str
    name: str
    type: str  # function, class, method, module, variable
    language: str
    file_path: str
    start_line: int
    end_line: int
    signature: str = ""
    docstring: str = ""
    body: str = ""
    full_content: str = ""
    complexity: int = 1
    dependencies: List[str] = field(default_factory=list)
    calls: List[str] = field(default_factory=list)
    hash: str = ""

    def compute_hash(self) -> str:
        """Compute hash of the element's body."""
        if not self.hash and self.body:
            self.hash = hashlib.sha256(self.body.encode()).hexdigest()[:16]
        return self.hash


class CodeAnalyzer:
    """Orchestrates code structure extraction across strategies."""

    LANGUAGE_EXTENSIONS = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".c": "c_cpp",
        ".cpp": "c_cpp",
        ".h": "c_cpp",
        ".hpp": "c_cpp",
    }

    def __init__(self):
        self._regex = None  # lazy: only instantiated if tree-sitter/ctags miss

    def _get_language(self, file_path: str) -> Optional[str]:
        """Determine language from file extension."""
        ext = Path(file_path).suffix.lower()
        return self.LANGUAGE_EXTENSIONS.get(ext)

    def analyze_file(self, file_path: str) -> List[CodeElement]:
        """Analyze a source file and extract all code elements."""
        file_path = str(Path(file_path).resolve())
        fp = Path(file_path)

        # 1. Tree-sitter first (has its own extension mapping, wider coverage).
        # Truthy check, not `is not None`: the grammar pack covers 371
        # languages but _walk()'s node-type dispatch only knows a handful —
        # for the rest, parse_file returns [] (grammar loaded fine, zero
        # elements extracted), which must fall through to ctags/regex, not
        # be treated as a successful empty result.
        try:
            from .treesitter_strategy import parse_file
            result = parse_file(fp)
            if result:
                return result
        except Exception:
            pass

        # 2. ctags — broad safety net, ~164 languages, zero per-language install
        try:
            from .ctags_strategy import parse_file as ctags_parse_file
            result = ctags_parse_file(fp)
            if result:
                return result
        except Exception:
            pass

        # 3. Regex — true last resort
        language = self._get_language(file_path)
        if not language:
            return []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            return []

        lines = content.splitlines()

        if self._regex is None:
            from .regex_strategy import RegexStrategy
            self._regex = RegexStrategy()
        elements = self._regex.extract(lines, file_path, language)

        for elem in elements:
            elem.compute_hash()

        return elements

    def analyze_directory(
        self, directory: str, extensions: Optional[List[str]] = None
    ) -> Dict[str, List[CodeElement]]:
        """Analyze all source files in a directory."""
        directory = str(Path(directory).resolve())
        if extensions is None:
            extensions = list(self.LANGUAGE_EXTENSIONS.keys())

        results = {}
        for ext in extensions:
            for fp in Path(directory).rglob(f"*{ext}"):
                elements = self.analyze_file(str(fp))
                if elements:
                    results[str(fp)] = elements

        return results
