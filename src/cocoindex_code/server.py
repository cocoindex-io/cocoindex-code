"""MCP server for codebase indexing and querying."""

import argparse
import asyncio
import sqlite3

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .indexer import app as indexer_app
from .query import query_codebase
from .shared import config

# Initialize MCP server
mcp = FastMCP(
    "cocoindex-code",
    instructions=(
        "Code search and codebase understanding tools."
        "\n"
        "Use when you need to find code, understand how something works,"
        " locate implementations, or explore an unfamiliar codebase."
        "\n"
        "Provides semantic search that understands meaning --"
        " unlike grep or text matching,"
        " it finds relevant code even when exact keywords are unknown."
    ),
)

# Lock to prevent concurrent index updates
_index_lock = asyncio.Lock()


async def _refresh_index() -> None:
    """Refresh the index. Uses lock to prevent concurrent updates."""
    async with _index_lock:
        await indexer_app.update(report_to_stdout=False)


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


# === MCP Tools ===


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
        default=10,
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
) -> SearchResultModel:
    """Query the codebase index."""
    try:
        # Refresh index if requested
        if refresh_index:
            await _refresh_index()

        results = await query_codebase(query=query, limit=limit, offset=offset)

        return SearchResultModel(
            success=True,
            results=[
                CodeChunkResult(
                    file_path=r.file_path,
                    language=r.language,
                    content=r.content,
                    start_line=r.start_line,
                    end_line=r.end_line,
                    score=r.score,
                )
                for r in results
            ],
            total_returned=len(results),
            offset=offset,
        )
    except RuntimeError as e:
        # Index doesn't exist
        return SearchResultModel(
            success=False,
            message=str(e),
        )
    except Exception as e:
        return SearchResultModel(
            success=False,
            message=f"Query failed: {e!s}",
        )


async def _async_serve() -> None:
    """Async entry point for the MCP server."""
    # Refresh index in background so startup isn't blocked
    asyncio.create_task(_refresh_index())
    await mcp.run_stdio_async()


async def _async_index() -> None:
    """Async entry point for the index command."""
    await indexer_app.update(report_to_stdout=True)
    _print_index_stats()


def _print_index_stats() -> None:
    """Print index statistics from the database."""
    db_path = config.target_sqlite_db_path
    if not db_path.exists():
        print("No index database found.")
        return

    conn = sqlite3.connect(str(db_path))
    try:
        total_chunks = conn.execute("SELECT COUNT(*) FROM code_chunks").fetchone()[0]
        total_files = conn.execute("SELECT COUNT(DISTINCT file_path) FROM code_chunks").fetchone()[
            0
        ]
        langs = conn.execute(
            "SELECT language, COUNT(*) as cnt FROM code_chunks GROUP BY language ORDER BY cnt DESC"
        ).fetchall()

        print("\nIndex stats:")
        print(f"  Chunks: {total_chunks}")
        print(f"  Files:  {total_files}")
        if langs:
            print("  Languages:")
            for lang, count in langs:
                print(f"    {lang}: {count} chunks")
    finally:
        conn.close()


def main() -> None:
    """Entry point for the cocoindex-code CLI."""
    parser = argparse.ArgumentParser(
        prog="cocoindex-code",
        description="MCP server for codebase indexing and querying.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Run the MCP server (default)")
    subparsers.add_parser("index", help="Build/refresh the index and report stats")

    args = parser.parse_args()

    if args.command == "index":
        asyncio.run(_async_index())
    else:
        asyncio.run(_async_serve())


if __name__ == "__main__":
    main()
