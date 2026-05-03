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


_RRF_K = 60  # Standard RRF constant (Cormack et al., 2009)


def _keyword_query(
    conn: sqlite3.Connection,
    keywords: list[str],
    limit: int,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
) -> list[tuple[str, str, str, int, int, int]]:
    """Keyword search using INSTR for term matching. Returns rows with match count."""
    conditions: list[str] = []
    params: list[Any] = []

    # Count keyword matches in content (case-insensitive via LOWER)
    match_expr = " + ".join(
        f"(CASE WHEN INSTR(LOWER(content), LOWER(?)) > 0 THEN 1 ELSE 0 END)"
        for _ in keywords
    )
    params.extend(keywords)

    if languages:
        placeholders = ",".join("?" for _ in languages)
        conditions.append(f"language IN ({placeholders})")
        params.extend(languages)

    if paths:
        path_clauses = " OR ".join("file_path GLOB ?" for _ in paths)
        conditions.append(f"({path_clauses})")
        params.extend(paths)

    if exclude_paths:
        exclude_clauses = " AND ".join("file_path NOT GLOB ?" for _ in exclude_paths)
        conditions.append(f"({exclude_clauses})")
        params.extend(exclude_paths)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    return conn.execute(
        f"""
        SELECT file_path, language, content, start_line, end_line,
               ({match_expr}) as match_count
        FROM code_chunks_vec
        {where}
        HAVING match_count > 0
        ORDER BY match_count DESC
        LIMIT ?
        """,
        params,
    ).fetchall()


def _fuse_rrf(
    vector_results: list[QueryResult],
    keyword_results: list[tuple[str, str, str, int, int, int]],
    limit: int,
) -> list[QueryResult]:
    """Fuse vector and keyword results using Reciprocal Rank Fusion."""
    scores: dict[str, float] = {}
    vector_map: dict[str, QueryResult] = {}
    keyword_map: dict[str, tuple] = {}

    # Score vector results by rank
    for rank, r in enumerate(vector_results, start=1):
        key = f"{r.file_path}:{r.start_line}"
        scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
        vector_map[key] = r

    # Score keyword results by rank
    for rank, row in enumerate(keyword_results, start=1):
        file_path, language, content, start_line, end_line, match_count = row
        key = f"{file_path}:{start_line}"
        scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
        if key not in keyword_map:
            keyword_map[key] = row

    # Consensus boost: items in both lists get a small bonus
    consensus = set(vector_map.keys()) & set(keyword_map.keys())
    for key in consensus:
        scores[key] += 0.003

    # Build final results sorted by RRF score
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    results: list[QueryResult] = []
    for key, rrf_score in ranked[:limit]:
        if key in vector_map:
            r = vector_map[key]
            results.append(QueryResult(
                file_path=r.file_path, language=r.language, content=r.content,
                start_line=r.start_line, end_line=r.end_line, score=rrf_score,
            ))
        elif key in keyword_map:
            fp, lang, content, sl, el, _ = keyword_map[key]
            results.append(QueryResult(
                file_path=fp, language=lang, content=content,
                start_line=sl, end_line=el, score=rrf_score,
            ))
    return results


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
    exclude_paths: list[str] | None = None,
) -> list[tuple[Any, ...]]:
    """Full scan with SQL-level distance computation and filtering."""
    conditions: list[str] = []
    params: list[Any] = [embedding_bytes]

    if languages:
        placeholders = ",".join("?" for _ in languages)
        conditions.append(f"language IN ({placeholders})")
        params.extend(languages)

    if paths:
        path_clauses = " OR ".join("file_path GLOB ?" for _ in paths)
        conditions.append(f"({path_clauses})")
        params.extend(paths)

    if exclude_paths:
        exclude_clauses = " AND ".join("file_path NOT GLOB ?" for _ in exclude_paths)
        conditions.append(f"({exclude_clauses})")
        params.extend(exclude_paths)

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


async def query_codebase(
    query: str,
    target_sqlite_db_path: Path,
    env: Any,
    limit: int = 10,
    offset: int = 0,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    mode: str = "semantic",
) -> list[QueryResult]:
    """
    Perform codebase search.

    Modes:
    - "semantic" (default): vector similarity search via vec0 KNN index
    - "hybrid": combines vector + keyword search with Reciprocal Rank Fusion
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
        if paths or exclude_paths:
            rows = _full_scan_query(conn, embedding_bytes, limit, offset, languages, paths, exclude_paths)
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

    if not paths and not exclude_paths:
        rows = rows[offset:]

    vector_results = [
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

    if mode == "hybrid":
        # Extract keywords from query (simple tokenization)
        keywords = [w for w in query.lower().split() if len(w) >= 3]
        if keywords:
            with db.readonly() as conn:
                keyword_rows = _keyword_query(
                    conn, keywords, limit * 2, languages, paths, exclude_paths
                )
            return _fuse_rrf(vector_results, keyword_rows, limit)

    return vector_results

