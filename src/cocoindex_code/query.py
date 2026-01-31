"""Query implementation for codebase search."""

import sqlite3
from dataclasses import dataclass

import sqlite_vec

from .schema import QueryResult
from .shared import config, embedder


@dataclass
class QueryParams:
    """Parameters for querying the codebase."""

    query: str
    limit: int = 10
    offset: int = 0


class CodebaseQuerier:
    """Handles vector similarity queries against the index."""

    def __init__(self) -> None:
        self._conn: sqlite3.Connection | None = None

    def _get_connection(self) -> sqlite3.Connection:
        """Lazily establish database connection."""
        if self._conn is None:
            if not config.target_sqlite_db_path.exists():
                raise RuntimeError(
                    f"Index database not found at {config.target_sqlite_db_path}. "
                    "Run the 'update_index' tool first to create the index."
                )
            self._conn = sqlite3.connect(str(config.target_sqlite_db_path))
            # Load sqlite-vec extension
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
        return self._conn

    def query(self, params: QueryParams) -> list[QueryResult]:
        """
        Perform vector similarity search.

        Uses sqlite-vec's vec_distance_cosine for similarity scoring.
        """
        conn = self._get_connection()

        # Generate query embedding
        query_embedding = embedder.embed(params.query)

        # Convert to bytes for sqlite-vec (float32)
        embedding_bytes = query_embedding.astype("float32").tobytes()

        # Query using sqlite-vec
        # vec_distance_cosine returns distance (lower is better),
        # so we convert to similarity score (1 - distance)
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
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
