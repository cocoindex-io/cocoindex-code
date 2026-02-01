"""MCP server for codebase indexing and querying."""

import threading

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .indexer import app as indexer_app
from .query import query_codebase

# Initialize MCP server
mcp = FastMCP(
    "cocoindex-code",
    instructions="""
This server provides semantic code search for the codebase.

Use the `query` tool when you need to:
- Find code related to a concept or functionality
- Search for implementations of specific features
- Discover how something is done in the codebase
- Find similar code patterns

The `query` tool has a `refresh_index` parameter (default: True) that refreshes
the index before searching. Set it to False for consecutive queries to avoid
redundant refreshes.

The search uses vector embeddings for semantic similarity, so you can describe
what you're looking for in natural language rather than exact text matches.
""".strip(),
)

# Lock to prevent concurrent index updates
_index_lock = threading.Lock()


def _refresh_index() -> None:
    """Refresh the index. Uses lock to prevent concurrent updates."""
    with _index_lock:
        indexer_app.update(report_to_stdout=False)


# === Pydantic Models for Tool Inputs/Outputs ===


class CodeChunkResult(BaseModel):
    """A single code chunk result."""

    file_path: str = Field(description="Relative path to the file")
    language: str = Field(description="Programming language")
    content: str = Field(description="The code content")
    start_line: int = Field(description="Starting line number (1-indexed)")
    end_line: int = Field(description="Ending line number (1-indexed)")
    score: float = Field(description="Similarity score (0-1, higher is better)")


class QueryResultModel(BaseModel):
    """Result from query tool."""

    success: bool
    results: list[CodeChunkResult] = Field(default_factory=list)
    total_returned: int = Field(default=0)
    offset: int = Field(default=0)
    message: str | None = None


# === MCP Tools ===


@mcp.tool(
    name="query",
    description=(
        "Search the codebase using semantic similarity. "
        "Returns relevant code chunks with file locations and similarity scores. "
        "Use natural language queries or code snippets to find related code."
    ),
)
def query(
    query: str = Field(description="Natural language query or code snippet to search for"),
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
            "Whether to refresh the index before querying. "
            "Set to False for consecutive queries to skip redundant refreshes."
        ),
    ),
) -> QueryResultModel:
    """Query the codebase index."""
    try:
        # Refresh index if requested
        if refresh_index:
            _refresh_index()

        results = query_codebase(query=query, limit=limit, offset=offset)

        return QueryResultModel(
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
        return QueryResultModel(
            success=False,
            message=str(e),
        )
    except Exception as e:
        return QueryResultModel(
            success=False,
            message=f"Query failed: {e!s}",
        )


def main() -> None:
    """Entry point for the MCP server."""
    # Refresh index in background so startup isn't blocked
    threading.Thread(target=_refresh_index, daemon=True).start()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
