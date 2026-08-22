"""Tree-sitter based code parser.

Lazy grammar loading: grammars are imported on first use per language.

Primary source: `tree-sitter-language-pack` (PyPI) — one package, 371
pre-compiled grammars, verified against this codebase's actual dependency
resolution before adopting it (see conversation record: 12/12 of our
previously hand-picked languages match its naming exactly, and its own
extension->grammar bridge below matched 454 of Linguist's ~1,479
tracked extensions on first try). This replaces the old model of one
separate `tree-sitter-<lang>` pip package per language (13 hand-typed
entries) — same "derive from a real, comprehensive source instead of a
hand-maintained list" fix applied everywhere else in this codebase.

GRAMMAR_REGISTRY is kept as a fallback for any grammar key NOT covered by
the language pack but separately pip-installed the old way — falls back to
None (regex/ctags handle it) if neither path has it.
"""
import hashlib
import importlib
from pathlib import Path
from typing import List, Optional

from . import CodeElement

# Fallback: old per-language pip package registry, only consulted if the
# language pack (below) doesn't have the requested key.
GRAMMAR_REGISTRY: dict[str, tuple[str, str, str]] = {
    "typescript":   ("tree_sitter_typescript", "language_typescript", "tree-sitter-typescript"),
    "tsx":          ("tree_sitter_typescript", "language_tsx",        "tree-sitter-typescript"),
    "javascript":   ("tree_sitter_javascript", "language",            "tree-sitter-javascript"),
    "jsx":          ("tree_sitter_javascript", "language",            "tree-sitter-javascript"),
    "python":       ("tree_sitter_python",     "language",            "tree-sitter-python"),
    "go":           ("tree_sitter_go",         "language",            "tree-sitter-go"),
    "c":            ("tree_sitter_c",          "language",            "tree-sitter-c"),
    "cpp":          ("tree_sitter_cpp",        "language",            "tree-sitter-cpp"),
    "rust":         ("tree_sitter_rust",       "language",            "tree-sitter-rust"),
    "java":         ("tree_sitter_java",       "language",            "tree-sitter-java"),
    "kotlin":       ("tree_sitter_kotlin",     "language",            "tree-sitter-kotlin"),
    "scala":        ("tree_sitter_scala",      "language",            "tree-sitter-scala"),
    "swift":        ("tree_sitter_swift",      "language",            "tree-sitter-swift"),
}

# Small hand-kept core (fast path, no lang_detect dependency needed for the
# common case) — anything else is resolved dynamically via _dynamic_ext_map()
# below, sourced from real Linguist data + the language pack's actual
# supported-language set, not hand-typed.
EXT_TO_GRAMMAR: dict[str, str] = {
    ".ts":   "typescript",
    ".tsx":  "tsx",
    ".js":   "javascript",
    ".jsx":  "jsx",
    ".py":   "python",
    ".go":   "go",
    ".c":    "c",
    ".h":    "c",
    ".cpp":  "cpp",
    ".hpp":  "cpp",
    ".ino":  "cpp",   # Arduino is C++
    ".rs":   "rust",
    ".java": "java",
    ".kt":   "kotlin",
    ".scala": "scala",
    ".swift": "swift",
}

_dynamic_ext_map_cache: Optional[dict[str, str]] = None


def _normalize_lang_name(name: str) -> str:
    return name.lower().replace(" ", "").replace("#", "sharp").replace("+", "p").replace(".", "")


def _dynamic_ext_map() -> dict[str, str]:
    """Extension -> grammar key for every language the pack supports and
    Linguist tracks, beyond the small hand-kept EXT_TO_GRAMMAR core above.
    Built once, cached — real data, not a maintained list.
    """
    global _dynamic_ext_map_cache
    if _dynamic_ext_map_cache is not None:
        return _dynamic_ext_map_cache

    result: dict[str, str] = {}
    try:
        import typing
        from tree_sitter_language_pack import SupportedLanguage
        pkg_langs = set(typing.get_args(SupportedLanguage))

        from ..lang_detect import _load as _load_lang_data, _ext_to_langs, _lang_type
        _load_lang_data()

        for ext, lang_names in _ext_to_langs.items():
            if ext in EXT_TO_GRAMMAR:
                continue  # hand-kept core already covers it
            for lname in lang_names:
                if _lang_type.get(lname) not in ("programming", "markup"):
                    continue
                norm = _normalize_lang_name(lname)
                if norm in pkg_langs:
                    result[ext] = norm
                    break
    except Exception:
        pass  # language pack or lang_detect unavailable — dynamic map just stays empty

    _dynamic_ext_map_cache = result
    return result

