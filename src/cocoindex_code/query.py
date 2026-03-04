"""Query implementation for codebase search."""

import fnmatch

import cocoindex as coco

from .config import config
from .schema import QueryResult
from .shared import SQLITE_DB, embedder

# Over-fetch multiplier when post-filtering is needed
_FILTER_OVERFETCH = 5
_FILTER_MIN_K = 200


async def query_codebase(
    query: str,
    limit: int = 10,
    offset: int = 0,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
) -> list[QueryResult]:
    """
    Perform vector similarity search using vec0 KNN index.

    Uses sqlite-vec's vec0 virtual table for indexed nearest-neighbor search.
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

    # vec0 KNN queries don't support arbitrary WHERE/OFFSET, so we
    # over-fetch when post-filtering is needed and apply filters in Python.
    needs_post_filter = bool(languages or paths)
    if needs_post_filter:
        fetch_k = max((limit + offset) * _FILTER_OVERFETCH, _FILTER_MIN_K)
    else:
        fetch_k = limit + offset

    # Query using vec0 KNN index with readonly transaction.
    # vec0 returns L2 distance; for normalized embeddings the ranking is
    # identical to cosine distance.  Convert to cosine similarity via
    # cos_sim = 1 - L2² / 2  (exact for unit vectors).
    with db.value.readonly() as conn:
        cursor = conn.execute(
            """
            SELECT
                file_path,
                language,
                content,
                start_line,
                end_line,
                distance
            FROM code_chunks_vec
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
            """,
            (embedding_bytes, fetch_k),
        )
        rows = cursor.fetchall()

    language_set = set(languages) if languages else None
    results: list[QueryResult] = []

    for file_path, language, content, start_line, end_line, distance in rows:
        if language_set and language not in language_set:
            continue
        if paths and not any(fnmatch.fnmatch(file_path, p) for p in paths):
            continue
        results.append(
            QueryResult(
                file_path=file_path,
                language=language,
                content=content,
                start_line=start_line,
                end_line=end_line,
                score=1.0 - distance * distance / 2.0,
            )
        )

    return results[offset : offset + limit]
