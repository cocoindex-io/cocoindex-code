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
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

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


class SearchResultModel(BaseModel):
    """Result from search tool."""

    success: bool
    results: list[CodeChunkResult] = Field(default_factory=list)
    total_returned: int = Field(default=0)
    offset: int = Field(default=0)
    message: str | None = None


# === Daemon-backed MCP server factory ===


def _graph_db_path(project_root: str) -> Path:
    root = Path(project_root)
    declarations_db = root / ".cocoindex_code" / "declarations.db"
    if declarations_db.exists():
        return declarations_db
    from . import settings as _settings

    return _settings.target_sqlite_db_path(root)


def create_mcp_server(project_root: str) -> FastMCP:
    """Create a lightweight MCP server that delegates to the daemon."""
    mcp = FastMCP("cocoindex-code", instructions=_MCP_INSTRUCTIONS)

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
        mode: str = Field(
            default="vector",
            description="Search mode: vector, keyword, or hybrid.",
        ),
    ) -> SearchResultModel:
        """Query the codebase index via the daemon."""
        from . import client as _client

        loop = asyncio.get_event_loop()
        try:
            mode_value = mode.lower()
            if mode_value not in {"vector", "keyword", "hybrid"}:
                return SearchResultModel(
                    success=False,
                    message=f"invalid mode {mode!r}; expected vector, keyword, or hybrid",
                )
            if refresh_index:
                await loop.run_in_executor(None, lambda: _client.index(project_root))
            if mode_value in {"keyword", "hybrid"}:
                from . import hybrid_search as _hyb
                from . import settings as _settings

                db_path = _settings.target_sqlite_db_path(Path(project_root))
                await loop.run_in_executor(
                    None, lambda: _hyb.ensure_fts_index(db_path, force_rebuild=refresh_index)
                )
                if mode_value == "keyword":
                    keyword_rows = await loop.run_in_executor(
                        None,
                        lambda: _hyb.keyword_search(
                            db_path,
                            query,
                            limit=limit,
                            path_prefix=paths[0] if paths else None,
                            language=languages[0] if languages else None,
                        ),
                    )
                    return SearchResultModel(
                        success=True,
                        results=[
                            CodeChunkResult(
                                file_path=r.file_path,
                                language=languages[0] if languages else "",
                                content=r.content,
                                start_line=r.start_line,
                                end_line=r.end_line,
                                score=r.score,
                            )
                            for r in keyword_rows
                        ],
                        total_returned=len(keyword_rows),
                        offset=0,
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
            if mode_value == "hybrid":
                from . import hybrid_search as _hyb
                from . import settings as _settings

                db_path = _settings.target_sqlite_db_path(Path(project_root))
                keyword_rows = await loop.run_in_executor(
                    None,
                    lambda: _hyb.keyword_search(
                        db_path,
                        query,
                        limit=limit,
                        path_prefix=paths[0] if paths else None,
                        language=languages[0] if languages else None,
                    ),
                )
                vector_rows = [
                    {
                        "file_path": r.file_path,
                        "language": r.language,
                        "content": r.content,
                        "start_line": r.start_line,
                        "end_line": r.end_line,
                        "score": r.score,
                    }
                    for r in resp.results
                ]
                fused = _hyb.reciprocal_rank_fusion(
                    vector_results=vector_rows, keyword_results=keyword_rows, limit=limit
                )
                return SearchResultModel(
                    success=True,
                    results=[
                        CodeChunkResult(
                            file_path=r["file_path"],
                            language=r.get("language") or "",
                            content=r["content"],
                            start_line=r["start_line"],
                            end_line=r["end_line"],
                            score=float(r["score"]),
                        )
                        for r in fused
                    ],
                    total_returned=len(fused),
                    offset=0,
                    message=resp.message,
                )
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
                message=resp.message,
            )
        except Exception as e:
            return SearchResultModel(success=False, message=f"Query failed: {e!s}")

    @mcp.tool(
        name="get_impact_radius",
        description=(
            "Get the impact radius (BFS closure) of declarations via caller/callee relationships. "
            "Returns all nodes reachable within N hops of the given declaration IDs, "
            "including call graph edges and inheritance relationships."
        ),
    )
    async def get_impact_radius(
        decl_ids: list[int] = Field(description="Declaration IDs to analyze"),
        depth: int = Field(
            default=3,
            ge=1,
            le=5,
            description="Maximum BFS depth (1-5)",
        ),
        max_nodes: int = Field(
            default=200,
            ge=1,
            le=500,
            description="Maximum nodes to return",
        ),
    ) -> dict[str, object]:
        """Get impact radius for a declaration (callers, callees, inheritance chain)."""
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        db_path = _graph_db_path(project_root)
        return await loop.run_in_executor(
            None,
            lambda: mcp_handlers.query_impact_radius_tool(
                db_path,
                decl_ids,
                depth=depth,
                max_nodes=max_nodes,
            ),
        )

    @mcp.tool(
        name="detect_changes",
        description=(
            "Detect changed declarations from a git diff with risk scoring. "
            "Analyzes git diff output and returns affected declarations ranked by risk "
            "(combining centrality, test coverage, and change magnitude)."
        ),
    )
    async def detect_changes(
        repo_id: str = Field(description="Repository identifier"),
        ref_spec: str = Field(
            default="HEAD",
            description=(
                "Git ref spec to diff against. Examples: 'HEAD' (working tree), "
                "'develop' (branch), 'HEAD~3..HEAD' (commit range)"
            ),
        ),
        path_prefix: str | None = Field(
            default=None,
            description="Optional filter to changes in specific path",
        ),
        top_n: int = Field(
            default=20,
            ge=1,
            le=100,
            description="Number of top-risk declarations to return",
        ),
    ) -> dict[str, object]:
        """Detect changed declarations from git diff."""
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        db_path = _graph_db_path(project_root)
        return await loop.run_in_executor(
            None,
            lambda: mcp_handlers.detect_changes_tool(
                db_path,
                Path(project_root),
                repo_id,
                ref_spec,
                path_prefix=path_prefix,
                top_n=top_n,
            ),
        )

    @mcp.tool(
        name="get_architecture",
        description=(
            "Get architecture overview of the codebase: hub nodes (high betweenness), "
            "bridge nodes (connect clusters), and community structure."
        ),
    )
    async def get_architecture(
        repo_id: str | None = Field(
            default=None,
            description="Optional filter to specific repository",
        ),
        hub_limit: int = Field(
            default=20,
            ge=1,
            le=100,
            description="Number of hub nodes to return",
        ),
    ) -> dict[str, object]:
        """Get architecture overview with hub nodes and communities."""
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        db_path = _graph_db_path(project_root)
        return await loop.run_in_executor(
            None,
            lambda: mcp_handlers.get_architecture_overview_tool(
                db_path,
                repo_id=repo_id,
                hub_limit=hub_limit,
            ),
        )

    @mcp.tool(
        name="get_knowledge_gaps",
        description=(
            "Identify knowledge gaps in the codebase: untested hub nodes, "
            "isolated declarations, and thin communities."
        ),
    )
    async def get_knowledge_gaps(
        repo_id: str | None = Field(
            default=None,
            description="Optional filter to specific repository",
        ),
        limit: int = Field(
            default=30,
            ge=1,
            le=100,
            description="Limit results per category",
        ),
    ) -> dict[str, object]:
        """Get knowledge gaps (untested hubs, isolated nodes, thin communities)."""
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        db_path = _graph_db_path(project_root)
        return await loop.run_in_executor(
            None,
            lambda: mcp_handlers.get_knowledge_gaps_tool(
                db_path,
                repo_id=repo_id,
                limit=limit,
            ),
        )

    @mcp.tool(
        name="detect_flows",
        description=(
            "Detect flow entry points (FastAPI routes, HTTP handlers, queue consumers, etc) "
            "and rank them by criticality (in-degree and test coverage)."
        ),
    )
    async def detect_flows(
        repo_id: str | None = Field(
            default=None,
            description="Optional filter to specific repository",
        ),
        limit: int = Field(
            default=50,
            ge=1,
            le=200,
            description="Maximum flows to return",
        ),
    ) -> dict[str, object]:
        """Detect flow entry points."""
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        db_path = _graph_db_path(project_root)
        return await loop.run_in_executor(
            None,
            lambda: mcp_handlers.detect_flows_tool(
                db_path,
                repo_id=repo_id,
                limit=limit,
            ),
        )

    @mcp.tool(
        name="sync_configured_repos",
        description="Sync and link repositories configured in coco-config.yml",
    )
    async def sync_configured_repos(
        config_path: str | None = Field(default=None, description="Path to coco-config.yml"),
        repo_ids: list[str] | None = Field(
            default=None, description="Optional list of repo ids to sync"
        ),
        force: bool = Field(default=False, description="Force resync"),
    ) -> dict[str, Any]:
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: mcp_handlers.sync_configured_repos_tool(
                config_path=config_path, repo_ids=repo_ids, force=force
            ),
        )

    @mcp.tool(
        name="get_repo_sync_status",
        description="Get repository synchronization status for configured repos.",
    )
    async def get_repo_sync_status(
        config_path: str | None = Field(default=None, description="Path to coco-config.yml"),
    ) -> dict[str, Any]:
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: mcp_handlers.get_repo_sync_status_tool(config_path=config_path),
        )

    @mcp.tool(
        name="hybrid_search",
        description="Hybrid vector + BM25 search (fusion).",
    )
    async def hybrid_search(
        query: str = Field(description="Query string"),
        limit: int = Field(default=5, ge=1, le=100),
        refresh_index: bool = Field(default=True),
    ) -> SearchResultModel:
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: mcp_handlers.hybrid_search_tool(
                project_root, query, limit=limit, refresh_index=refresh_index
            ),
        )
        if not resp.get("success"):
            return SearchResultModel(
                success=False, results=[], total_returned=0, offset=0, message=resp.get("error")
            )
        fused = resp.get("results", [])
        results = [
            CodeChunkResult(
                file_path=r.get("file_path", ""),
                language=r.get("language") or "",
                content=r.get("content", ""),
                start_line=r.get("start_line", 0),
                end_line=r.get("end_line", 0),
                score=float(r.get("score") or 0.0),
            )
            for r in fused
        ]
        return SearchResultModel(
            success=True, results=results, total_returned=len(results), offset=0, message=None
        )

    @mcp.tool(
        name="ripgrep_bounded",
        description="Run bounded ripgrep and return matches.",
    )
    async def ripgrep_bounded(
        pattern: str = Field(description="Search pattern"),
        path_prefix: str | None = Field(default=None),
        glob: str | None = Field(default=None),
        fixed_strings: bool = Field(default=True),
        max_matches: int = Field(default=200),
        per_file_cap: int = Field(default=40),
        wall_timeout_s: float = Field(default=25.0),
    ) -> dict[str, Any]:
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: mcp_handlers.ripgrep_bounded_tool(
                project_root,
                pattern,
                path_prefix=path_prefix,
                glob=glob,
                fixed_strings=fixed_strings,
                max_matches=max_matches,
                per_file_cap=per_file_cap,
                wall_timeout_s=wall_timeout_s,
            ),
        )

    # Replace the experimental/fork-internal names with a natural codebase_* surface.
    for _old_tool_name in (
        "search",
        "get_impact_radius",
        "detect_changes",
        "get_architecture",
        "get_knowledge_gaps",
        "detect_flows",
        "sync_configured_repos",
        "get_repo_sync_status",
        "hybrid_search",
        "ripgrep_bounded",
    ):
        mcp.remove_tool(_old_tool_name)

    @mcp.tool(
        name="codebase_index",
        description=(
            "Build or refresh the codebase index. Runs through the existing CocoIndex daemon "
            "and returns index stats when complete."
        ),
    )
    async def codebase_index() -> dict[str, Any]:
        from . import client as _client
        from . import settings as _settings
        from .code_graph_indexer import index_code_declarations

        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(None, lambda: _client.index(project_root))
            db_path = _settings.target_sqlite_db_path(Path(project_root))
            graph = await loop.run_in_executor(
                None,
                lambda: index_code_declarations(Path(project_root), db_path, repo_id="local"),
            )
            status = await loop.run_in_executor(None, lambda: _client.project_status(project_root))
            return {
                "success": resp.success,
                "message": resp.message,
                "chunks": status.total_chunks,
                "files": status.total_files,
                "languages": status.languages,
                "graph": graph,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @mcp.tool(
        name="codebase_update",
        description=(
            "Incrementally update the codebase index. Native CocoIndex memoization skips "
            "unchanged files; this is the normal update command after edits."
        ),
    )
    async def codebase_update() -> dict[str, Any]:
        return await codebase_index()

    @mcp.tool(
        name="codebase_stop",
        description=(
            "Stop the running cocoindex-code daemon. Use when an indexing process is stuck; "
            "progress already committed by CocoIndex is preserved."
        ),
    )
    async def codebase_stop() -> dict[str, Any]:
        from . import client as _client

        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(None, _client.stop)
            return {"success": bool(resp.ok)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @mcp.tool(
        name="codebase_remove",
        description="Remove this project's loaded daemon state and release project resources.",
    )
    async def codebase_remove() -> dict[str, Any]:
        from . import client as _client

        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(None, lambda: _client.remove_project(project_root))
            return {"success": bool(resp.ok)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    watch_task: asyncio.Task[None] | None = None
    watch_last_error: str | None = None
    watch_index_runs = 0
    watch_started_at: float | None = None
    watch_snapshot: dict[str, int] = {}
    watch_excluded_dirs = {
        ".git",
        ".cocoindex_code",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }

    def _watch_file_snapshot(root: Path) -> dict[str, int]:
        snapshot: dict[str, int] = {}
        for current_root, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if dirname not in watch_excluded_dirs and not dirname.startswith(".tmp")
            ]
            for filename in filenames:
                path = Path(current_root) / filename
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if not path.is_file():
                    continue
                snapshot[str(path.relative_to(root))] = stat.st_mtime_ns
        return snapshot

    async def _watch_loop(interval_seconds: float) -> None:
        nonlocal watch_index_runs, watch_last_error, watch_snapshot
        from . import client as _client

        loop = asyncio.get_event_loop()
        root = Path(project_root)
        watch_snapshot = await loop.run_in_executor(None, lambda: _watch_file_snapshot(root))
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                current = await loop.run_in_executor(None, lambda: _watch_file_snapshot(root))
                if current != watch_snapshot:
                    watch_snapshot = current
                    await loop.run_in_executor(None, lambda: _client.index(project_root))
                    watch_index_runs += 1
                    watch_last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                watch_last_error = str(exc)

    @mcp.tool(
        name="codebase_watch",
        description="Start, stop, or inspect a lightweight incremental file watcher.",
    )
    async def codebase_watch(
        action: str = Field(description="start, stop, or status"),
        interval_seconds: float = Field(default=2.0, ge=0.5, le=60.0),
    ) -> dict[str, Any]:
        nonlocal watch_task, watch_started_at

        if action not in {"start", "stop", "status"}:
            return {"success": False, "error": "action must be start, stop, or status"}
        if action == "start":
            if watch_task and not watch_task.done():
                return {
                    "success": True,
                    "watching": True,
                    "message": "Watcher is already running.",
                    "index_runs": watch_index_runs,
                    "last_error": watch_last_error,
                }
            watch_started_at = asyncio.get_event_loop().time()
            watch_task = asyncio.create_task(_watch_loop(interval_seconds))
            return {
                "success": True,
                "watching": True,
                "message": "Watcher started.",
                "interval_seconds": interval_seconds,
            }
        if action == "stop":
            if watch_task and not watch_task.done():
                watch_task.cancel()
                try:
                    await watch_task
                except asyncio.CancelledError:
                    pass
            watch_task = None
            watch_started_at = None
            return {"success": True, "watching": False, "message": "Watcher stopped."}

        watching = bool(watch_task and not watch_task.done())
        uptime_seconds = (
            asyncio.get_event_loop().time() - watch_started_at
            if watching and watch_started_at is not None
            else 0.0
        )
        return {
            "success": True,
            "watching": watching,
            "index_runs": watch_index_runs,
            "last_error": watch_last_error,
            "uptime_seconds": uptime_seconds,
        }

    @mcp.tool(
        name="codebase_search",
        description=(
            "Hybrid semantic + keyword search across the indexed codebase. Uses vector "
            "search, SQLite FTS5 BM25, and reciprocal rank fusion by default."
        ),
    )
    async def codebase_search(
        query: str = Field(description="Natural language, identifier, or code query"),
        limit: int = Field(default=10, ge=1, le=100),
        offset: int = Field(default=0, ge=0),
        refresh_index: bool = Field(default=False),
        languages: list[str] | None = Field(default=None),
        paths: list[str] | None = Field(default=None),
        mode: str = Field(default="hybrid", description="hybrid, vector, keyword, or grep"),
    ) -> SearchResultModel | dict[str, Any]:
        if mode == "grep":
            from . import mcp_handlers

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: mcp_handlers.ripgrep_bounded_tool(
                    project_root,
                    query,
                    path_prefix=paths[0] if paths else None,
                    max_matches=limit,
                ),
            )
        return await search(
            query=query,
            limit=limit,
            offset=offset,
            refresh_index=refresh_index,
            languages=languages,
            paths=paths,
            mode=mode,
        )

    @mcp.tool(name="codebase_status", description="Show index, daemon, graph, and artifact status.")
    async def codebase_status() -> dict[str, Any]:
        from . import client as _client
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        status_payload: dict[str, Any] = {"project_root": project_root}
        try:
            st = await loop.run_in_executor(None, lambda: _client.project_status(project_root))
            status_payload.update(
                {
                    "success": True,
                    "indexing": st.indexing,
                    "chunks": st.total_chunks,
                    "files": st.total_files,
                    "languages": st.languages,
                    "index_exists": st.index_exists,
                }
            )
        except Exception as exc:
            status_payload.update({"success": False, "error": str(exc)})
        db_path = _graph_db_path(project_root)
        status_payload["graph"] = await loop.run_in_executor(
            None, lambda: mcp_handlers.codebase_graph_stats_tool(db_path)
        )
        status_payload["context"] = mcp_handlers.codebase_context_list_tool(Path(project_root))
        return status_payload

    @mcp.tool(name="codebase_graph_build", description="Build or refresh graph-derived metadata.")
    async def codebase_graph_build(
        repo_id: str | None = Field(default=None),
        recompute_analytics: bool = Field(default=True),
    ) -> dict[str, Any]:
        from . import mcp_handlers
        from .analytics.centrality import compute_centrality
        from .analytics.communities import compute_communities
        from .code_graph_indexer import index_code_declarations

        loop = asyncio.get_event_loop()
        db_path = _graph_db_path(project_root)
        result: dict[str, Any] = {
            "success": True,
            "index": await loop.run_in_executor(
                None,
                lambda: index_code_declarations(
                    Path(project_root), db_path, repo_id=repo_id or "local"
                ),
            ),
        }
        if recompute_analytics:
            result["centrality"] = await loop.run_in_executor(
                None, lambda: compute_centrality(db_path, repo_id=repo_id)
            )
            result["communities"] = await loop.run_in_executor(
                None, lambda: compute_communities(db_path, repo_id=repo_id)
            )
        result["stats"] = await loop.run_in_executor(
            None, lambda: mcp_handlers.codebase_graph_stats_tool(db_path, repo_id=repo_id)
        )
        return result

    @mcp.tool(
        name="codebase_graph_query",
        description="Show imports, dependents, and symbols for a file.",
    )
    async def codebase_graph_query(
        file_path: str = Field(description="Relative file path"),
        repo_id: str | None = Field(default=None),
    ) -> dict[str, Any]:
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        db_path = _graph_db_path(project_root)
        return await loop.run_in_executor(
            None, lambda: mcp_handlers.codebase_graph_query_tool(db_path, file_path, repo_id)
        )

    @mcp.tool(name="codebase_graph_stats", description="Return dependency and symbol graph stats.")
    async def codebase_graph_stats(
        repo_id: str | None = Field(default=None),
    ) -> dict[str, Any]:
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        db_path = _graph_db_path(project_root)
        return await loop.run_in_executor(
            None, lambda: mcp_handlers.codebase_graph_stats_tool(db_path, repo_id=repo_id)
        )

    @mcp.tool(name="codebase_graph_circular", description="Find file-level circular dependencies.")
    async def codebase_graph_circular(
        repo_id: str | None = Field(default=None),
        limit: int = Field(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        db_path = _graph_db_path(project_root)
        return await loop.run_in_executor(
            None, lambda: mcp_handlers.codebase_graph_circular_tool(db_path, repo_id, limit)
        )

    @mcp.tool(name="codebase_graph_visualize", description="Return a Mermaid dependency graph.")
    async def codebase_graph_visualize(
        repo_id: str | None = Field(default=None),
        limit: int = Field(default=120, ge=1, le=500),
    ) -> dict[str, Any]:
        from .declarations_db import db_connection

        db_path = _graph_db_path(project_root)
        try:
            with db_connection(db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT c.file_path AS from_file, d.file_path AS to_file
                    FROM calls c
                    JOIN declarations d ON d.id = c.callee_decl_id
                    WHERE c.callee_decl_id IS NOT NULL
                      AND c.file_path != d.file_path
                      AND (? IS NULL OR c.repo_id = ?)
                    LIMIT ?
                    """,
                    (repo_id, repo_id, limit),
                ).fetchall()
            lines = ["graph TD"]
            for row in rows:
                src = str(row["from_file"]).replace("-", "_").replace("/", "_").replace(".", "_")
                dst = str(row["to_file"]).replace("-", "_").replace("/", "_").replace(".", "_")
                lines.append(f'  {src}["{row["from_file"]}"] --> {dst}["{row["to_file"]}"]')
            return {"success": True, "mode": "mermaid", "mermaid": "\n".join(lines)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @mcp.tool(name="codebase_graph_status", description="Show graph status.")
    async def codebase_graph_status() -> dict[str, Any]:
        return await codebase_graph_stats()

    @mcp.tool(
        name="codebase_graph_remove",
        description="Graph metadata is derived; returns current behavior.",
    )
    async def codebase_graph_remove() -> dict[str, Any]:
        from .declarations_db import db_connection

        db_path = _graph_db_path(project_root)
        try:
            with db_connection(db_path) as conn:
                deleted = {}
                for table in (
                    "calls",
                    "inherits",
                    "centrality",
                    "communities",
                    "tests",
                    "declarations",
                    "imports",
                    '"references"',
                    "file_signatures",
                ):
                    deleted[table] = conn.execute(f"DELETE FROM {table}").rowcount
            return {"success": True, "removed": deleted}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @mcp.tool(name="codebase_impact", description="Blast radius for a file path or symbol name.")
    async def codebase_impact(
        target: str = Field(description="File path or symbol name"),
        repo_id: str | None = Field(default=None),
        depth: int = Field(default=3, ge=1, le=10),
        max_nodes: int = Field(default=200, ge=1, le=1000),
    ) -> dict[str, Any]:
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        db_path = _graph_db_path(project_root)
        return await loop.run_in_executor(
            None,
            lambda: mcp_handlers.codebase_impact_tool(
                db_path, target=target, repo_id=repo_id, depth=depth, max_nodes=max_nodes
            ),
        )

    @mcp.tool(
        name="codebase_flow",
        description="Trace forward call flow or list likely entry points.",
    )
    async def codebase_flow(
        entrypoint: str | None = Field(default=None),
        file: str | None = Field(default=None),
        repo_id: str | None = Field(default=None),
        depth: int = Field(default=5, ge=1, le=10),
    ) -> dict[str, Any]:
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        db_path = _graph_db_path(project_root)
        return await loop.run_in_executor(
            None,
            lambda: mcp_handlers.codebase_flow_tool(
                db_path, entrypoint=entrypoint, file=file, repo_id=repo_id, depth=depth
            ),
        )

    @mcp.tool(name="codebase_symbol", description="Definition, callers, and callees for a symbol.")
    async def codebase_symbol(
        name: str = Field(description="Symbol name"),
        file: str | None = Field(default=None),
        repo_id: str | None = Field(default=None),
    ) -> dict[str, Any]:
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        db_path = _graph_db_path(project_root)
        return await loop.run_in_executor(
            None,
            lambda: mcp_handlers.codebase_symbol_tool(
                db_path,
                name=name,
                file=file,
                repo_id=repo_id,
            ),
        )

    @mcp.tool(
        name="codebase_symbols",
        description="List symbols in a file or search symbols by name.",
    )
    async def codebase_symbols(
        file: str | None = Field(default=None),
        query: str | None = Field(default=None),
        repo_id: str | None = Field(default=None),
        limit: int = Field(default=200, ge=1, le=1000),
    ) -> dict[str, Any]:
        from . import mcp_handlers

        loop = asyncio.get_event_loop()
        db_path = _graph_db_path(project_root)
        return await loop.run_in_executor(
            None,
            lambda: mcp_handlers.codebase_symbols_tool(
                db_path, file=file, query=query, repo_id=repo_id, limit=limit
            ),
        )

    @mcp.tool(name="codebase_context", description="List configured context artifacts.")
    async def codebase_context() -> dict[str, Any]:
        from . import mcp_handlers

        return mcp_handlers.codebase_context_list_tool(Path(project_root))

    @mcp.tool(
        name="codebase_context_index",
        description="Validate context artifacts for this project.",
    )
    async def codebase_context_index() -> dict[str, Any]:
        from . import mcp_handlers

        return mcp_handlers.codebase_context_index_tool(Path(project_root))

    @mcp.tool(name="codebase_context_search", description="Search configured context artifacts.")
    async def codebase_context_search(
        query: str = Field(description="Search query"),
        artifact: str | None = Field(default=None, description="Optional artifact name to search"),
        limit: int = Field(default=10, ge=1, le=100),
    ) -> dict[str, Any]:
        from . import mcp_handlers

        return mcp_handlers.codebase_context_search_tool(
            Path(project_root), query, artifact=artifact, limit=limit
        )

    @mcp.tool(
        name="codebase_context_remove",
        description="Remove local context artifact index metadata.",
    )
    async def codebase_context_remove() -> dict[str, Any]:
        from . import mcp_handlers

        return mcp_handlers.codebase_context_remove_tool(Path(project_root))

    @mcp.tool(name="codebase_health", description="Check daemon and local project health.")
    async def codebase_health() -> dict[str, Any]:
        from . import client as _client

        loop = asyncio.get_event_loop()
        try:
            daemon = await loop.run_in_executor(None, _client.daemon_status)
            project = await loop.run_in_executor(None, lambda: _client.project_status(project_root))
            return {
                "success": True,
                "daemon": {
                    "version": daemon.version,
                    "uptime_seconds": daemon.uptime_seconds,
                    "projects": [p.project_root for p in daemon.projects],
                },
                "project": {
                    "index_exists": project.index_exists,
                    "files": project.total_files,
                    "chunks": project.total_chunks,
                    "indexing": project.indexing,
                },
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @mcp.tool(name="codebase_list_projects", description="List projects loaded in the daemon.")
    async def codebase_list_projects() -> dict[str, Any]:
        from . import client as _client

        loop = asyncio.get_event_loop()
        try:
            daemon = await loop.run_in_executor(None, _client.daemon_status)
            return {
                "success": True,
                "projects": [
                    {"project_root": p.project_root, "indexing": p.indexing}
                    for p in daemon.projects
                ],
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @mcp.tool(name="codebase_about", description="Explain available codebase tools.")
    async def codebase_about() -> dict[str, Any]:
        return {
            "success": True,
            "name": "cocoindex-code",
            "summary": (
                "Native codebase intelligence: hybrid search, dependency graph, "
                "impact analysis, symbols, and context artifacts."
            ),
            "tools": [
                "codebase_index",
                "codebase_update",
                "codebase_search",
                "codebase_status",
                "codebase_graph_*",
                "codebase_impact",
                "codebase_flow",
                "codebase_symbol",
                "codebase_symbols",
                "codebase_context_*",
                "codebase_health",
                "codebase_list_projects",
                "codebase_about",
            ],
        }

    return mcp


# Keep the old `mcp` global for backward compatibility in __init__.py
mcp: FastMCP | None = None


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

    from .settings import (
        EmbeddingSettings,
        LanguageOverride,
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

    if project_root is None:
        # Try env var
        env_root = os.environ.get("COCOINDEX_CODE_ROOT_PATH")
        if env_root:
            project_root = Path(env_root).resolve()
        else:
            # Use marker-based discovery
            legacy_root = find_legacy_project_root(cwd)
            project_root = legacy_root if legacy_root is not None else cwd

    # --- Auto-create project settings if needed ---
    proj_settings_file = project_settings_path(project_root)
    if not proj_settings_file.is_file():
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
        import sys

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

            asyncio.create_task(_bg_index(str(project_root)))
            await mcp_server.run_stdio_async()

        asyncio.run(_serve())