# Tree-sitter grammar keys that diverge from analyzer.py's regex-fallback
# language naming for the same file type — normalized so the "language"
# field on a CodeElement is consistent regardless of which path produced it.
_CANONICAL_LANGUAGE: dict[str, str] = {
    "tsx": "typescript",
    "jsx": "javascript",
    "c": "c_cpp",
    "cpp": "c_cpp",
}

_lang_cache: dict[str, object] = {}  # grammar_key → Language object or None sentinel


def _load_grammar(grammar_key: str):
    """Lazily load a grammar Language object.

    Tries tree-sitter-language-pack first (371 languages, one dependency),
    falls back to the old per-package GRAMMAR_REGISTRY for anything the pack
    doesn't have but got separately pip-installed. None if neither has it.
    """
    if grammar_key in _lang_cache:
        return _lang_cache[grammar_key]

    try:
        from tree_sitter_language_pack import get_language
        language = get_language(grammar_key)
        _lang_cache[grammar_key] = language
        return language
    except Exception:
        pass

    if grammar_key in GRAMMAR_REGISTRY:
        mod_name, fn_name, _ = GRAMMAR_REGISTRY[grammar_key]
        try:
            from tree_sitter import Language
            mod = importlib.import_module(mod_name)
            lang_fn = getattr(mod, fn_name)
            language = Language(lang_fn())
            _lang_cache[grammar_key] = language
            return language
        except (ImportError, AttributeError):
            pass

    _lang_cache[grammar_key] = None
    return None


def grammar_key_for(file_path: Path) -> Optional[str]:
    ext = file_path.suffix.lower()
    return EXT_TO_GRAMMAR.get(ext) or _dynamic_ext_map().get(ext)


def has_grammar(file_path: Path) -> bool:
    key = grammar_key_for(file_path)
    return key is not None and _load_grammar(key) is not None


def missing_grammars() -> list[tuple[str, str]]:
    """Return list of (grammar_key, pip_package) for uninstalled grammars."""
    missing = []
    seen_pkgs: set[str] = set()
    for key, (_, _, pkg) in GRAMMAR_REGISTRY.items():
        if pkg in seen_pkgs:
            continue
        if _load_grammar(key) is None:
            missing.append((key, pkg))
            seen_pkgs.add(pkg)
    return missing


def grammar_status() -> list[dict]:
    """Return status of all grammar packages (installed/missing)."""
    rows = []
    seen_pkgs: set[str] = set()
    for key, (_, _, pkg) in GRAMMAR_REGISTRY.items():
        if pkg in seen_pkgs:
            continue
        seen_pkgs.add(pkg)
        lang = _load_grammar(key)
        rows.append({"key": key, "pkg": pkg, "installed": lang is not None})
    return rows


# lib.lang_detect returns GitHub Linguist's canonical language names (e.g.
# "Rust", "TypeScript", "C++"); GRAMMAR_REGISTRY's keys are lowercase,
# tree-sitter-package-shaped ("rust", "typescript", "cpp"). This is the one
# place that bridges the two vocabularies.
LINGUIST_TO_GRAMMAR: dict[str, str] = {
    "Python": "python",
    "JavaScript": "javascript",
    "TypeScript": "typescript",
    "TSX": "tsx",
    "Go": "go",
    "C": "c",
    "C++": "cpp",
    "Rust": "rust",
    "Java": "java",
    "Kotlin": "kotlin",
    "Scala": "scala",
    "Swift": "swift",
}


