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


def _is_glob_pattern(path: str) -> bool:
    return any(ch in path for ch in "*?[")


def _path_matches(path: str, filters: list[str]) -> bool:
    from fnmatch import fnmatch

    for flt in filters:
        if _is_glob_pattern(flt):
            if fnmatch(path, flt):
                return True
            continue
        if path == flt or path.startswith(f"{flt}/"):
            return True
    return False


def _knn_query(
    conn: sqlite3.Connection,
    embedding_bytes: bytes,
    k: int,
    language: str | None = None,
) -> list[tuple[Any, ...]]:
    """Run a vec0 KNN query, optionally constrained to a language partition."""
    if language is not None:
        return conn.execute(
            """
            SELECT file_path, language, content, start_line, end_line, distance
            FROM code_chunks_vec
            WHERE embedding MATCH ? AND k = ? AND language = ?
            ORDER BY distance
            """,
            (embedding_bytes, k, language),
        ).fetchall()
    return conn.execute(
        """
        SELECT file_path, language, content, start_line, end_line, distance
        FROM code_chunks_vec
        WHERE embedding MATCH ? AND k = ?
        ORDER BY distance
        """,
        (embedding_bytes, k),
    ).fetchall()


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
    params: list[Any] = [embedding_bytes]

    if languages:
        placeholders = ",".join("?" for _ in languages)
        conditions.append(f"language IN ({placeholders})")
        params.extend(languages)

    if paths:
        path_clauses: list[str] = []
        for path in paths:
            if _is_glob_pattern(path):
                path_clauses.append("file_path GLOB ?")
                params.append(path)
            else:
                path_clauses.append("(file_path = ? OR file_path LIKE ?)")
                params.extend([path, f"{path}/%"])
        conditions.append(f"({' OR '.join(path_clauses)})")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    return conn.execute(
        f"""
        SELECT file_path, language, content, start_line, end_line,
               vec_distance_L2(embedding, ?) as distance
        FROM code_chunks_vec
        {where}
        ORDER BY distance
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()


def _indexed_path_query(
    conn: sqlite3.Connection,
    embedding_bytes: bytes,
    limit: int,
    offset: int,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
) -> list[tuple[Any, ...]]:
    """Use ANN retrieval first, then filter candidates by path in Python.

    Prefix-style path filters are common in MCP calls. Running vec_distance_L2
    over the whole table for these makes filtered search much slower than
    unfiltered search. This helper preserves low latency by overfetching from
    the vector index and only falling back to a full scan if the filtered
    candidate pool stays undersized.
    """
    if not paths:
        return []

    normalized_paths = [path.rstrip("/") for path in paths if path.strip()]
    if not normalized_paths:
        return []
    if any(_is_glob_pattern(path) for path in normalized_paths):
        return _full_scan_query(conn, embedding_bytes, limit, offset, languages, normalized_paths)

    target = limit + offset
    candidate_k = max(64, target * 8)
    max_candidate_k = max(256, target * 64)

    while True:
        if not languages or len(languages) == 1:
            lang = languages[0] if languages else None
            candidates = _knn_query(conn, embedding_bytes, candidate_k, lang)
        else:
            candidates = heapq.nsmallest(
                candidate_k,
                (
                    row
                    for lang in languages
                    for row in _knn_query(conn, embedding_bytes, candidate_k, lang)
                ),
                key=lambda r: r[5],
            )

        filtered = [row for row in candidates if _path_matches(str(row[0]), normalized_paths)]
        if len(filtered) >= target:
            return filtered[offset : offset + limit]
        if candidate_k >= max_candidate_k or len(candidates) < candidate_k:
            return _full_scan_query(
                conn,
                embedding_bytes,
                limit,
                offset,
                languages,
                normalized_paths,
            )
        candidate_k = min(candidate_k * 2, max_candidate_k)


async def query_codebase(
    query: str,
    target_sqlite_db_path: Path,
    env: Any,
    limit: int = 10,
    offset: int = 0,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    query_embedding: Any | None = None,
) -> list[QueryResult]:
    """
    Perform vector similarity search using vec0 KNN index.

    Uses sqlite-vec's vec0 virtual table for indexed nearest-neighbor search.
    Language filtering uses vec0 partition keys for exact index-level filtering.
    Path-prefix filtering uses ANN overfetch + in-memory filtering to avoid
    full-table distance scans on common MCP usage patterns. True glob filters
    still fall back to a full scan.
    """
    if not target_sqlite_db_path.exists():
        raise RuntimeError(
            f"Index database not found at {target_sqlite_db_path}. "
            "Please run a query with refresh_index=True first."
        )

    db = env.get_context(SQLITE_DB)
    embedder = env.get_context(EMBEDDER)
    query_params = env.get_context(QUERY_EMBED_PARAMS)

    # Generate query embedding unless already provided by the caller.
    if query_embedding is None:
        query_embedding = await embedder.embed(query, **query_params)

    embedding_bytes = query_embedding.astype("float32").tobytes()

    with db.readonly() as conn:
        if paths:
            rows = _indexed_path_query(conn, embedding_bytes, limit, offset, languages, paths)
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
