"""Regex-based structure extraction — true last resort in the
tree-sitter -> ctags -> regex fallback chain. Verbatim logic moved
from the old analyzer.py (only the class wrapper changed) so the
already-debugged extraction methods are not retyped/risked again."""

import re
from typing import List

from . import CodeElement


class RegexStrategy:
    """Extracts CodeElements via hand-written per-language regexes."""

    def __init__(self):
        self.element_id_counter = 0

    PATTERNS = {
        "python": {
            "class": re.compile(r"^\s*class\s+(\w+)(?:\s*\(([^)]*)\))?:\s*"),
            "function": re.compile(
                r"^\s*def\s+(\w+)\s*\(([^)]*)\)\s*(?:->\s*[^:]+)?:\s*"
            ),
            "async_function": re.compile(
                r"^\s*async\s+def\s+(\w+)\s*\(([^)]*)\)\s*(?:->\s*[^:]+)?:\s*"
            ),
            "decorator": re.compile(r"^\s*@\s*(\w+(?:\.\w+)*)\s*$"),
            "import": re.compile(r"^\s*(?:import|from)\s+(\S+)"),
        },
        "javascript": {
            "class": re.compile(r"^\s*class\s+(\w+)\s*(?:extends\s+\w+)?\s*{?\s*$"),
            "function": re.compile(
                r"^\s*(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)\s*{?\s*$"
            ),
            "arrow_function": re.compile(
                r"^\s*(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>\s*{?\s*$"
            ),
            # Negative lookahead excludes JS control-flow/reserved keywords —
            # without it, `if (x) {`, `while (x) {`, `catch (e) {` etc. all
            # match the same shape as a real method definition and get
            # indexed as bogus functions literally named "if"/"while"/"catch".
            "method": re.compile(
                r"^\s*(?!(?:if|else|for|while|switch|catch|do|return|function|"
                r"typeof|instanceof|new|delete|void|yield|await|throw|with)\b)"
                r"(\w+)\s*\(([^)]*)\)\s*{?\s*$"
            ),
        },
        "typescript": {
            "class": re.compile(
                r"^\s*class\s+(\w+)\s*(?:extends\s+\w+)?\s*(?:implements\s+[^\s]+)?\s*{?\s*$"
            ),
            "function": re.compile(
                r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)\s*(?::\s*[^\s]+)?\s*{?\s*$"
            ),
            "arrow_function": re.compile(
                r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*(?::\s*[^\s]+)?\s*=>\s*{?\s*$"
            ),
        },
        "java": {
            "class": re.compile(
                r"^\s*(?:public|private|protected)?\s*(?:abstract\s+)?class\s+(\w+)\s*(?:extends\s+\w+)?\s*(?:implements\s+[^\s]+)?\s*{?\s*$"
            ),
            "method": re.compile(
                r"^\s*(?:public|private|protected|static|final|synchronized|native|abstract|transient)+[\s\w<>\[\]]*\s+(\w+)\s*\(([^)]*)\)\s*(?::\s*[^\s]+)?\s*{?\s*$"
            ),
        },
        "go": {
            "function": re.compile(
                r"^\s*func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(([^)]*)\)\s*(?:\([^)]*\))?\s*{?\s*$"
            ),
            "type": re.compile(r"^\s*type\s+(\w+)\s+struct\s*{?\s*$"),
        },
        "rust": {
            "function": re.compile(
                r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*\(([^)]*)\)\s*(?:->\s*[^\s]+)?\s*{?\s*$"
            ),
            "struct": re.compile(r"^\s*(?:pub\s+)?struct\s+(\w+)\s*{?\s*$"),
        },
        "c_cpp": {
            "function": re.compile(
                r"^\s*(?:static\s+)?(?:inline\s+)?(?:[\w:]+\s+)+\*?\s*(\w+)\s*\(([^)]*)\)\s*{?\s*$"
            ),
            "struct": re.compile(r"^\s*struct\s+(\w+)\s*{?\s*$"),
        },
    }

    def _next_id(self) -> str:
        """Generate unique element ID."""
        self.element_id_counter += 1
        return f"elem_{self.element_id_counter}"

    def _extract_python_structure(
        self, lines: List[str], file_path: str
    ) -> List[CodeElement]:
        """Extract Python code structure."""
        elements = []
        i = 0
        current_decorators = []

        while i < len(lines):
            line = lines[i]
            stripped = line.rstrip()

            # Decorator
            dec_match = self.PATTERNS["python"]["decorator"].match(stripped)
            if dec_match:
                current_decorators.append(dec_match.group(1))
                i += 1
                continue

            # Class
            class_match = self.PATTERNS["python"]["class"].match(stripped)
            if class_match:
                class_name = class_match.group(1)
                bases = class_match.group(2) or ""
                start_line = i

                # Find class body
                indent = len(line) - len(line.lstrip())
                body_lines = []
                j = i + 1
                class_methods = []

                while j < len(lines):
                    next_line = lines[j]
                    if (
                        next_line.strip()
                        and not next_line.startswith(" " * (indent + 1))
                        and not next_line.startswith("\t")
                    ):
                        if next_line.strip().startswith("@"):
                            pass  # Decorator at same indent level
                        else:
                            break
                    body_lines.append(next_line)

                    # Check for methods inside class
                    method_match = self.PATTERNS["python"]["function"].match(
                        next_line.rstrip()
                    )
                    async_match = self.PATTERNS["python"]["async_function"].match(
                        next_line.rstrip()
                    )
                    if method_match or async_match:
                        match = method_match or async_match
                        method_name = match.group(1)
                        method_args = match.group(2)
                        method_start = j

                        # Find method body
                        method_indent = len(next_line) - len(next_line.lstrip())
                        method_body_lines = [next_line]
                        k = j + 1
                        while k < len(lines):
                            ml = lines[k]
                            if (
                                ml.strip()
                                and not ml.startswith(" " * (method_indent + 1))
                                and not ml.startswith("\t")
                            ):
                                if not ml.strip().startswith("@"):
                                    break
                            method_body_lines.append(ml)
                            k += 1

                        method_body = "".join(method_body_lines)
                        docstring = self._extract_docstring(method_body_lines)

                        elem = CodeElement(
                            id=self._next_id(),
                            name=method_name,
                            type="method",
                            language="python",
                            file_path=file_path,
                            start_line=method_start,
                            end_line=k - 1,
                            signature=f"def {method_name}({method_args})",
                            docstring=docstring,
                            body=method_body,
                            full_content=method_body,
                        )
                        class_methods.append(elem)
                        j = k - 1

                    j += 1

                class_body = "".join(body_lines)
                docstring = self._extract_docstring(body_lines)

                class_elem = CodeElement(
                    id=self._next_id(),
                    name=class_name,
                    type="class",
                    language="python",
                    file_path=file_path,
                    start_line=start_line,
                    end_line=i + len(body_lines),
                    signature=f"class {class_name}({bases})",
                    docstring=docstring,
                    body=class_body,
                    full_content=class_body,
                    dependencies=[bases] if bases else [],
                )
                elements.append(class_elem)
                elements.extend(class_methods)

                # The class's own body-scan (the `j` loop above) already
                # consumed every line belonging to this class, methods
                # included. Advancing past body_lines' *last* line (not
                # landing on it) is what makes the next main-loop iteration
                # look at genuinely unprocessed code — landing on it instead
                # re-scanned the class's final line as if unprocessed.
                i += len(body_lines) + 1
                current_decorators = []
                continue

            # Function (module-level)
            func_match = self.PATTERNS["python"]["function"].match(stripped)
            async_match = self.PATTERNS["python"]["async_function"].match(stripped)
            if func_match or async_match:
                match = func_match or async_match
                func_name = match.group(1)
                func_args = match.group(2)
                start_line = i

                # No "skip if inside a class" guard needed here: the class
                # branch above already advances `i` past a class's entire
                # body (methods included) via its own body-scan, so by the
                # time this branch runs, `i` can only be at genuine
                # module-level code. A persistent current_class flag here
                # previously never got reset, silently dropping every
                # module-level function that came after any class at all.

                # Find function body
                indent = len(line) - len(line.lstrip())
                body_lines = [line]
                j = i + 1
                while j < len(lines):
                    next_line = lines[j]
                    if (
                        next_line.strip()
                        and not next_line.startswith(" " * (indent + 1))
                        and not next_line.startswith("\t")
                    ):
                        if not next_line.strip().startswith("@"):
                            break
                    body_lines.append(next_line)
                    j += 1

                func_body = "".join(body_lines)
                docstring = self._extract_docstring(body_lines)

                elem = CodeElement(
                    id=self._next_id(),
                    name=func_name,
                    type="function",
                    language="python",
                    file_path=file_path,
                    start_line=start_line,
                    end_line=j - 1,
                    signature=f"def {func_name}({func_args})",
                    docstring=docstring,
                    body=func_body,
                    full_content=func_body,
                )
                elements.append(elem)

                i = j
                current_decorators = []
                continue

            i += 1

        return elements

    def _extract_docstring(self, lines: List[str]) -> str:
        """Extract docstring from lines."""
        if not lines:
            return ""
        # Look for triple-quoted string after first line
        in_docstring = False
        docstring_lines = []
        quote_char = None

        for i, line in enumerate(lines):
            stripped = line.strip()
            if i == 0:
                continue  # Skip def/class line

            # Check for opening triple quotes
            if '"""' in stripped or "'''" in stripped:
                quote = '"""' if '"""' in stripped else "'''"
                if not in_docstring:
                    in_docstring = True
                    quote_char = quote
                    # Extract content after opening quote
                    parts = stripped.split(quote, 1)
                    if len(parts) > 1:
                        content = parts[1]
                        if content.endswith(quote):
                            docstring_lines.append(content[:-3])
                            in_docstring = False
                        else:
                            docstring_lines.append(content)
                    continue
                else:
                    # Closing quote
                    if quote_char and quote in stripped:
                        parts = stripped.split(quote, 1)
                        docstring_lines.append(parts[0])
                        in_docstring = False
                    continue

            if in_docstring:
                docstring_lines.append(stripped)

        return "\n".join(docstring_lines).strip()

    def _extract_generic_structure(
        self, lines: List[str], file_path: str, language: str
    ) -> List[CodeElement]:
        """Generic structure extraction for non-Python languages."""
        elements = []
        if language not in self.PATTERNS:
            return elements

        patterns = self.PATTERNS[language]
        i = 0

        while i < len(lines):
            line = lines[i]
            stripped = line.rstrip()

            # Try each pattern type
            for elem_type, pattern in patterns.items():
                match = pattern.match(stripped)
                if match:
                    name = match.group(1)
                    args = match.group(2) if match.lastindex > 1 else ""
                    start_line = i

                    # Find body (simplified: look for matching braces)
                    body_lines = [line]
                    brace_count = line.count("{") - line.count("}")
                    j = i + 1

                    while j < len(lines) and brace_count > 0:
                        body_lines.append(lines[j])
                        brace_count += lines[j].count("{") - lines[j].count("}")
                        j += 1

                    if brace_count == 0 and j > i + 1:
                        # Include closing brace line
                        if j < len(lines):
                            body_lines.append(lines[j])
                            j += 1

                    body = "".join(body_lines)

                    elem = CodeElement(
                        id=self._next_id(),
                        name=name,
                        type=elem_type,
                        language=language,
                        file_path=file_path,
                        start_line=start_line,
                        end_line=j - 1,
                        signature=f"{elem_type} {name}",
                        body=body,
                        full_content=body,
                    )
                    elements.append(elem)

                    i = j - 1
                    break

            i += 1

        return elements

    def extract(self, lines: List[str], file_path: str, language: str) -> List[CodeElement]:
        """Dispatch to the python-specific or generic extractor."""
        if language == "python":
            return self._extract_python_structure(lines, file_path)
        return self._extract_generic_structure(lines, file_path, language)
