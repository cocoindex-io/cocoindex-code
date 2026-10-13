"""MCP server for codebase indexing and querying.

Supports two modes:
1. Daemon-backed: ``create_mcp_server(client, project_root)`` — lightweight MCP
   server that delegates to the daemon via per-request client functions.
2. Legacy entry point: ``main()`` — backward-compatible ``cocoindex-code`` CLI that
   auto-creates settings from env vars and delegates to the daemon.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from cocoindex.ops.text import detect_code_language
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ._version import __version__
from .file_walk import build_matcher, iter_included_files
from .protocol import ProjectStatusResponse
from .settings import (
    DaemonSettings,
    load_project_settings,
    load_user_settings,
    user_settings_root_note,
)

_MCP_INSTRUCTIONS = (
    "Code search and codebase understanding tools."
    "\n"
    "Use when you need to find code, understand how something works,"
    " locate implementations, or explore an unfamiliar codebase."
    "\n"
    "Provides semantic search that understands meaning --"
    " unlike grep or text matching,"
    " it finds relevant code even when exact keywords are unknown."
)


# === Pydantic Models for Tool Inputs/Outputs ===


class CodeChunkResult(BaseModel):
    """A single code chunk result."""

    file_path: str = Field(description="Relative path to the file")
    language: str = Field(description="Programming language")
    content: str = Field(description="The code content")
    start_line: int = Field(description="Starting line number (1-indexed)")
    end_line: int = Field(description="Ending line number (1-indexed)")
    score: float = Field(description="Similarity score (0-1, higher is better)")


class IndexFreshnessModel(BaseModel):
    """Whether the source tree still matches the latest successful index."""

    stale: bool = Field(description="True when a refresh is needed or freshness is unknown")
    indexed_at: str | None = Field(
        default=None,
        description="UTC time of the successful index update, or null when unavailable",
    )


class SearchResultModel(BaseModel):
    """Result from search tool."""

    success: bool
    results: list[CodeChunkResult] = Field(default_factory=list)
    total_returned: int = Field(default=0)
    offset: int = Field(default=0)
    freshness: IndexFreshnessModel = Field(default_factory=lambda: IndexFreshnessModel(stale=True))
    message: str | None = None


class IndexStatusModel(BaseModel):
    """Index health, size, and freshness for the current project."""

    indexing: bool
    index_exists: bool
    total_chunks: int
    total_files: int
    languages: dict[str, int] = Field(
        default_factory=dict,
        description="Indexed chunk counts by programming language",
    )
    freshness: IndexFreshnessModel
    message: str | None = None


class StructuralMatchResult(BaseModel):
    """One structural-code match."""

    file_path: str = Field(description="Path relative to the project root")
    language: str | None = Field(description="Detected language, if available")
    content: str = Field(description="The exact source span matching the structural pattern")
    start_line: int = Field(description="Starting line number (1-indexed)")
    end_line: int = Field(description="Ending line number (1-indexed)")


class GrepResultModel(BaseModel):
    """Result from structural grep."""

    success: bool
    matches: list[StructuralMatchResult] = Field(default_factory=list)
    total_matches: int = Field(default=0)
    warnings: list[str] = Field(default_factory=list)
    truncated: bool = False
    message: str | None = None


class DirectorySummary(BaseModel):
    """Indexed-source file count for a top-level directory."""

    path: str
    files: int


class CodebaseOverviewResult(BaseModel):
    """A compact source-tree overview for agent orientation."""

    success: bool
    total_source_files: int = 0
    languages: dict[str, int] = Field(
        default_factory=dict,
        description="Current source file counts by detected language",
    )
    top_level_directories: list[DirectorySummary] = Field(default_factory=list)
    tree: list[str] = Field(default_factory=list)
    truncated: bool = False
    freshness: IndexFreshnessModel = Field(default_factory=lambda: IndexFreshnessModel(stale=True))
    message: str | None = None


def _with_note(message: str | None, note: str | None) -> str | None:
    """Append *note* to a result message; MCP clients hide the server's stderr."""
    if note is None:
        return message
    return f"{message}\nNote: {note}" if message else f"Note: {note}"


