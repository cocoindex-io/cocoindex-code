"""Query implementation for codebase search."""

import cocoindex as coco

from .config import config
from .schema import QueryResult
from .shared import SQLITE_DB, embedder


async def query_codebase(
    query: str,
    limit: int = 10,
    offset: int = 0,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
) -> list[QueryResult]:
    """
    Perform vector similarity search.

    Uses sqlite-vec's vec_distance_cosine for similarity scoring.
    Optionally filters by language(s) and/or file path glob pattern(s).
    """
    if not config.target_sqlite_db_path.exists():
        raise RuntimeError(
            f"Index database not found at {config.target_sqlite_db_path}. "
            "Please run a query with refresh_index=True first."
        )

    # Get the database connection from CocoIndex environment
    coco_env = await coco.default_env()
    db = coco_env.get_context(SQLITE_DB)

    # Generate query embedding — use embed_query if available (supports asymmetric
    # prompting for models like nomic-embed-code that use different prefixes for
    # queries vs indexed documents).
    if hasattr(embedder, "embed_query"):
        query_embedding = await embedder.embed_query(query)
    else:
        query_embedding = await embedder.embed(query)

    # Convert to bytes for sqlite-vec (float32)
    embedding_bytes = query_embedding.astype("float32").tobytes()

    # Build WHERE clause for optional filters
    conditions: list[str] = []
    filter_params: list[object] = []

    if languages:
        placeholders = ", ".join("?" for _ in languages)
        conditions.append(f"language IN ({placeholders})")
        filter_params.extend(languages)

    if paths:
        glob_clauses = " OR ".join("file_path GLOB ?" for _ in paths)
        conditions.append(f"({glob_clauses})")
        filter_params.extend(paths)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Parameter order must match SQL placeholder positions:
    # 1) embedding in SELECT, 2) filter params in WHERE,
    # 3) embedding in ORDER BY, 4) limit, 5) offset
    params: list[object] = [embedding_bytes, *filter_params, embedding_bytes, limit, offset]

    # Query using sqlite-vec with readonly transaction
    # vec_distance_cosine returns distance (lower is better),
    # so we convert to similarity score (1 - distance)
    with db.value.readonly() as conn:
        cursor = conn.execute(
            f"""
            SELECT
                file_path,
                language,
                content,
                start_line,
                end_line,
                (1.0 - vec_distance_cosine(embedding, ?)) as score
            FROM code_chunks
            {where_clause}
            ORDER BY vec_distance_cosine(embedding, ?) ASC
            LIMIT ? OFFSET ?
            """,
            params,
        )

        return [
            QueryResult(
                file_path=row[0],
                language=row[1],
                content=row[2],
                start_line=row[3],
                end_line=row[4],
                score=row[5],
            )
            for row in cursor.fetchall()
        ]
