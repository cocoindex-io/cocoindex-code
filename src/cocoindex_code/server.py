"""MCP server for codebase indexing and querying."""

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .indexer import app as indexer_app
from .query import CodebaseQuerier, QueryParams
from .shared import config

# Initialize MCP server
mcp = FastMCP("cocoindex-code")

# Lazy-initialized querier (created on first query)
_querier: CodebaseQuerier | None = None


def _get_querier() -> CodebaseQuerier:
    """Get or create the querier instance."""
    global _querier
    if _querier is None:
        _querier = CodebaseQuerier()
    return _querier


# === Pydantic Models for Tool Inputs/Outputs ===


class UpdateIndexResult(BaseModel):
    """Result from update_index tool."""

    success: bool
    message: str
    codebase_root: str


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
    name="update_index",
    description=(
        "Update the codebase index to reflect the latest content. "
        "This will scan the codebase directory and update the vector index "
        "with any new or modified files. Unchanged files are skipped for efficiency. "
        "Run this before querying if you've made changes to the codebase."
    ),
)
def update_index() -> UpdateIndexResult:
    """Update the codebase index."""
    global _querier

    try:
        # Close existing querier to release database lock
        if _querier is not None:
            _querier.close()
            _querier = None

        # Run the indexer app update
        indexer_app.update(report_to_stdout=False)

        return UpdateIndexResult(
            success=True,
            message="Index updated successfully",
            codebase_root=str(config.codebase_root_path),
        )
    except Exception as e:
        return UpdateIndexResult(
            success=False,
            message=f"Failed to update index: {e!s}",
            codebase_root=str(config.codebase_root_path),
        )


@mcp.tool(
    name="query",
    description=(
        "Search the codebase using semantic similarity. "
        "Returns relevant code chunks with file locations and similarity scores. "
        "Use natural language queries or code snippets to find related code. "
        "Supports pagination via limit and offset parameters."
    ),
    annotations={"readOnlyHint": True},  # type: ignore[arg-type]
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
) -> QueryResultModel:
    """Query the codebase index."""
    try:
        querier = _get_querier()

        results = querier.query(
            QueryParams(
                query=query,
                limit=limit,
                offset=offset,
            )
        )

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
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