def _freshness_from_status(status: ProjectStatusResponse) -> IndexFreshnessModel:
    """Translate daemon timestamp fields into a stable MCP-facing shape."""
    indexed_at_us = getattr(status, "indexed_at_us", None)
    indexed_at = None
    if indexed_at_us is not None:
        indexed_at = datetime.fromtimestamp(indexed_at_us / 1_000_000, UTC).isoformat()
    return IndexFreshnessModel(stale=getattr(status, "stale", True), indexed_at=indexed_at)


def _status_model(status: ProjectStatusResponse, note: str | None = None) -> IndexStatusModel:
    """Build the public MCP status model from the daemon protocol response."""
    return IndexStatusModel(
        indexing=status.indexing,
        index_exists=status.index_exists,
        total_chunks=status.total_chunks,
        total_files=status.total_files,
        languages=status.languages,
        freshness=_freshness_from_status(status),
        message=_with_note(None, note),
    )


def _resolve_project_path(project_root: Path, path: str) -> Path:
    """Resolve an MCP path and reject paths outside the configured project."""
    root = project_root.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError("path must be within the project root")
    if not target.exists():
        raise ValueError(f"path not found: {path}")
    return target


def _language_for_path(path: Path, overrides: dict[str, str]) -> str:
    """Match the indexer's language-detection behavior for MCP-only tools."""
    return overrides.get(path.suffix) or detect_code_language(filename=path.name) or "text"


def _run_structural_grep(
    project_root: Path,
    pattern: str,
    path: str,
    languages: list[str] | None,
    path_glob: str | None,
    limit: int,
) -> tuple[list[StructuralMatchResult], int, list[str], bool]:
    """Run local structural grep and retain only the requested number of matches."""
    from . import grep as _grep

    target = _resolve_project_path(project_root, path)
    settings = load_project_settings(project_root)
    overrides = {f".{entry.ext}": entry.lang for entry in settings.language_overrides}
    grep_run = _grep.Grep(
        _grep.GrepRequest(
            pattern=pattern,
            root=target,
            languages=frozenset(language.lower() for language in languages) if languages else None,
            path_glob=path_glob,
        )
    )
    matches: list[StructuralMatchResult] = []
    warnings: list[str] = []
    total_matches = 0
    lock = threading.Lock()

    def emit(item: _grep.FileMatches | _grep.GrepWarning) -> None:
        nonlocal total_matches
        if isinstance(item, _grep.GrepWarning):
            with lock:
                warnings.append(item.message)
            return
        relative_path = Path(item.path).resolve().relative_to(project_root.resolve()).as_posix()
        language = _language_for_path(Path(relative_path), overrides)
        file_matches: list[StructuralMatchResult] = []
        for match in item.matches:
            if not match.chunks:
                continue
            chunk = match.chunks[0]
            file_matches.append(
                StructuralMatchResult(
                    file_path=relative_path,
                    language=language,
                    content=item.source[chunk.start.char_offset : chunk.end.char_offset],
                    start_line=chunk.start.line,
                    end_line=chunk.end.line,
                )
            )
        with lock:
            total_matches += len(file_matches)
            remaining = limit - len(matches)
            if remaining > 0:
                matches.extend(file_matches[:remaining])

    grep_run.run(emit)
    matches.sort(key=lambda match: (match.file_path, match.start_line, match.end_line))
    return matches, total_matches, sorted(warnings), grep_run.unusable


