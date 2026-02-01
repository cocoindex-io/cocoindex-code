"""Query implementation for codebase search."""

from dataclasses import dataclass

import cocoindex as coco

from .schema import QueryResult
from .shared import SQLITE_DB, config, embedder


@dataclass
class QueryParams:
    """Parameters for querying the codebase."""

    query: str
    limit: int = 10
    offset: int = 0


class CodebaseQuerier:
    """Handles vector similarity queries against the index."""

    def query(self, params: QueryParams) -> list[QueryResult]:
        """
        Perform vector similarity search.

        Uses sqlite-vec's vec_distance_cosine for similarity scoring.
        """
        if not config.target_sqlite_db_path.exists():
            raise RuntimeError(
                f"Index database not found at {config.target_sqlite_db_path}. "
                "Run the 'update_index' tool first to create the index."
            )

        # Get the database connection from CocoIndex environment
        db = coco.default_env().get_context(SQLITE_DB)

        # Generate query embedding
        query_embedding = embedder.embed(params.query)

        # Convert to bytes for sqlite-vec (float32)
        embedding_bytes = query_embedding.astype("float32").tobytes()

        # Query using sqlite-vec with readonly transaction
        # vec_distance_cosine returns distance (lower is better),
        # so we convert to similarity score (1 - distance)
        with db.value.readonly() as conn:
            cursor = conn.execute(
                """
                SELECT
                    file_path,
                    language,
                    content,
                    start_line,
                    end_line,
                    (1.0 - vec_distance_cosine(embedding, ?)) as score
                FROM code_chunks
                ORDER BY vec_distance_cosine(embedding, ?) ASC
                LIMIT ? OFFSET ?
                """,
                (embedding_bytes, embedding_bytes, params.limit, params.offset),
            )

            results = []
            for row in cursor.fetchall():
                results.append(
                    QueryResult(
                        file_path=row[0],
                        language=row[1],
                        content=row[2],
                        start_line=row[3],
                        end_line=row[4],
                        score=row[5],
                    )
                )

        return results

    def close(self) -> None:
        """Close the database connection (no-op, managed by CocoIndex)."""
        # Connection is managed by CocoIndex lifespan, nothing to do here
        pass
