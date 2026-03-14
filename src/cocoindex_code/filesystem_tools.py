"""Fast filesystem tools for the cocoindex-code MCP server.

Provides find_files, read_file, write_file, edit_file, grep_code, and directory_tree tools
that operate directly on the filesystem without vector search overhead.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .config import config

EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "node_modules",
        ".cocoindex_code",
        ".next",
        ".nuxt",
        ".venv",
        "venv",
        "env",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)

EXCLUDED_DIR_PATTERNS: list[str] = [
    "target",
    "build",
    "dist",
    "vendor",
]

MAX_READ_BYTES = 1_048_576
MAX_RESULTS = 200
MAX_TREE_DEPTH = 6

_EXT_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hxx": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".md": "markdown",
    ".mdx": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".txt": "text",
    ".rst": "text",
}


# === Pydantic models ===


class FileEntry(BaseModel):
    """A file found by find_files."""

    path: str = Field(description="Relative path from codebase root")
    size: int = Field(description="File size in bytes")
    language: str = Field(default="", description="Detected language (by extension)")


class FindFilesResult(BaseModel):
    """Result from find_files tool."""

    success: bool
    files: list[FileEntry] = Field(default_factory=list)
    total_found: int = 0
    truncated: bool = False
    message: str | None = None


class ReadFileResult(BaseModel):
    """Result from read_file tool."""

    success: bool
    path: str = ""
    content: str = ""
    start_line: int = 1
    end_line: int = 0
    total_lines: int = 0
    language: str = ""
    message: str | None = None


MAX_WRITE_BYTES = 1_048_576


class WriteFileResult(BaseModel):
    """Result from write_file tool."""

    success: bool
    path: str = ""
    bytes_written: int = 0
    created: bool = False
    message: str | None = None


class EditFileResult(BaseModel):
    """Result from edit_file tool."""

    success: bool
    path: str = ""
    replacements: int = 0
    message: str | None = None


class GrepMatch(BaseModel):
    """A single grep match."""

    path: str = Field(description="Relative file path")
    line_number: int = Field(description="1-indexed line number")
    line: str = Field(description="Matched line content")
    context_before: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)


class GrepResult(BaseModel):
    """Result from grep_code tool."""

    success: bool
    matches: list[GrepMatch] = Field(default_factory=list)
    total_matches: int = 0
    files_searched: int = 0
    truncated: bool = False
    message: str | None = None


class TreeEntry(BaseModel):
    """A node in the directory tree."""

    path: str
    type: str = Field(description="'file' or 'dir'")
    size: int = Field(default=0, description="File size in bytes (0 for dirs)")
    children: int = Field(default=0, description="Number of direct children (dirs only)")


class DirectoryTreeResult(BaseModel):
    """Result from directory_tree tool."""

    success: bool
    root: str = ""
    entries: list[TreeEntry] = Field(default_factory=list)
    message: str | None = None


# === Internal helpers ===


def _root() -> Path:
    """Return resolved codebase root."""
    return config.codebase_root_path.resolve()


def _safe_resolve(path_str: str) -> Path:
    """Resolve a user-supplied path, ensuring it stays within the codebase root."""
    root = _root()
    resolved = (root / path_str).resolve()
    if not (resolved == root or str(resolved).startswith(str(root) + os.sep)):
        msg = f"Path '{path_str}' escapes the codebase root"
        raise ValueError(msg)
    return resolved


def _is_excluded_dir(name: str) -> bool:
    """Check if a directory name should be excluded."""
    if name.startswith("."):
        return True
    if name in EXCLUDED_DIRS:
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDED_DIR_PATTERNS)


def _is_binary(path: Path, sample_size: int = 8192) -> bool:
    """Heuristic binary detection by looking for null bytes."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample_size)
        return b"\x00" in chunk
    except OSError:
        return True


def _relative(path: Path) -> str:
    """Return path relative to codebase root."""
    try:
        return str(path.relative_to(_root()))
    except ValueError:
        return str(path)


def _detect_lang(path: Path) -> str:
    """Detect programming language by file extension."""
    return _EXT_LANG.get(path.suffix.lower(), "")


# === Core implementations ===