def _build_codebase_overview(
    project_root: Path, max_depth: int, max_entries: int
) -> tuple[int, dict[str, int], list[DirectorySummary], list[str], bool]:
    """Summarize the currently indexable source tree without reading file content."""
    settings = load_project_settings(project_root)
    matcher = build_matcher(
        project_root,
        settings.include_patterns,
        settings.exclude_patterns,
        settings.max_file_size,
    )
    overrides = {f".{entry.ext}": entry.lang for entry in settings.language_overrides}
    languages: Counter[str] = Counter()
    top_level: Counter[str] = Counter()
    tree_entries: set[str] = set()
    total_files = 0

    for path, relative_path in iter_included_files(project_root, project_root, matcher):
        total_files += 1
        languages[_language_for_path(path, overrides)] += 1
        parts = relative_path.parts
        if len(parts) > 1:
            top_level[parts[0]] += 1
        for depth in range(1, min(len(parts), max_depth) + 1):
            is_file = depth == len(parts)
            entry = "/".join(parts[:depth])
            tree_entries.add(entry if is_file else f"{entry}/")
        if len(parts) > max_depth:
            tree_entries.add(f"{'/'.join(parts[:max_depth])}/…")

    tree = sorted(tree_entries)
    truncated = len(tree) > max_entries
    return (
        total_files,
        dict(sorted(languages.items())),
        [DirectorySummary(path=path, files=count) for path, count in sorted(top_level.items())],
        tree[:max_entries],
        truncated,
    )


# === Daemon-backed MCP server factory ===


