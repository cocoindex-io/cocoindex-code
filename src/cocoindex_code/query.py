"""Query implementation for codebase search."""

from __future__ import annotations

import heapq
import sqlite3
from pathlib import Path
from typing import Any

from .schema import QueryResult
from .shared import EMBEDDER, QUERY_EMBED_PARAMS, SQLITE_DB


def _l2_to_score(distance: float) -> float:
    """Convert L2 distance to cosine similarity (exact for unit vectors)."""
    return 1.0 - distance * distance / 2.0


def _checked(rows: list[tuple[Any, ...]], query_shape: str) -> list[tuple[Any, ...]]:
    """Return *rows*, failing loudly if any of them carries a NULL distance.

    The hidden vec0 distance column is populated only by the KNN plan.
    A scalar distance can also be NULL on some filtered scan plans (#270).
    A missing distance cannot be ranked or converted to a similarity score.
    """
    bad = next((row for row in rows if row[5] is None), None)
    if bad is not None:
        raise RuntimeError(
            f"Vector index returned a row with no distance ({query_shape}, "
            f"file_path={bad[0]!r}). Please report this at "
            "https://github.com/cocoindex-io/cocoindex-code/issues along with "
            "the output of `ccc doctor`."
        )
    return rows


def _knn_query(
    conn: sqlite3.Connection,
    embedding_bytes: bytes,
    k: int,
    language: str | None = None,
) -> list[tuple[Any, ...]]:
    """Run a vec0 KNN query, optionally constrained to a language partition."""
    if language is not None:
        return _checked(
            conn.execute(
                """
                SELECT file_path, language, content, start_line, end_line, distance
                FROM code_chunks_vec
                WHERE embedding MATCH ? AND k = ? AND language = ?
                ORDER BY distance
                """,
                (embedding_bytes, k, language),
            ).fetchall(),
            f"knn language={language!r}",
        )
    return _checked(
        conn.execute(
            """
            SELECT file_path, language, content, start_line, end_line, distance
            FROM code_chunks_vec
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
            """,
            (embedding_bytes, k),
        ).fetchall(),
        "knn unfiltered",
    )


def _full_scan_query(
    conn: sqlite3.Connection,
    embedding_bytes: bytes,
    limit: int,
    offset: int,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
) -> list[tuple[Any, ...]]:
    """Full scan with SQL-level distance computation and filtering."""
    conditions: list[str] = []
    filter_params: list[str] = []

    if languages:
        placeholders = ",".join("?" for _ in languages)
        conditions.append(f"language IN ({placeholders})")
        filter_params.extend(languages)

    if paths:
        path_clauses = " OR ".join("file_path GLOB ?" for _ in paths)
        conditions.append(f"({path_clauses})")
        filter_params.extend(paths)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"""
        SELECT file_path, language, content, start_line, end_line,
               vec_distance_L2(embedding, ?) as distance
        FROM code_chunks_vec
        {where}
        ORDER BY distance
        LIMIT ? OFFSET ?
        """,
        (embedding_bytes, *filter_params, limit, offset),
    ).fetchall()
    if all(row[5] is not None for row in rows):
        return rows

    # Separate the vec0 scan from distance evaluation when the direct scan
    # returns NULL. Filter before materializing and fetch payloads after ranking
    # so the temporary table only holds matching IDs and vectors.
    return _checked(
        conn.execute(
            f"""
            WITH candidates AS MATERIALIZED (
                SELECT id, embedding FROM code_chunks_vec {where}
            ),
            ranked AS MATERIALIZED (
                SELECT id, vec_distance_L2(embedding, ?) AS l2
                FROM candidates
                ORDER BY l2
                LIMIT ? OFFSET ?
            )
            SELECT chunks.file_path, chunks.language, chunks.content,
                   chunks.start_line, chunks.end_line, ranked.l2
            FROM ranked
            JOIN code_chunks_vec AS chunks ON chunks.id = ranked.id
            ORDER BY ranked.l2
            """,
            (*filter_params, embedding_bytes, limit, offset),
        ).fetchall(),
        "materialized full scan",
    )


async def query_codebase(
    query: str,
    target_sqlite_db_path: Path,
    env: Any,
    limit: int = 10,
    offset: int = 0,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
) -> list[QueryResult]:
    """
    Perform vector similarity search using vec0 KNN index.

    Uses sqlite-vec's vec0 virtual table for indexed nearest-neighbor search.
    Language filtering uses vec0 partition keys for exact index-level filtering.
    Path filtering triggers a full scan with distance computation.
    """
    if not target_sqlite_db_path.exists():
        raise RuntimeError(
            f"Index database not found at {target_sqlite_db_path}. "
            "Please run a query with refresh_index=True first."
        )

    db = env.get_context(SQLITE_DB)
    embedder = env.get_context(EMBEDDER)
    query_params = env.get_context(QUERY_EMBED_PARAMS)

    # Generate query embedding.
    query_embedding = await embedder.embed(query, **query_params)

    embedding_bytes = query_embedding.astype("float32").tobytes()

    with db.readonly() as conn:
        if paths:
            rows = _full_scan_query(conn, embedding_bytes, limit, offset, languages, paths)
        elif not languages or len(languages) == 1:
            lang = languages[0] if languages else None
            rows = _knn_query(conn, embedding_bytes, limit + offset, lang)
        else:
            fetch_k = limit + offset
            rows = heapq.nsmallest(
                fetch_k,
                (
                    row
                    for lang in languages
                    for row in _knn_query(conn, embedding_bytes, fetch_k, lang)
                ),
                key=lambda r: r[5],
            )

    if not paths:
        rows = rows[offset:]

    return [
        QueryResult(
            file_path=file_path,
            language=language,
            content=content,
            start_line=start_line,
            end_line=end_line,
            score=_l2_to_score(distance),
        )
        for file_path, language, content, start_line, end_line, distance in rows
    ]