def repo_grammar_status(languages: "set[str]") -> list[dict]:
    """Three-state status (installed / available-not-installed / unsupported)
    for a set of Linguist language names actually detected in a repo — the
    nvim-treesitter-style registry: what's usable now vs. what a
    `huh install-grammars` run would improve vs. what nothing helps.

    Checks the language pack's real ~371-language set first (via name
    normalization, not a hand-typed map), falling back to the old
    LINGUIST_TO_GRAMMAR/GRAMMAR_REGISTRY pairing only for anything the pack
    doesn't cover but was separately pip-installed the old way.
    """
    try:
        import typing
        from tree_sitter_language_pack import SupportedLanguage
        pkg_langs = set(typing.get_args(SupportedLanguage))
    except Exception:
        pkg_langs = set()

    rows = []
    for lang_name in sorted(languages):
        norm = _normalize_lang_name(lang_name)
        if norm in pkg_langs:
            installed = _load_grammar(norm) is not None
            rows.append({
                "language": lang_name,
                "status": "installed" if installed else "available-not-installed",
                "pip_package": "tree-sitter-language-pack",
            })
            continue

        grammar_key = LINGUIST_TO_GRAMMAR.get(lang_name)
        if grammar_key is None:
            rows.append({"language": lang_name, "status": "unsupported", "pip_package": None})
            continue
        pkg = GRAMMAR_REGISTRY[grammar_key][2]
        installed = _load_grammar(grammar_key) is not None
        rows.append({
            "language": lang_name,
            "status": "installed" if installed else "available-not-installed",
            "pip_package": pkg,
        })
    return rows


def _src(source: bytes, node) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _elem_id(file_path: str, name: str, line: int) -> str:
    h = hashlib.md5(f"{file_path}:{name}:{line}".encode()).hexdigest()[:8]
    return f"ts_{name}_{h}"


def _extract_docstring_from_body(node, source: bytes) -> str:
    """Pull the first string literal from a statement_block as a docstring."""
    if node is None or node.type != "statement_block":
        return ""
    for child in node.children:
        if child.type == "expression_statement":
            for sub in child.children:
                if sub.type in ("string", "template_string"):
                    raw = _src(source, sub)
                    return raw.strip("'\"` \n")
    return ""


def _extract_c_function_name(declarator_node, source: bytes) -> Optional[str]:
    """Walk C/C++ declarator chain to find the function name identifier."""
    if declarator_node is None:
        return None
    t = declarator_node.type
    if t == "identifier":
        return _src(source, declarator_node)
    if t in ("function_declarator", "pointer_declarator", "reference_declarator"):
        inner = declarator_node.child_by_field_name("declarator")
        return _extract_c_function_name(inner, source)
    # qualified name (C++): foo::bar
    if t == "qualified_identifier":
        name_node = declarator_node.child_by_field_name("name")
        return _src(source, name_node) if name_node else None
    return None


def _make_elem(
    name: str,
    elem_type: str,
    node,
    source: bytes,
    file_path: str,
    language: str,
    parent_class: Optional[str] = None,
) -> CodeElement:
    params_node = node.child_by_field_name("parameters")
    ret_node = node.child_by_field_name("return_type")
    body_node = node.child_by_field_name("body")

    params_str = _src(source, params_node) if params_node else "()"
    ret_str = ": " + _src(source, ret_node).lstrip(": ") if ret_node else ""

    if elem_type == "class":
        sig = f"class {name}"
    elif parent_class:
        sig = f"{name}{params_str}{ret_str}"
    else:
        sig = f"function {name}{params_str}{ret_str}"

    body_str = _src(source, body_node) if body_node else ""
    docstring = _extract_docstring_from_body(body_node, source)

    # Hash the FULL body before truncating what's stored/displayed. An edit
    # past character 2000 must still change the hash — hashing the
    # already-truncated body (what compute_hash() would otherwise do,
    # lazily, later) meant edits past that point were invisible to change
    # detection: identical truncated body -> identical hash -> "unchanged".
    import hashlib
    full_hash = hashlib.sha256(body_str.encode()).hexdigest()[:16] if body_str else ""

    return CodeElement(
        id=_elem_id(file_path, name, node.start_point[0]),
        name=name,
        type=elem_type,
        language=_CANONICAL_LANGUAGE.get(language, language),
        file_path=file_path,
        start_line=node.start_point[0],
        end_line=node.end_point[0],
        signature=sig,
        docstring=docstring,
        body=body_str[:2000],
        full_content=body_str[:2000],
        hash=full_hash,
    )