def _walk_files(
    root: Path,
    pattern: str | None = None,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    limit: int = MAX_RESULTS,
) -> tuple[list[FileEntry], int, bool]:
    """Walk the codebase and collect matching files."""
    lang_set = {lang.lower() for lang in languages} if languages else None
    results: list[FileEntry] = []
    total = 0
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _is_excluded_dir(d))

        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            rel = _relative(fpath)

            if (
                pattern
                and not fnmatch.fnmatch(rel, pattern)
                and not fnmatch.fnmatch(fname, pattern)
            ):
                continue

            if paths and not any(fnmatch.fnmatch(rel, p) for p in paths):
                continue

            lang = _detect_lang(fpath)

            if lang_set and lang.lower() not in lang_set:
                continue

            total += 1
            if len(results) < limit:
                try:
                    size = fpath.stat().st_size
                except OSError:
                    size = 0
                results.append(FileEntry(path=rel, size=size, language=lang))
            else:
                truncated = True

    return results, total, truncated


def _read_file(
    path: Path,
    start_line: int | None = None,
    end_line: int | None = None,
) -> tuple[str, int, int, int]:
    """Read a file, optionally slicing by line range."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    total = len(lines)
    s = max(1, start_line or 1)
    e = min(total, end_line or total)

    selected = lines[s - 1 : e]
    content = "".join(selected)

    if len(content.encode("utf-8", errors="replace")) > MAX_READ_BYTES:
        content = content[:MAX_READ_BYTES] + "\n\n... [truncated at 1 MB] ..."

    return content, s, e, total


def _write_file(path: Path, content: str) -> tuple[int, bool]:
    """Write content to a file, creating parent directories as needed.

    Returns (bytes_written, created) where created indicates a new file.
    """
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_WRITE_BYTES:
        msg = f"Content exceeds maximum write size ({MAX_WRITE_BYTES} bytes)"
        raise ValueError(msg)
    created = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return len(content_bytes), created


def _edit_file(
    path: Path,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
) -> int:
    """Perform exact string replacement in a file.

    Returns the number of replacements made.
    Raises ValueError if old_string is not found or is ambiguous.
    """
    content = path.read_text(encoding="utf-8")

    if old_string == new_string:
        msg = "old_string and new_string are identical"
        raise ValueError(msg)

    count = content.count(old_string)
    if count == 0:
        msg = "old_string not found in file"
        raise ValueError(msg)

    if count > 1 and not replace_all:
        msg = (
            f"Found {count} matches for old_string."
            " Provide more context to identify a unique match, or set replace_all=true."
        )
        raise ValueError(msg)

    if replace_all:
        new_content = content.replace(old_string, new_string)
        replacements = count
    else:
        new_content = content.replace(old_string, new_string, 1)
        replacements = 1

    new_bytes = new_content.encode("utf-8")
    if len(new_bytes) > MAX_WRITE_BYTES:
        msg = f"Resulting file exceeds maximum size ({MAX_WRITE_BYTES} bytes)"
        raise ValueError(msg)

    path.write_text(new_content, encoding="utf-8")
    return replacements


def _grep_files(
    root: Path,
    pattern_str: str,
    include: str | None = None,
    paths: list[str] | None = None,
    context_lines: int = 0,
    limit: int = MAX_RESULTS,
    *,
    case_sensitive: bool = True,
) -> tuple[list[GrepMatch], int, int, bool]:
    """Grep across files in the codebase."""
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(pattern_str, flags)
    except re.error as e:
        msg = f"Invalid regex: {e}"
        raise ValueError(msg) from e

    matches: list[GrepMatch] = []
    total_matches = 0
    files_searched = 0
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _is_excluded_dir(d))

        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            rel = _relative(fpath)

            if (
                include
                and not fnmatch.fnmatch(fname, include)
                and not fnmatch.fnmatch(rel, include)
            ):
                continue

            if paths and not any(fnmatch.fnmatch(rel, p) for p in paths):
                continue

            try:
                if fpath.stat().st_size > MAX_READ_BYTES:
                    continue
            except OSError:
                continue
            if _is_binary(fpath):
                continue

            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    file_lines = f.readlines()
            except OSError:
                continue

            files_searched += 1

            for i, line in enumerate(file_lines):
                if regex.search(line):
                    total_matches += 1
                    if len(matches) < limit:
                        ctx_before = [
                            file_lines[j].rstrip("\n\r")
                            for j in range(max(0, i - context_lines), i)
                        ]
                        ctx_after = [
                            file_lines[j].rstrip("\n\r")
                            for j in range(i + 1, min(len(file_lines), i + 1 + context_lines))
                        ]
                        matches.append(
                            GrepMatch(
                                path=rel,
                                line_number=i + 1,
                                line=line.rstrip("\n\r"),
                                context_before=ctx_before,
                                context_after=ctx_after,
                            )
                        )
                    elif not truncated:
                        truncated = True

    return matches, total_matches, files_searched, truncated


def _directory_tree(
    root: Path,
    rel_path: str = "",
    max_depth: int = MAX_TREE_DEPTH,
) -> list[TreeEntry]:
    """Build a directory tree listing."""
    start = _safe_resolve(rel_path) if rel_path else root
    entries: list[TreeEntry] = []

    def _walk(dirpath: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(dirpath.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return

        for child in children:
            rel = _relative(child)
            if child.is_dir():
                if _is_excluded_dir(child.name):
                    continue
                sub_children = (
                    sum(1 for c in child.iterdir() if not (c.is_dir() and _is_excluded_dir(c.name)))
                    if depth < max_depth
                    else 0
                )
                entries.append(TreeEntry(path=rel, type="dir", children=sub_children))
                _walk(child, depth + 1)
            else:
                try:
                    size = child.stat().st_size
                except OSError:
                    size = 0
                entries.append(TreeEntry(path=rel, type="file", size=size))

    _walk(start, 0)
    return entries


# === MCP tool registration ===


def register_filesystem_tools(mcp: FastMCP) -> None:
    """Register all filesystem tools on the given MCP server."""

    @mcp.tool(
        name="find_files",
        description=(
            "Fast file discovery by glob pattern, language, or path."
            " Use this to quickly list files matching a pattern"
            " (e.g., '*.py', 'src/**/*.ts', 'README*')."
            " Much faster than semantic search for finding files by name."
            " Returns file paths, sizes, and detected languages."
        ),
    )
    async def find_files(
        pattern: str | None = Field(
            default=None,
            description=(
                "Glob pattern to match file names or paths."
                " Examples: '*.py', 'src/**/*.ts', 'README*', '*.test.*'"
            ),
        ),
        languages: list[str] | None = Field(
            default=None,
            description="Filter by language(s). Example: ['python', 'typescript']",
        ),
        paths: list[str] | None = Field(
            default=None,
            description=(
                "Filter by path pattern(s) using GLOB wildcards. Example: ['src/*', 'lib/**']"
            ),
        ),
        limit: int = Field(
            default=50,
            ge=1,
            le=MAX_RESULTS,
            description=f"Maximum number of results (1-{MAX_RESULTS})",
        ),
    ) -> FindFilesResult:
        """Find files in the codebase by pattern."""
        try:
            files, total, truncated = await asyncio.to_thread(
                _walk_files,
                _root(),
                pattern=pattern,
                languages=languages,
                paths=paths,
                limit=limit,
            )
            return FindFilesResult(
                success=True,
                files=files,
                total_found=total,
                truncated=truncated,
            )
        except Exception as e:
            return FindFilesResult(success=False, message=f"Find failed: {e!s}")

    @mcp.tool(
        name="read_file",
        description=(
            "Read file contents by path, with optional line range."
            " Use this when you know the exact file path and want to read"
            " its contents quickly -- much faster than semantic search."
            " Supports reading specific line ranges for large files."
            " Returns content with language detection and total line count."
        ),
    )
    async def read_file(
        path: str = Field(
            description="Relative path from codebase root. Example: 'src/utils/helpers.ts'",
        ),
        start_line: int | None = Field(
            default=None,
            ge=1,
            description="Start reading from this line (1-indexed). Default: first line.",
        ),
        end_line: int | None = Field(
            default=None,
            ge=1,
            description="Stop reading at this line (inclusive). Default: last line.",
        ),
    ) -> ReadFileResult:
        """Read a file from the codebase."""
        try:
            resolved = _safe_resolve(path)
            if not resolved.is_file():
                return ReadFileResult(
                    success=False,
                    path=path,
                    message=f"File not found: {path}",
                )
            if _is_binary(resolved):
                return ReadFileResult(
                    success=False,
                    path=path,
                    message=f"Binary file, cannot display: {path}",
                )

            content, s, e, total = _read_file(resolved, start_line, end_line)
            return ReadFileResult(
                success=True,
                path=path,
                content=content,
                start_line=s,
                end_line=e,
                total_lines=total,
                language=_detect_lang(resolved),
            )
        except ValueError as ve:
            return ReadFileResult(success=False, path=path, message=str(ve))
        except Exception as e:
            return ReadFileResult(success=False, path=path, message=f"Read failed: {e!s}")

    @mcp.tool(
        name="write_file",
        description=(
            "Write content to a file in the codebase."
            " Creates the file if it does not exist, overwrites if it does."
            " Automatically creates parent directories as needed."
            " Use this to create new files or update existing ones."
            " Returns bytes written and whether the file was newly created."
        ),
    )
    async def write_file(
        path: str = Field(
            description="Relative path from codebase root. Example: 'src/utils/helpers.ts'",
        ),
        content: str = Field(
            description="The text content to write to the file.",
        ),
    ) -> WriteFileResult:
        """Write content to a file in the codebase."""
        try:
            resolved = _safe_resolve(path)
            bytes_written, created = _write_file(resolved, content)
            return WriteFileResult(
                success=True,
                path=path,
                bytes_written=bytes_written,
                created=created,
            )
        except ValueError as ve:
            return WriteFileResult(success=False, path=path, message=str(ve))
        except Exception as e:
            return WriteFileResult(success=False, path=path, message=f"Write failed: {e!s}")

    @mcp.tool(
        name="edit_file",
        description=(
            "Perform exact string replacements in a file."
            " Finds old_string in the file and replaces it with new_string."
            " By default requires old_string to match exactly once (for safety)."
            " Set replace_all=true to replace every occurrence."
            " Use this for surgical edits instead of rewriting entire files."
        ),
    )
    async def edit_file(
        path: str = Field(
            description="Relative path from codebase root. Example: 'src/utils/helpers.ts'",
        ),
        old_string: str = Field(
            description="The exact text to find and replace. Must match file content exactly.",
        ),
        new_string: str = Field(
            description="The replacement text. Must differ from old_string.",
        ),
        replace_all: bool = Field(
            default=False,
            description=(
                "Replace all occurrences. Default false requires exactly one match for safety."
            ),
        ),
    ) -> EditFileResult:
        """Perform exact string replacement in a file."""
        try:
            resolved = _safe_resolve(path)
            if not resolved.is_file():
                return EditFileResult(
                    success=False,
                    path=path,
                    message=f"File not found: {path}",
                )
            if _is_binary(resolved):
                return EditFileResult(
                    success=False,
                    path=path,
                    message=f"Binary file, cannot edit: {path}",
                )
            replacements = _edit_file(resolved, old_string, new_string, replace_all=replace_all)
            return EditFileResult(
                success=True,
                path=path,
                replacements=replacements,
            )
        except ValueError as ve:
            return EditFileResult(success=False, path=path, message=str(ve))
        except Exception as e:
            return EditFileResult(success=False, path=path, message=f"Edit failed: {e!s}")

    @mcp.tool(
        name="grep_code",
        description=(
            "Fast regex text search across codebase files."
            " Use this instead of semantic search when you need exact"
            " text or pattern matching (e.g., function names, imports,"
            " TODO comments, error strings)."
            " Returns matching lines with file paths, line numbers,"
            " and optional context lines."
        ),
    )
    async def grep_code(
        pattern: str = Field(
            description=(
                "Regular expression pattern to search for."
                " Examples: 'def authenticate', 'import.*redis',"
                " 'TODO|FIXME|HACK', 'class\\s+User'"
            ),
        ),
        include: str | None = Field(
            default=None,
            description="File pattern to include. Examples: '*.py', '*.{ts,tsx}', 'Makefile'",
        ),
        paths: list[str] | None = Field(
            default=None,
            description="Filter by path pattern(s). Example: ['src/*', 'lib/**']",
        ),
        context_lines: int = Field(
            default=0,
            ge=0,
            le=10,
            description="Number of context lines before and after each match (0-10)",
        ),
        case_sensitive: bool = Field(
            default=True,
            description="Whether the search is case-sensitive",
        ),
        limit: int = Field(
            default=50,
            ge=1,
            le=MAX_RESULTS,
            description=f"Maximum number of matches (1-{MAX_RESULTS})",
        ),
    ) -> GrepResult:
        """Search file contents by regex pattern."""
        try:
            matches, total, searched, truncated = _grep_files(
                _root(),
                pattern,
                include=include,
                paths=paths,
                context_lines=context_lines,
                limit=limit,
                case_sensitive=case_sensitive,
            )
            return GrepResult(
                success=True,
                matches=matches,
                total_matches=total,
                files_searched=searched,
                truncated=truncated,
            )
        except ValueError as ve:
            return GrepResult(success=False, message=str(ve))
        except Exception as e:
            return GrepResult(success=False, message=f"Grep failed: {e!s}")

    @mcp.tool(
        name="directory_tree",
        description=(
            "List the directory structure of the codebase."
            " Use this to understand project layout, find directories,"
            " or get an overview before diving into specific files."
            " Excludes hidden dirs, node_modules, build artifacts, etc."
            " Returns a flat list of entries with types and sizes."
        ),
    )
    async def directory_tree(
        path: str = Field(
            default="",
            description=(
                "Relative path to start from (empty = codebase root). Example: 'src/components'"
            ),
        ),
        max_depth: int = Field(
            default=MAX_TREE_DEPTH,
            ge=1,
            le=10,
            description=f"Maximum directory depth to recurse (1-10, default {MAX_TREE_DEPTH})",
        ),
    ) -> DirectoryTreeResult:
        """List the directory tree of the codebase."""
        try:
            start = _safe_resolve(path) if path else _root()
            if not start.is_dir():
                return DirectoryTreeResult(
                    success=False,
                    message=f"Directory not found: {path}",
                )
            entries = _directory_tree(_root(), rel_path=path, max_depth=max_depth)
            return DirectoryTreeResult(
                success=True,
                root=_relative(start) if path else ".",
                entries=entries,
            )
        except ValueError as ve:
            return DirectoryTreeResult(success=False, message=str(ve))
        except Exception as e:
            return DirectoryTreeResult(success=False, message=f"Tree failed: {e!s}")


# === Large write support ===

# In-memory buffers for chunked writes, keyed by session_id
_large_write_buffers: dict[str, dict] = {}

MAX_LARGE_WRITE_BYTES = 5_242_880  # 5 MB total limit per session
MAX_LARGE_WRITE_SESSIONS = 50  # Maximum concurrent sessions


class LargeWriteResult(BaseModel):
    """Result from large_write tool."""

    success: bool
    session_id: str = ""
    path: str = ""
    action: str = ""
    chunks_received: int = 0
    total_bytes: int = 0
    bytes_written: int = 0
    created: bool = False
    message: str | None = None


def _large_write_start(
    session_id: str, path: str,
) -> None:
    """Start a new large write session.

    Evicts the oldest session if MAX_LARGE_WRITE_SESSIONS is reached.
    """
    # Evict oldest session if at capacity
    if (
        session_id not in _large_write_buffers
        and len(_large_write_buffers) >= MAX_LARGE_WRITE_SESSIONS
    ):
        oldest_key = min(
            _large_write_buffers,
            key=lambda k: _large_write_buffers[k].get("created_at", 0),
        )
        _large_write_buffers.pop(oldest_key, None)

    _large_write_buffers[session_id] = {
        "path": path,
        "chunks": [],
        "total_bytes": 0,
        "created_at": time.monotonic(),
    }


def _large_write_append(
    session_id: str, content: str,
) -> int:
    """Append content to a large write session. Returns new total bytes."""
    buf = _large_write_buffers[session_id]
    chunk_bytes = len(content.encode("utf-8"))
    new_total = buf["total_bytes"] + chunk_bytes
    if new_total > MAX_LARGE_WRITE_BYTES:
        msg = (
            f"Content exceeds max size ({MAX_LARGE_WRITE_BYTES} bytes)."
            f" Current: {buf['total_bytes']}, chunk: {chunk_bytes}"
        )
        raise ValueError(msg)
    buf["chunks"].append(content)
    buf["total_bytes"] = new_total
    return new_total


def _large_write_finalize(
    session_id: str,
) -> tuple[str, int, bool]:
    """Finalize and write the buffered content.

    Returns (path, bytes_written, created).
    """
    buf = _large_write_buffers.pop(session_id)
    path_str = buf["path"]
    full_content = "".join(buf["chunks"])
    resolved = _safe_resolve(path_str)
    created = not resolved.exists()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(full_content, encoding="utf-8")
    return path_str, buf["total_bytes"], created


def register_large_write_tool(mcp: FastMCP) -> None:
    """Register the large_write tool on the MCP server."""

    @mcp.tool(
        name="large_write",
        description=(
            "Write large files in chunks when content is too big for"
            " a single write_file call."
            " Use action='start' to begin a session with a file path,"
            " then 'append' to add content in pieces,"
            " then 'finalize' to write the assembled file to disk."
            " Supports up to 5 MB total. Each session is identified"
            " by a session_id you provide."
            "\n\nWorkflow:"
            "\n1. large_write(action='start', session_id='s1',"
            "    path='src/big_file.py')"
            "\n2. large_write(action='append', session_id='s1',"
            "    content='first chunk...')"
            "\n3. large_write(action='append', session_id='s1',"
            "    content='second chunk...')"
            "\n4. large_write(action='finalize', session_id='s1')"
        ),
    )
    async def large_write(
        action: str = Field(
            description=(
                "Action: 'start' to begin, 'append' to add content,"
                " 'finalize' to write to disk, 'abort' to cancel."
            ),
        ),
        session_id: str = Field(
            description="Unique session identifier for this write.",
        ),
        path: str = Field(
            default="",
            description=(
                "Relative file path. Required for 'start' action."
                " Example: 'src/utils/big_module.py'"
            ),
        ),
        content: str = Field(
            default="",
            description=(
                "Content chunk to append. Used with 'append' action."
            ),
        ),
    ) -> LargeWriteResult:
        """Write large files in chunks."""
        try:
            if action == "start":
                if not path:
                    return LargeWriteResult(
                        success=False, action=action,
                        session_id=session_id,
                        message="path is required for 'start' action",
                    )
                # Validate path early
                _safe_resolve(path)
                _large_write_start(session_id, path)
                return LargeWriteResult(
                    success=True, action=action,
                    session_id=session_id, path=path,
                    chunks_received=0, total_bytes=0,
                )

            if action == "append":
                if session_id not in _large_write_buffers:
                    return LargeWriteResult(
                        success=False, action=action,
                        session_id=session_id,
                        message=f"No active session '{session_id}'."
                        " Call with action='start' first.",
                    )
                if not content:
                    return LargeWriteResult(
                        success=False, action=action,
                        session_id=session_id,
                        message="content is required for 'append'",
                    )
                total = _large_write_append(session_id, content)
                buf = _large_write_buffers[session_id]
                return LargeWriteResult(
                    success=True, action=action,
                    session_id=session_id,
                    path=buf["path"],
                    chunks_received=len(buf["chunks"]),
                    total_bytes=total,
                )

            if action == "finalize":
                if session_id not in _large_write_buffers:
                    return LargeWriteResult(
                        success=False, action=action,
                        session_id=session_id,
                        message=f"No active session '{session_id}'",
                    )
                fpath, written, created = _large_write_finalize(
                    session_id,
                )
                return LargeWriteResult(
                    success=True, action=action,
                    session_id=session_id,
                    path=fpath, bytes_written=written,
                    created=created,
                )

            if action == "abort":
                _large_write_buffers.pop(session_id, None)
                return LargeWriteResult(
                    success=True, action=action,
                    session_id=session_id,
                    message="Session aborted",
                )

            return LargeWriteResult(
                success=False, action=action,
                session_id=session_id,
                message=(
                    f"Invalid action '{action}'."
                    " Must be 'start', 'append',"
                    " 'finalize', or 'abort'."
                ),
            )
        except ValueError as ve:
            return LargeWriteResult(
                success=False, action=action,
                session_id=session_id, message=str(ve),
            )
        except Exception as e:
            return LargeWriteResult(
                success=False, action=action,
                session_id=session_id,
                message=f"large_write failed: {e!s}",
            )
