"""Code intelligence tools for the cocoindex-code MCP server.

Provides list_symbols, find_definition, find_references, code_metrics,
and rename_symbol tools using regex-based multi-language symbol extraction.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .filesystem_tools import (
    MAX_READ_BYTES,
    MAX_RESULTS,
    _detect_lang,
    _is_binary,
    _is_excluded_dir,
    _relative,
    _root,
    _safe_resolve,
)

# === Pydantic result models ===


class SymbolEntry(BaseModel):
    """A symbol found in source code."""

    name: str = Field(description="Symbol name")
    symbol_type: str = Field(
        description="Type: function, method, class, variable, constant, "
        "interface, type, enum, struct, trait, module, impl"
    )
    line: int = Field(description="Start line number (1-indexed)")
    end_line: int = Field(description="End line number (1-indexed)")
    signature: str = Field(description="Source line where symbol is defined")
    indent_level: int = Field(default=0, description="Indentation level")


class ListSymbolsResult(BaseModel):
    """Result from list_symbols tool."""

    success: bool
    path: str = ""
    symbols: list[SymbolEntry] = Field(default_factory=list)
    total_symbols: int = 0
    language: str = ""
    message: str | None = None


class DefinitionEntry(BaseModel):
    """A symbol definition location."""

    file_path: str = Field(description="Relative file path")
    name: str = Field(description="Symbol name")
    symbol_type: str = Field(description="Symbol type")
    line: int = Field(description="Line number (1-indexed)")
    signature: str = Field(description="Definition line content")
    context: str = Field(default="", description="Surrounding context")


class FindDefinitionResult(BaseModel):
    """Result from find_definition tool."""

    success: bool
    definitions: list[DefinitionEntry] = Field(default_factory=list)
    total_found: int = 0
    message: str | None = None


class ReferenceEntry(BaseModel):
    """A single reference to a symbol."""

    path: str = Field(description="Relative file path")
    line_number: int = Field(description="1-indexed line number")
    line: str = Field(description="Matched line content")
    usage_type: str = Field(
        default="other",
        description="Usage type: import, call, assignment, "
        "type_annotation, definition, other",
    )
    context_before: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)


class FindReferencesResult(BaseModel):
    """Result from find_references tool."""

    success: bool
    references: list[ReferenceEntry] = Field(default_factory=list)
    total_found: int = 0
    files_searched: int = 0
    truncated: bool = False
    message: str | None = None


class MetricsData(BaseModel):
    """Code quality metrics."""

    total_lines: int = Field(description="Total line count")
    code_lines: int = Field(description="Non-blank, non-comment lines")
    blank_lines: int = Field(description="Blank line count")
    comment_lines: int = Field(description="Comment line count")
    functions: int = Field(description="Number of functions/methods")
    classes: int = Field(description="Number of classes/structs")
    avg_function_length: float = Field(
        default=0.0, description="Average function body length"
    )
    max_function_length: int = Field(
        default=0, description="Longest function body length"
    )
    max_nesting_depth: int = Field(
        default=0, description="Max indentation nesting depth"
    )
    complexity_estimate: int = Field(
        default=0, description="Estimated cyclomatic complexity"
    )


class CodeMetricsResult(BaseModel):
    """Result from code_metrics tool."""

    success: bool
    path: str = ""
    metrics: MetricsData | None = None
    language: str = ""
    message: str | None = None


class RenameChange(BaseModel):
    """A file changed by rename_symbol."""

    file_path: str = Field(description="Relative file path")
    occurrences: int = Field(description="Number of replacements in this file")


class RenameResult(BaseModel):
    """Result from rename_symbol tool."""

    success: bool
    old_name: str = ""
    new_name: str = ""
    files_changed: int = 0
    total_replacements: int = 0
    changes: list[RenameChange] = Field(default_factory=list)
    dry_run: bool = True
    message: str | None = None


# === Multi-language symbol extraction patterns ===
# Each entry: (compiled_regex, symbol_type, name_group_index)

_PatternEntry = tuple[re.Pattern[str], str, int]


def _build_patterns() -> dict[str, list[_PatternEntry]]:
    """Build symbol extraction patterns per language."""

    def _c(pattern: str, flags: int = 0) -> re.Pattern[str]:
        return re.compile(pattern, flags)

    python: list[_PatternEntry] = [
        (_c(r"^(\s*)(async\s+)?def\s+(\w+)\s*\("), "function", 3),
        (_c(r"^(\s*)class\s+(\w+)"), "class", 2),
        (_c(r"^([A-Z][A-Z0-9_]{1,})\s*[=:]"), "constant", 1),
    ]

    javascript: list[_PatternEntry] = [
        (
            _c(r"^(\s*)(?:export\s+)?(?:default\s+)?"
               r"(?:async\s+)?function\s*\*?\s+(\w+)"),
            "function", 2,
        ),
        (_c(r"^(\s*)(?:export\s+)?(?:default\s+)?class\s+(\w+)"),
         "class", 2),
        (_c(r"^(\s*)(?:export\s+)?(?:const|let|var)\s+(\w+)"),
         "variable", 2),
    ]

    ts_extra: list[_PatternEntry] = [
        (_c(r"^(\s*)(?:export\s+)?interface\s+(\w+)"),
         "interface", 2),
        (_c(r"^(\s*)(?:export\s+)?type\s+(\w+)\s*[=<{]"),
         "type", 2),
        (_c(r"^(\s*)(?:export\s+)?enum\s+(\w+)"), "enum", 2),
    ]
    typescript = javascript + ts_extra

    rust: list[_PatternEntry] = [
        (_c(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)"),
         "function", 2),
        (_c(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?struct\s+(\w+)"),
         "struct", 2),
        (_c(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?enum\s+(\w+)"),
         "enum", 2),
        (_c(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?trait\s+(\w+)"),
         "trait", 2),
        (_c(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)"),
         "module", 2),
        (_c(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+(\w+)"),
         "constant", 2),
        (_c(r"^(\s*)impl(?:\s*<[^>]*>)?\s+(\w+)"), "impl", 2),
    ]

    go: list[_PatternEntry] = [
        (_c(r"^func\s+(?:\([^)]*\)\s+)?(\w+)\s*\("),
         "function", 1),
        (_c(r"^type\s+(\w+)\s+struct\b"), "struct", 1),
        (_c(r"^type\s+(\w+)\s+interface\b"), "interface", 1),
        (_c(r"^(?:const|var)\s+(\w+)"), "variable", 1),
    ]

    java: list[_PatternEntry] = [
        (_c(r"^(\s*)(?:(?:public|private|protected|static|"
            r"abstract|final|sealed|partial)\s+)*class\s+(\w+)"),
         "class", 2),
        (_c(r"^(\s*)(?:(?:public|private|protected|static|"
            r"abstract|final)\s+)*interface\s+(\w+)"),
         "interface", 2),
        (_c(r"^(\s*)(?:(?:public|private|protected|static|"
            r"abstract|final)\s+)*enum\s+(\w+)"),
         "enum", 2),
    ]

    c_patterns: list[_PatternEntry] = [
        (_c(r"^(\s*)(?:typedef\s+)?struct\s+(\w+)"), "struct", 2),
        (_c(r"^(\s*)#define\s+(\w+)"), "constant", 2),
        (_c(r"^(\s*)enum(?:\s+class)?\s+(\w+)"), "enum", 2),
    ]

    cpp_extra: list[_PatternEntry] = [
        (_c(r"^(\s*)class\s+(\w+)"), "class", 2),
        (_c(r"^(\s*)namespace\s+(\w+)"), "module", 2),
    ]
    cpp = c_patterns + cpp_extra

    php: list[_PatternEntry] = [
        (_c(r"^(\s*)(?:(?:public|private|protected|static|"
            r"abstract|final)\s+)*function\s+(\w+)"),
         "function", 2),
        (_c(r"^(\s*)(?:abstract\s+|final\s+)?class\s+(\w+)"),
         "class", 2),
        (_c(r"^(\s*)interface\s+(\w+)"), "interface", 2),
        (_c(r"^(\s*)trait\s+(\w+)"), "trait", 2),
    ]

    ruby: list[_PatternEntry] = [
        (_c(r"^(\s*)def\s+(?:self\.)?(\w+)"), "function", 2),
        (_c(r"^(\s*)class\s+(\w+)"), "class", 2),
        (_c(r"^(\s*)module\s+(\w+)"), "module", 2),
    ]

    shell: list[_PatternEntry] = [
        (_c(r"^(\s*)(?:function\s+)?(\w+)\s*\(\s*\)"),
         "function", 2),
        (_c(r"^([A-Z_][A-Z0-9_]*)\s*="), "variable", 1),
    ]

    sql: list[_PatternEntry] = [
        (_c(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?"
            r"(?:FUNCTION|PROCEDURE)\s+(\w+)",
            re.IGNORECASE),
         "function", 1),
        (_c(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?"
            r"(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
            re.IGNORECASE),
         "type", 1),
    ]

    return {
        "python": python,
        "javascript": javascript,
        "typescript": typescript,
        "rust": rust,
        "go": go,
        "java": java,
        "csharp": java,  # same base patterns
        "c": c_patterns,
        "cpp": cpp,
        "php": php,
        "ruby": ruby,
        "shell": shell,
        "sql": sql,
        "kotlin": java,
        "scala": java,
    }


_SYMBOL_PATTERNS: dict[str, list[_PatternEntry]] = _build_patterns()


# === Core internal functions ===


def _extract_symbols(content: str, language: str) -> list[SymbolEntry]:
    """Extract symbols from file content using regex patterns."""
    patterns = _SYMBOL_PATTERNS.get(language, [])
    if not patterns:
        return []

    lines = content.splitlines()
    raw_symbols: list[SymbolEntry] = []

    for line_idx, line_text in enumerate(lines):
        line_num = line_idx + 1
        for pattern, sym_type, name_group in patterns:
            m = pattern.match(line_text)
            if m is None:
                continue
            name = m.group(name_group)
            # Compute indent level
            stripped = line_text.lstrip()
            indent = len(line_text) - len(stripped)
            indent_level = indent // 4 if indent > 0 else 0

            actual_type = sym_type
            # Python: indented function → method
            if language == "python" and sym_type == "function":
                if indent > 0:
                    actual_type = "method"

            raw_symbols.append(SymbolEntry(
                name=name,
                symbol_type=actual_type,
                line=line_num,
                end_line=line_num,  # computed below
                signature=line_text.rstrip(),
                indent_level=indent_level,
            ))
            break  # first match wins per line

    # Compute end_line for each symbol
    for i, sym in enumerate(raw_symbols):
        if i + 1 < len(raw_symbols):
            next_sym = raw_symbols[i + 1]
            # End at line before next symbol at same or lesser indent
            if next_sym.indent_level <= sym.indent_level:
                sym.end_line = next_sym.line - 1
            else:
                # Next symbol is nested; scan further
                end = len(lines)
                for j in range(i + 1, len(raw_symbols)):
                    if raw_symbols[j].indent_level <= sym.indent_level:
                        end = raw_symbols[j].line - 1
                        break
                sym.end_line = end
        else:
            sym.end_line = len(lines)

    return raw_symbols


def _walk_source_files(
    root: Path,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
) -> list[tuple[Path, str, str]]:
    """Walk codebase and return (abs_path, rel_path, language) tuples."""
    lang_set = (
        {lang.lower() for lang in languages} if languages else None
    )
    results: list[tuple[Path, str, str]] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not _is_excluded_dir(d)
        )
        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            rel = _relative(fpath)
            lang = _detect_lang(fpath)

            if lang_set and lang.lower() not in lang_set:
                continue
            if paths and not any(
                fnmatch.fnmatch(rel, p) for p in paths
            ):
                continue
            if _is_binary(fpath):
                continue

            results.append((fpath, rel, lang))

    return results


def _classify_usage(
    line: str, symbol_name: str, language: str,
) -> str:
    """Classify how a symbol is used on a given line."""
    stripped = line.strip()

    # Import patterns
    import_patterns = [
        r"\bimport\b", r"\bfrom\b.*\bimport\b",
        r"\brequire\s*\(", r"\buse\s+",
        r"\binclude\b", r"\busing\b",
    ]
    for pat in import_patterns:
        if re.search(pat, stripped):
            return "import"

    # Definition patterns (def, class, fn, func, struct, etc.)
    def_patterns = [
        rf"(?:def|fn|func|function)\s+{re.escape(symbol_name)}\s*\(",
        rf"class\s+{re.escape(symbol_name)}\b",
        rf"struct\s+{re.escape(symbol_name)}\b",
        rf"trait\s+{re.escape(symbol_name)}\b",
        rf"interface\s+{re.escape(symbol_name)}\b",
        rf"enum\s+{re.escape(symbol_name)}\b",
        rf"type\s+{re.escape(symbol_name)}\b",
    ]
    for pat in def_patterns:
        if re.search(pat, stripped):
            return "definition"

    # Call: symbol followed by (
    if re.search(
        rf"\b{re.escape(symbol_name)}\s*\(", stripped,
    ):
        return "call"

    # Type annotation: : symbol or -> symbol or <symbol>
    if re.search(
        rf"[:\->]\s*{re.escape(symbol_name)}\b", stripped,
    ):
        return "type_annotation"

    # Assignment: symbol = ... or ... = symbol
    if re.search(
        rf"\b{re.escape(symbol_name)}\s*=[^=]", stripped,
    ):
        return "assignment"

    return "other"


def _find_definitions_impl(
    symbol_name: str,
    root: Path,
    symbol_type: str | None = None,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    limit: int = 20,
) -> list[DefinitionEntry]:
    """Find symbol definitions across codebase."""
    results: list[DefinitionEntry] = []
    files = _walk_source_files(root, languages=languages, paths=paths)

    for fpath, rel, lang in files:
        if len(results) >= limit:
            break
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        symbols = _extract_symbols(content, lang)
        for sym in symbols:
            if sym.name != symbol_name:
                continue
            if symbol_type and sym.symbol_type != symbol_type:
                continue

            lines = content.splitlines()
            ctx_start = max(0, sym.line - 2)
            ctx_end = min(len(lines), sym.line + 2)
            context = "\n".join(lines[ctx_start:ctx_end])

            results.append(DefinitionEntry(
                file_path=rel,
                name=sym.name,
                symbol_type=sym.symbol_type,
                line=sym.line,
                signature=sym.signature,
                context=context,
            ))
            if len(results) >= limit:
                break

    return results


def _find_references_impl(
    symbol_name: str,
    root: Path,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    context_lines: int = 0,
    limit: int = 50,
) -> tuple[list[ReferenceEntry], int, int, bool]:
    """Find all references to a symbol."""
    word_re = re.compile(rf"\b{re.escape(symbol_name)}\b")
    refs: list[ReferenceEntry] = []
    total = 0
    files_searched = 0
    truncated = False

    files = _walk_source_files(root, languages=languages, paths=paths)

    for fpath, rel, lang in files:
        try:
            if fpath.stat().st_size > MAX_READ_BYTES:
                continue
            content = fpath.read_text(
                encoding="utf-8", errors="replace",
            )
        except OSError:
            continue

        files_searched += 1
        file_lines = content.splitlines()

        for i, line_text in enumerate(file_lines):
            if not word_re.search(line_text):
                continue
            total += 1
            if len(refs) >= limit:
                truncated = True
                continue

            ctx_before = [
                file_lines[j].rstrip("\n\r")
                for j in range(
                    max(0, i - context_lines), i,
                )
            ]
            ctx_after = [
                file_lines[j].rstrip("\n\r")
                for j in range(
                    i + 1,
                    min(len(file_lines), i + 1 + context_lines),
                )
            ]

            usage = _classify_usage(line_text, symbol_name, lang)

            refs.append(ReferenceEntry(
                path=rel,
                line_number=i + 1,
                line=line_text.rstrip("\n\r"),
                usage_type=usage,
                context_before=ctx_before,
                context_after=ctx_after,
            ))

    return refs, total, files_searched, truncated


# Comment line patterns per language
_COMMENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "python": re.compile(r"^\s*#"),
    "ruby": re.compile(r"^\s*#"),
    "shell": re.compile(r"^\s*#"),
    "javascript": re.compile(r"^\s*//"),
    "typescript": re.compile(r"^\s*//"),
    "rust": re.compile(r"^\s*//"),
    "go": re.compile(r"^\s*//"),
    "java": re.compile(r"^\s*//"),
    "csharp": re.compile(r"^\s*//"),
    "c": re.compile(r"^\s*//"),
    "cpp": re.compile(r"^\s*//"),
    "php": re.compile(r"^\s*(?://|#)"),
    "sql": re.compile(r"^\s*--"),
    "kotlin": re.compile(r"^\s*//"),
    "scala": re.compile(r"^\s*//"),
}

# Branching keywords for complexity estimation
_COMPLEXITY_KEYWORDS: re.Pattern[str] = re.compile(
    r"\b(?:if|elif|else|for|while|and|or|try|except|catch"
    r"|case|when|switch|\?|&&|\|\|)\b"
)


def _compute_metrics(content: str, language: str) -> MetricsData:
    """Compute code metrics for file content."""
    lines = content.splitlines()
    total_lines = len(lines)
    blank_lines = sum(1 for line in lines if not line.strip())

    # Count comment lines
    comment_pat = _COMMENT_PATTERNS.get(language)
    comment_lines = 0
    if comment_pat:
        comment_lines = sum(
            1 for line in lines
            if line.strip() and comment_pat.match(line)
        )

    code_lines = total_lines - blank_lines - comment_lines

    # Extract symbols for function/class counts
    symbols = _extract_symbols(content, language)
    func_types = {"function", "method"}
    class_types = {"class", "struct"}
    funcs = [s for s in symbols if s.symbol_type in func_types]
    classes = [s for s in symbols if s.symbol_type in class_types]

    # Function lengths
    func_lengths = [
        s.end_line - s.line + 1 for s in funcs if s.end_line >= s.line
    ]
    avg_func_len = (
        sum(func_lengths) / len(func_lengths) if func_lengths else 0.0
    )
    max_func_len = max(func_lengths) if func_lengths else 0

    # Max nesting depth via indentation
    max_depth = 0
    for line in lines:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        # Use 4 spaces or 1 tab as one level
        depth = indent // 4 if "\t" not in line else line.count("\t")
        if depth > max_depth:
            max_depth = depth

    # Complexity estimate: count branching keywords
    complexity = 0
    for line in lines:
        complexity += len(_COMPLEXITY_KEYWORDS.findall(line))

    return MetricsData(
        total_lines=total_lines,
        code_lines=code_lines,
        blank_lines=blank_lines,
        comment_lines=comment_lines,
        functions=len(funcs),
        classes=len(classes),
        avg_function_length=round(avg_func_len, 1),
        max_function_length=max_func_len,
        max_nesting_depth=max_depth,
        complexity_estimate=complexity,
    )


def _rename_symbol_impl(
    old_name: str,
    new_name: str,
    root: Path,
    scope: str | None = None,
    languages: list[str] | None = None,
    dry_run: bool = True,
) -> RenameResult:
    """Rename a symbol across the codebase."""
    # Validate
    if old_name == new_name:
        return RenameResult(
            success=False,
            old_name=old_name,
            new_name=new_name,
            message="old_name and new_name are identical",
        )
    if not re.match(r"^\w+$", new_name):
        return RenameResult(
            success=False,
            old_name=old_name,
            new_name=new_name,
            message="new_name must be a valid identifier (letters, "
            "digits, underscores)",
        )

    word_re = re.compile(rf"\b{re.escape(old_name)}\b")
    path_filters = [scope] if scope else None
    files = _walk_source_files(
        root, languages=languages, paths=path_filters,
    )

    changes: list[RenameChange] = []
    total_replacements = 0

    for fpath, rel, _lang in files:
        try:
            content = fpath.read_text(
                encoding="utf-8", errors="replace",
            )
        except OSError:
            continue

        count = len(word_re.findall(content))
        if count == 0:
            continue

        if not dry_run:
            new_content = word_re.sub(new_name, content)
            fpath.write_text(new_content, encoding="utf-8")

        changes.append(RenameChange(
            file_path=rel, occurrences=count,
        ))
        total_replacements += count

    return RenameResult(
        success=True,
        old_name=old_name,
        new_name=new_name,
        files_changed=len(changes),
        total_replacements=total_replacements,
        changes=changes,
        dry_run=dry_run,
    )


# === MCP tool registration ===


def register_code_intelligence_tools(mcp: FastMCP) -> None:
    """Register all code intelligence tools on the MCP server."""

    @mcp.tool(
        name="list_symbols",
        description=(
            "List all functions, classes, methods, variables, and other"
            " symbols defined in a file or directory."
            " Use this to understand the structure of a file before"
            " reading it, to find function signatures, or to get an"
            " overview of a module's API surface."
            " Returns symbol names, types, line numbers, and signatures."
        ),
    )
    async def list_symbols(
        path: str = Field(
            default="",
            description=(
                "Relative path to a file or directory."
                " Empty string = codebase root."
                " Example: 'src/utils/helpers.ts'"
            ),
        ),
        symbol_types: list[str] | None = Field(
            default=None,
            description=(
                "Filter by symbol type(s)."
                " Options: function, method, class, variable,"
                " constant, interface, type, enum, struct,"
                " trait, module, impl."
                " Example: ['function', 'class']"
            ),
        ),
        languages: list[str] | None = Field(
            default=None,
            description=(
                "Filter by language(s)."
                " Example: ['python', 'typescript']"
            ),
        ),
        limit: int = Field(
            default=100,
            ge=1,
            le=MAX_RESULTS,
            description=f"Max symbols to return (1-{MAX_RESULTS})",
        ),
    ) -> ListSymbolsResult:
        """List symbols in a file or directory."""
        try:
            root = _root()
            target = _safe_resolve(path) if path else root
            type_set = (
                {t.lower() for t in symbol_types}
                if symbol_types else None
            )

            all_symbols: list[SymbolEntry] = []

            if target.is_file():
                if _is_binary(target):
                    return ListSymbolsResult(
                        success=False, path=path,
                        message="Binary file, cannot parse",
                    )
                lang = _detect_lang(target)
                content = target.read_text(
                    encoding="utf-8", errors="replace",
                )
                symbols = _extract_symbols(content, lang)
                if type_set:
                    symbols = [
                        s for s in symbols
                        if s.symbol_type in type_set
                    ]
                return ListSymbolsResult(
                    success=True,
                    path=path,
                    symbols=symbols[:limit],
                    total_symbols=len(symbols),
                    language=lang,
                )
            elif target.is_dir():
                files = _walk_source_files(
                    target, languages=languages,
                )
                for fpath, rel, lang in files:
                    if len(all_symbols) >= limit:
                        break
                    try:
                        content = fpath.read_text(
                            encoding="utf-8", errors="replace",
                        )
                    except OSError:
                        continue
                    symbols = _extract_symbols(content, lang)
                    if type_set:
                        symbols = [
                            s for s in symbols
                            if s.symbol_type in type_set
                        ]
                    # Prefix signature with file path for dir listing
                    for s in symbols:
                        s.signature = f"{rel}:{s.line}  {s.signature}"
                    all_symbols.extend(symbols)

                return ListSymbolsResult(
                    success=True,
                    path=path or ".",
                    symbols=all_symbols[:limit],
                    total_symbols=len(all_symbols),
                )
            else:
                return ListSymbolsResult(
                    success=False, path=path,
                    message=f"Path not found: {path}",
                )
        except ValueError as ve:
            return ListSymbolsResult(
                success=False, path=path, message=str(ve),
            )
        except Exception as e:
            return ListSymbolsResult(
                success=False, path=path,
                message=f"list_symbols failed: {e!s}",
            )

    @mcp.tool(
        name="find_definition",
        description=(
            "Find where a symbol (function, class, variable, etc.) is"
            " defined across the entire codebase."
            " Use this as 'go to definition' -- much faster and more"
            " precise than grep for locating declarations."
            " Works across Python, JS/TS, Rust, Go, Java, C/C++, and"
            " more. Returns file path, line number, and signature."
        ),
    )
    async def find_definition(
        symbol_name: str = Field(
            description=(
                "Name of the symbol to find."
                " Examples: 'authenticate', 'UserModel',"
                " 'parse_config'"
            ),
        ),
        symbol_type: str | None = Field(
            default=None,
            description=(
                "Filter by type: function, class, method, variable,"
                " constant, interface, struct, enum, trait, module"
            ),
        ),
        languages: list[str] | None = Field(
            default=None,
            description="Filter by language(s)",
        ),
        paths: list[str] | None = Field(
            default=None,
            description=(
                "Filter by path pattern(s) using GLOB."
                " Example: ['src/*', 'lib/**']"
            ),
        ),
        limit: int = Field(
            default=20,
            ge=1,
            le=MAX_RESULTS,
            description="Max definitions to return",
        ),
    ) -> FindDefinitionResult:
        """Find symbol definitions."""
        try:
            defs = await asyncio.to_thread(
                _find_definitions_impl,
                symbol_name, _root(),
                symbol_type=symbol_type,
                languages=languages,
                paths=paths, limit=limit,
            )
            return FindDefinitionResult(
                success=True,
                definitions=defs,
                total_found=len(defs),
            )
        except Exception as e:
            return FindDefinitionResult(
                success=False,
                message=f"find_definition failed: {e!s}",
            )

    @mcp.tool(
        name="find_references",
        description=(
            "Find all usages of a symbol across the codebase."
            " Shows where a function is called, a class is"
            " instantiated, a variable is read, etc."
            " Use this before refactoring to understand impact."
            " Classifies each reference as import, call, assignment,"
            " type_annotation, definition, or other."
        ),
    )
    async def find_references(
        symbol_name: str = Field(
            description="Name of the symbol to find references for",
        ),
        include_definitions: bool = Field(
            default=False,
            description="Include definition sites in results",
        ),
        languages: list[str] | None = Field(
            default=None,
            description="Filter by language(s)",
        ),
        paths: list[str] | None = Field(
            default=None,
            description="Filter by path pattern(s) using GLOB",
        ),
        context_lines: int = Field(
            default=0, ge=0, le=10,
            description="Context lines before/after each match",
        ),
        limit: int = Field(
            default=50, ge=1, le=MAX_RESULTS,
            description="Max references to return",
        ),
    ) -> FindReferencesResult:
        """Find all references to a symbol."""
        try:
            refs, total, searched, trunc = await asyncio.to_thread(
                _find_references_impl,
                symbol_name, _root(),
                languages=languages, paths=paths,
                context_lines=context_lines, limit=limit,
            )
            if not include_definitions:
                refs = [
                    r for r in refs
                    if r.usage_type != "definition"
                ]
                total = len(refs)

            return FindReferencesResult(
                success=True,
                references=refs,
                total_found=total,
                files_searched=searched,
                truncated=trunc,
            )
        except Exception as e:
            return FindReferencesResult(
                success=False,
                message=f"find_references failed: {e!s}",
            )

    @mcp.tool(
        name="code_metrics",
        description=(
            "Compute code quality metrics for a file."
            " Returns line counts (total, code, blank, comment),"
            " function/class counts, average and max function length,"
            " nesting depth, and cyclomatic complexity estimate."
            " Use to identify files needing refactoring."
        ),
    )
    async def code_metrics(
        path: str = Field(
            description=(
                "Relative path to a source file."
                " Example: 'src/server.py'"
            ),
        ),
    ) -> CodeMetricsResult:
        """Compute code metrics for a file."""
        try:
            resolved = _safe_resolve(path)
            if not resolved.is_file():
                return CodeMetricsResult(
                    success=False, path=path,
                    message=f"File not found: {path}",
                )
            if _is_binary(resolved):
                return CodeMetricsResult(
                    success=False, path=path,
                    message="Binary file, cannot analyze",
                )
            lang = _detect_lang(resolved)
            content = resolved.read_text(
                encoding="utf-8", errors="replace",
            )
            metrics = _compute_metrics(content, lang)
            return CodeMetricsResult(
                success=True, path=path,
                metrics=metrics, language=lang,
            )
        except ValueError as ve:
            return CodeMetricsResult(
                success=False, path=path, message=str(ve),
            )
        except Exception as e:
            return CodeMetricsResult(
                success=False, path=path,
                message=f"code_metrics failed: {e!s}",
            )

    @mcp.tool(
        name="rename_symbol",
        description=(
            "Rename a symbol across the entire codebase using"
            " word-boundary-aware replacement."
            " Much safer than find-and-replace because it won't"
            " rename 'get' inside 'get_user'."
            " Defaults to dry_run=true so you can preview changes"
            " before applying. Set dry_run=false to apply."
        ),
    )
    async def rename_symbol(
        old_name: str = Field(
            description="Current symbol name to rename",
        ),
        new_name: str = Field(
            description="New name for the symbol",
        ),
        scope: str | None = Field(
            default=None,
            description=(
                "Limit rename to files matching this GLOB pattern."
                " Example: 'src/**/*.py'"
            ),
        ),
        languages: list[str] | None = Field(
            default=None,
            description="Filter by language(s)",
        ),
        dry_run: bool = Field(
            default=True,
            description=(
                "Preview changes without applying."
                " Set to false to actually rename."
            ),
        ),
    ) -> RenameResult:
        """Rename a symbol across the codebase."""
        try:
            return await asyncio.to_thread(
                _rename_symbol_impl,
                old_name, new_name, _root(),
                scope=scope, languages=languages,
                dry_run=dry_run,
            )
        except Exception as e:
            return RenameResult(
                success=False,
                old_name=old_name, new_name=new_name,
                message=f"rename_symbol failed: {e!s}",
            )