def create_mcp_server(project_root: str) -> MCPServer:
    """Create a lightweight MCP server that delegates to the daemon."""
    mcp = MCPServer("cocoindex-code", instructions=_MCP_INSTRUCTIONS, version=__version__)

    @mcp.tool(
        name="search",
        description=(
            "Semantic code search across the entire codebase"
            " -- finds code by meaning, not just text matching."
            " Use this instead of grep/glob when you need to find implementations,"
            " understand how features work,"
            " or locate related code without knowing exact names or keywords."
            " Accepts natural language queries"
            " (e.g., 'authentication logic', 'database connection handling')"
            " or code snippets."
            " Returns matching code chunks with file paths,"
            " line numbers, and relevance scores."
            " Start with a small limit (e.g., 5);"
            " if most results look relevant, use offset to paginate for more."
        ),
    )
    async def search(
        query: str = Field(
            description=(
                "Natural language query or code snippet to search for."
                " Examples: 'error handling middleware',"
                " 'how are users authenticated',"
                " 'database connection pool',"
                " or paste a code snippet to find similar code."
            )
        ),
        limit: int = Field(
            default=5,
            ge=1,
            le=100,
            description="Maximum number of results to return (1-100)",
        ),
        offset: int = Field(
            default=0,
            ge=0,
            description="Number of results to skip for pagination",
        ),
        refresh_index: bool = Field(
            default=True,
            description=(
                "Whether to incrementally update the index before searching."
                " Set to False for faster consecutive queries"
                " when the codebase hasn't changed."
            ),
        ),
        languages: list[str] | None = Field(
            default=None,
            description="Filter by programming language(s). Example: ['python', 'typescript']",
        ),
        paths: list[str] | None = Field(
            default=None,
            description=(
                "Filter by file path pattern(s) using GLOB wildcards (* and ?)."
                " Example: ['src/utils/*', '*.py']"
            ),
        ),
    ) -> SearchResultModel:
        """Query the codebase index via the daemon."""
        from . import client as _client

        loop = asyncio.get_event_loop()
        note: str | None = None
        try:
            note = user_settings_root_note(Path(project_root), Path.cwd())
            if refresh_index:
                index_response = await loop.run_in_executor(
                    None, lambda: _client.index(project_root)
                )
                if not index_response.success:
                    return SearchResultModel(
                        success=False,
                        message=_with_note(
                            f"Index refresh failed: {index_response.message or 'unknown error'}",
                            note,
                        ),
                    )
            resp = await loop.run_in_executor(
                None,
                lambda: _client.search(
                    project_root=project_root,
                    query=query,
                    languages=languages,
                    paths=paths,
                    limit=limit,
                    offset=offset,
                ),
            )
            # A separate status request adds a source-tree freshness check without
            # changing the search protocol or forcing a refresh on every query.
            try:
                status = await loop.run_in_executor(
                    None, lambda: _client.project_status(project_root)
                )
                freshness = _freshness_from_status(status)
            except Exception:
                # Never discard useful search hits merely because observability
                # failed. Conservatively tell the agent it must refresh to trust
                # the result.
                freshness = IndexFreshnessModel(stale=True)
            return SearchResultModel(
                success=resp.success,
                results=[
                    CodeChunkResult(
                        file_path=r.file_path,
                        language=r.language,
                        content=r.content,
                        start_line=r.start_line,
                        end_line=r.end_line,
                        score=r.score,
                    )
                    for r in resp.results
                ],
                total_returned=resp.total_returned,
                offset=resp.offset,
                freshness=freshness,
                message=_with_note(resp.message, note),
            )
        except Exception as e:
            return SearchResultModel(
                success=False, message=_with_note(f"Query failed: {e!s}", note)
            )

    @mcp.tool(
        name="grep",
        description=(
            "Structural code search by syntax example. Use this when you know the code shape "
            "or an exact API call/definition to find. It does not require an index or embeddings. "
            "Use \\ metavariables, for example `def \\NAME(\\(ARGS*\\)):` or `foo(\\(ARGS*\\))`."
        ),
    )
    async def grep(
        pattern: str = Field(description="Structural pattern to match, using \\ metavariables."),
        path: str = Field(
            default=".",
            description=(
                "Project-relative file or directory to search. It must stay inside the project."
            ),
        ),
        languages: list[str] | None = Field(
            default=None,
            description="Optional programming-language filter, for example ['python', 'rust'].",
        ),
        path_glob: str | None = Field(
            default=None,
            description="Optional project-relative glob filter, for example 'src/**'.",
        ),
        limit: int = Field(default=100, ge=1, le=1000, description="Maximum matches to return."),
    ) -> GrepResultModel:
        """Run local, AST-aware structural search without using the vector index."""
        loop = asyncio.get_event_loop()
        note = user_settings_root_note(Path(project_root), Path.cwd())
        try:
            matches, total_matches, warnings, unusable = await loop.run_in_executor(
                None,
                lambda: _run_structural_grep(
                    Path(project_root), pattern, path, languages, path_glob, limit
                ),
            )
            message = None
            if unusable:
                message = "Pattern did not compile for any of the matching languages."
            return GrepResultModel(
                success=not unusable,
                matches=matches,
                total_matches=total_matches,
                warnings=warnings,
                truncated=total_matches > len(matches),
                message=_with_note(message, note),
            )
        except Exception as e:
            return GrepResultModel(
                success=False, message=_with_note(f"Structural search failed: {e!s}", note)
            )

    @mcp.tool(
        name="status",
        description=(
            "Show index size, language breakdown, and freshness. Check this before trusting "
            "a search run with refresh_index=false."
        ),
    )
    async def status() -> IndexStatusModel:
        """Get index status and whether the source tree has changed since indexing."""
        from . import client as _client

        loop = asyncio.get_event_loop()
        note = user_settings_root_note(Path(project_root), Path.cwd())
        try:
            daemon_status = await loop.run_in_executor(
                None, lambda: _client.project_status(project_root)
            )
            return _status_model(daemon_status, note)
        except Exception as e:
            return IndexStatusModel(
                indexing=False,
                index_exists=False,
                total_chunks=0,
                total_files=0,
                freshness=IndexFreshnessModel(stale=True),
                message=_with_note(f"Status failed: {e!s}", note),
            )

    @mcp.tool(
        name="codebase_overview",
        description=(
            "Return a compact source-tree and language overview for orienting in an unfamiliar "
            "codebase. It lists no file contents and does not require a completed index."
        ),
    )
    async def codebase_overview(
        max_depth: int = Field(
            default=2,
            ge=1,
            le=8,
            description="Maximum directory depth represented in the tree.",
        ),
        max_entries: int = Field(
            default=80,
            ge=1,
            le=500,
            description="Maximum tree entries to return.",
        ),
    ) -> CodebaseOverviewResult:
        """Summarize indexable files and languages without spending tokens on source text."""
        from . import client as _client

        loop = asyncio.get_event_loop()
        note = user_settings_root_note(Path(project_root), Path.cwd())
        try:
            daemon_status = await loop.run_in_executor(
                None, lambda: _client.project_status(project_root)
            )
            total_files, languages, directories, tree, truncated = await loop.run_in_executor(
                None, lambda: _build_codebase_overview(Path(project_root), max_depth, max_entries)
            )
            return CodebaseOverviewResult(
                success=True,
                total_source_files=total_files,
                languages=languages,
                top_level_directories=directories,
                tree=tree,
                truncated=truncated,
                freshness=_freshness_from_status(daemon_status),
                message=_with_note(None, note),
            )
        except Exception as e:
            return CodebaseOverviewResult(
                success=False,
                freshness=IndexFreshnessModel(stale=True),
                message=_with_note(f"Codebase overview failed: {e!s}", note),
            )

    return mcp