def _walk(node, source: bytes, file_path: str, language: str,
          elements: list, parent_class: Optional[str] = None) -> None:
    t = node.type

    if t == "export_statement":
        decl = node.child_by_field_name("declaration")
        if decl:
            _walk(decl, source, file_path, language, elements, parent_class)
        return

    if t == "function_declaration":
        name_node = node.child_by_field_name("name")
        name = _src(source, name_node) if name_node else "<anon>"
        elem_type = "method" if parent_class else "function"
        elements.append(_make_elem(name, elem_type, node, source, file_path, language, parent_class))
        return  # don't recurse into function body

    if t == "class_declaration":
        name_node = node.child_by_field_name("name")
        name = _src(source, name_node) if name_node else "<anon>"
        elements.append(_make_elem(name, "class", node, source, file_path, language))
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                _walk(child, source, file_path, language, elements, name)
        return

    if t == "method_definition":
        name_node = node.child_by_field_name("name")
        name = _src(source, name_node) if name_node else "<anon>"
        elements.append(_make_elem(name, "method", node, source, file_path, language, parent_class))
        return

    if t in ("lexical_declaration", "variable_declaration"):
        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                value_node = child.child_by_field_name("value")
                if name_node and value_node and value_node.type in ("arrow_function", "function_expression"):
                    name = _src(source, name_node)
                    elem_type = "method" if parent_class else "function"
                    elements.append(_make_elem(name, elem_type, value_node, source, file_path, language, parent_class))
        return

    # ── Python ────────────────────────────────────────────────────────────────

    if t == "function_definition" and language == "python":
        name_node = node.child_by_field_name("name")
        name = _src(source, name_node) if name_node else "<anon>"
        elem_type = "method" if parent_class else "function"
        elements.append(_make_elem(name, elem_type, node, source, file_path, language, parent_class))
        return

    if t == "class_definition":
        name_node = node.child_by_field_name("name")
        name = _src(source, name_node) if name_node else "<anon>"
        elements.append(_make_elem(name, "class", node, source, file_path, language))
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                _walk(child, source, file_path, language, elements, name)
        return

    # ── Go ────────────────────────────────────────────────────────────────────

    if t == "method_declaration":
        name_node = node.child_by_field_name("name")
        name = _src(source, name_node) if name_node else "<anon>"
        elements.append(_make_elem(name, "method", node, source, file_path, language, parent_class))
        return

    if t == "type_spec":
        type_node = node.child_by_field_name("type")
        if type_node and type_node.type == "struct_type":
            name_node = node.child_by_field_name("name")
            name = _src(source, name_node) if name_node else "<anon>"
            elements.append(CodeElement(
                id=_elem_id(file_path, name, node.start_point[0]),
                name=name, type="class",
                language=_CANONICAL_LANGUAGE.get(language, language),
                file_path=file_path,
                start_line=node.start_point[0], end_line=node.end_point[0],
                signature=f"type {name} struct",
            ))
        return

    # ── C / C++ ───────────────────────────────────────────────────────────────

    if t == "function_definition" and language in ("c", "cpp", "arduino"):
        # name is nested: declarator → function_declarator → declarator (identifier)
        decl = node.child_by_field_name("declarator")
        name = _extract_c_function_name(decl, source)
        if name:
            elem_type = "method" if parent_class else "function"
            elements.append(_make_elem(name, elem_type, node, source, file_path, language, parent_class))
        return

    if t in ("class_specifier", "struct_specifier") and language == "cpp":
        name_node = node.child_by_field_name("name")
        name = _src(source, name_node) if name_node else None
        if name:
            elements.append(CodeElement(
                id=_elem_id(file_path, name, node.start_point[0]),
                name=name, type="class",
                language=_CANONICAL_LANGUAGE.get(language, language),
                file_path=file_path,
                start_line=node.start_point[0], end_line=node.end_point[0],
                signature=f"class {name}",
            ))
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    _walk(child, source, file_path, language, elements, name)
            return

    # Recurse for all other nodes
    for child in node.children:
        _walk(child, source, file_path, language, elements, parent_class)