# === Daemon heartbeat loop ===


def heartbeat_interval_s(idle_timeout_minutes: int) -> int:
    """Heartbeat period for a given idle timeout: a third of the timeout,
    clamped to [30 s, 300 s] so several heartbeats always fit in one idle
    window without hammering the daemon on very long timeouts.
    """
    return max(30, min(300, idle_timeout_minutes * 60 // 3))


async def run_heartbeat_loop() -> None:
    """Periodically heartbeat the daemon so it never idle-exits under this
    live MCP session when ``daemon.keep_alive_with_mcp`` is enabled. When the
    option is disabled, the daemon may idle-exit and the next real request
    restarts it transparently. When this process dies (even SIGKILL), the
    heartbeats stop and the daemon exits on its normal idle timeout.

    Reads the timeout from the same ``global_settings.yml`` the daemon reads
    (dataclass default when the file is missing or invalid). Returns
    immediately when the timeout is 0 (daemon never idle-exits) or MCP
    keep-alive is disabled. The heartbeat itself never starts or restarts a
    daemon — see ``client.send_heartbeat``.
    """
    try:
        daemon_settings = load_user_settings().daemon
    except (FileNotFoundError, ValueError):
        daemon_settings = DaemonSettings()
    timeout_minutes = daemon_settings.idle_timeout_minutes
    if timeout_minutes <= 0 or not daemon_settings.keep_alive_with_mcp:
        return

    from .client import send_heartbeat

    interval = heartbeat_interval_s(timeout_minutes)
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(interval)
        # Client I/O is blocking — run off the event loop thread.
        await loop.run_in_executor(None, send_heartbeat)


# Keep the old `mcp` global for backward compatibility in __init__.py
mcp: MCPServer | None = None


# === Backward-compatible entry point ===


def _convert_embedding_model(env_model: str) -> tuple[str, str]:
    """Convert old COCOINDEX_CODE_EMBEDDING_MODEL to (provider, model)."""
    sbert_prefix = "sbert/"
    if env_model.startswith(sbert_prefix):
        return "sentence-transformers", env_model[len(sbert_prefix) :]
    return "litellm", env_model


def main() -> None:
    """Backward-compatible entry point for ``cocoindex-code`` CLI.

    Auto-detects/creates settings from env vars, then delegates to daemon.
    """
    import argparse
    import sys

    from .settings import (
        EmbeddingSettings,
        LanguageOverride,
        auto_init_refusal,
        default_project_settings,
        default_user_settings,
        find_legacy_project_root,
        find_project_root,
        project_settings_path,
        save_project_settings,
        save_user_settings,
        user_settings_path,
    )

    parser = argparse.ArgumentParser(
        prog="cocoindex-code",
        description="MCP server for codebase indexing and querying.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Run the MCP server (default)")
    subparsers.add_parser("index", help="Build/refresh the index and report stats")
    args = parser.parse_args()

    # --- Discover project root ---
    cwd = Path.cwd()
    project_root = find_project_root(cwd)
    root_from_env = False

    if project_root is None:
        # Try env var
        env_root = os.environ.get("COCOINDEX_CODE_ROOT_PATH")
        if env_root:
            project_root = Path(env_root).resolve()
            root_from_env = True
        else:
            # Use marker-based discovery
            legacy_root = find_legacy_project_root(cwd)
            project_root = legacy_root if legacy_root is not None else cwd

    # --- Auto-create project settings if needed ---
    proj_settings_file = project_settings_path(project_root)
    if not proj_settings_file.is_file():
        # COCOINDEX_CODE_ROOT_PATH is a deliberate choice, like `ccc init`: only a
        # root found by discovery is refused.
        refusal = None if root_from_env else auto_init_refusal(project_root)
        if refusal is not None:
            sys.exit(
                f"Error: {refusal} To serve a project from here, set"
                " COCOINDEX_CODE_ROOT_PATH to its directory."
            )
        ps = default_project_settings()

        # Migrate COCOINDEX_CODE_EXCLUDED_PATTERNS
        raw_excluded = os.environ.get("COCOINDEX_CODE_EXCLUDED_PATTERNS", "").strip()
        if raw_excluded:
            try:
                extra_excluded = json.loads(raw_excluded)
                if isinstance(extra_excluded, list):
                    ps.exclude_patterns.extend(
                        p.strip() for p in extra_excluded if isinstance(p, str) and p.strip()
                    )
            except json.JSONDecodeError:
                pass

        # Migrate COCOINDEX_CODE_EXTRA_EXTENSIONS
        raw_extra = os.environ.get("COCOINDEX_CODE_EXTRA_EXTENSIONS", "")
        for token in raw_extra.split(","):
            token = token.strip()
            if not token:
                continue
            if ":" in token:
                ext, lang = token.split(":", 1)
                ext = ext.strip()
                lang = lang.strip()
                ps.include_patterns.append(f"**/*.{ext}")
                if lang:
                    ps.language_overrides.append(LanguageOverride(ext=ext, lang=lang))
            else:
                ps.include_patterns.append(f"**/*.{token}")

        save_project_settings(project_root, ps)

    # --- Auto-create user settings if needed ---
    user_file = user_settings_path()
    if not user_file.is_file():
        us = default_user_settings()

        # Migrate COCOINDEX_CODE_EMBEDDING_MODEL
        env_model = os.environ.get("COCOINDEX_CODE_EMBEDDING_MODEL", "")
        if env_model:
            provider, model = _convert_embedding_model(env_model)
            us.embedding = EmbeddingSettings(provider=provider, model=model)

        # Migrate COCOINDEX_CODE_DEVICE
        env_device = os.environ.get("COCOINDEX_CODE_DEVICE")
        if env_device:
            us.embedding.device = env_device

        save_user_settings(us)

    # --- Delegate to daemon ---
    from . import client as _client
    from .protocol import IndexingProgress

    if args.command == "index":
        from rich.console import Console
        from rich.live import Live
        from rich.spinner import Spinner

        from .cli import _format_progress

        err_console = Console(stderr=True)
        last_progress_line: str | None = None

        with Live(Spinner("dots", "Indexing..."), console=err_console, transient=True) as live:

            def _on_waiting() -> None:
                live.update(
                    Spinner(
                        "dots",
                        "Another indexing is ongoing, waiting for it to finish...",
                    )
                )

            def _on_progress(progress: IndexingProgress) -> None:
                nonlocal last_progress_line
                last_progress_line = f"Indexing: {_format_progress(progress)}"
                live.update(Spinner("dots", last_progress_line))

            resp = _client.index(
                str(project_root), on_progress=_on_progress, on_waiting=_on_waiting
            )

        if last_progress_line is not None:
            print(last_progress_line, file=sys.stderr)

        if resp.success:
            st = _client.project_status(str(project_root))
            print("\nIndex stats:")
            print(f"  Chunks: {st.total_chunks}")
            print(f"  Files:  {st.total_files}")
            if st.languages:
                print("  Languages:")
                for lang, count in sorted(st.languages.items(), key=lambda x: -x[1]):
                    print(f"    {lang}: {count} chunks")
        else:
            print(f"Indexing failed: {resp.message}")
    else:
        # Default: run MCP server
        mcp_server = create_mcp_server(str(project_root))

        async def _serve() -> None:
            from .cli import _bg_index

            background_tasks = {
                asyncio.create_task(_bg_index(str(project_root))),
                asyncio.create_task(run_heartbeat_loop()),
            }
            try:
                await mcp_server.run_stdio_async()
            finally:
                for task in background_tasks:
                    task.cancel()

        asyncio.run(_serve())