def _collect_imports(root_node, source: bytes, language: str) -> list[dict]:
    """Extract import/include statements from AST. Returns list of dicts."""
    imports = []

    def _walk_imports(node):
        t = node.type

        # TypeScript / JavaScript
        if t == "import_statement":
            src_node = node.child_by_field_name("source")
            if src_node:
                path = _src(source, src_node).strip("\"'`")
                clause = next((c for c in node.children if c.type == "import_clause"), None)
                names: list[str] = []
                if clause:
                    for child in clause.children:
                        if child.type == "identifier":
                            names.append(_src(source, child))
                        elif child.type == "named_imports":
                            for spec in child.children:
                                if spec.type == "import_specifier":
                                    n = spec.child_by_field_name("name")
                                    if n:
                                        names.append(_src(source, n))
                        elif child.type == "namespace_import":
                            for sub in child.children:
                                if sub.type == "identifier":
                                    names.append("* as " + _src(source, sub))
                imports.append({"path": path, "names": names, "local": path.startswith(".")})
            return

        # Python
        if t == "import_statement":  # handled above but py uses same name
            pass
        if t == "import_from_statement":
            mod_node = node.child_by_field_name("module_name")
            path = _src(source, mod_node).replace(".", "/") if mod_node else ""
            names = [_src(source, c) for c in node.children if c.type == "dotted_name" and c != mod_node]
            imports.append({"path": path, "names": names, "local": path.startswith(".")})
            return

        # C / C++ includes
        if t == "preproc_include":
            path_node = next((c for c in node.children if c.type in ("string_literal", "system_lib_string")), None)
            if path_node:
                path = _src(source, path_node).strip("\"<>")
                local = not _src(source, path_node).startswith("<")
                imports.append({"path": path, "names": [], "local": local})
            return

        # Go imports
        if t == "import_spec":
            path_node = node.child_by_field_name("path")
            if path_node:
                path = _src(source, path_node).strip("\"")
                imports.append({"path": path, "names": [], "local": False})
            return

        for child in node.children:
            _walk_imports(child)

    _walk_imports(root_node)
    return imports


def parse_file(file_path: Path) -> Optional[List[CodeElement]]:
    """Parse a file with tree-sitter. Returns None if no grammar available."""
    grammar_key = grammar_key_for(file_path)
    if not grammar_key:
        return None

    lang = _load_grammar(grammar_key)
    if lang is None:
        return None

    try:
        from tree_sitter import Parser
        source = file_path.read_bytes()
        parser = Parser(lang)
        tree = parser.parse(source)
    except Exception:
        return None

    # NOTE: `language` here stays the raw grammar_key (tsx/jsx/c/cpp) on
    # purpose — _walk()'s internal dispatch below checks e.g.
    # `language == "cpp"` against tree-sitter-specific node types, and
    # normalizing here would break that. Normalization to analyzer.py's
    # naming (typescript/javascript/c_cpp) happens instead at each
    # CodeElement construction site, where it only affects the field a
    # consumer actually sees.
    language = grammar_key
    elements: List[CodeElement] = []
    _walk(tree.root_node, source, str(file_path), language, elements)

    for elem in elements:
        elem.compute_hash()

    return elements


def parse_imports(file_path: Path) -> list[dict]:
    """Extract imports from a file. Returns [] if no grammar or parse error."""
    grammar_key = grammar_key_for(file_path)
    if not grammar_key:
        return []
    lang = _load_grammar(grammar_key)
    if lang is None:
        return []
    try:
        from tree_sitter import Parser
        source = file_path.read_bytes()
        parser = Parser(lang)
        tree = parser.parse(source)
        return _collect_imports(tree.root_node, source, grammar_key)
    except Exception:
        return []
