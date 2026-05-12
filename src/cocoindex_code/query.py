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


def _knn_query(
    conn: sqlite3.Connection,
    embedding_bytes: bytes,
    k: int,
    language: str | None = None,
    repo_key: str | None = None,
    has_repo_key: bool = False,
) -> list[tuple[Any, ...]]:
    """Run a vec0 KNN query, optionally constrained to a language partition."""
    conditions = ["embedding MATCH ?", "k = ?"]
    params: list[Any] = [embedding_bytes, k]
    if repo_key is not None:
        conditions.append("repo_key = ?")
        params.append(repo_key)
    if language is not None:
        conditions.append("language = ?")
        params.append(language)

    repo_key_select = "repo_key" if has_repo_key else "NULL"
    return conn.execute(
        f"""
        SELECT file_path, {repo_key_select} as repo_key,
               language, content, start_line, end_line, distance
        FROM code_chunks_vec
        WHERE {" AND ".join(conditions)}
        ORDER BY distance
        """,
        params,
    ).fetchall()


def _full_scan_query(
    conn: sqlite3.Connection,
    embedding_bytes: bytes,
    limit: int,
    offset: int,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    repo_keys: list[str] | None = None,
) -> list[tuple[Any, ...]]:
    """Full scan with SQL-level distance computation and filtering."""
    conditions: list[str] = []
    params: list[Any] = [embedding_bytes]

    has_repo_key = _table_has_column(conn, "code_chunks_vec", "repo_key")

    if languages:
        placeholders = ",".join("?" for _ in languages)
        conditions.append(f"language IN ({placeholders})")
        params.extend(languages)

    if repo_keys:
        if has_repo_key:
            placeholders = ",".join("?" for _ in repo_keys)
            conditions.append(f"repo_key IN ({placeholders})")
            params.extend(repo_keys)
        else:
            repo_key_paths = [
                f"{repo_key.rstrip('/')}/*" for repo_key in repo_keys if repo_key != "."
            ]
            paths = [*(paths or []), *repo_key_paths] or paths

    if paths:
        path_clauses = " OR ".join("file_path GLOB ?" for _ in paths)
        conditions.append(f"({path_clauses})")
        params.extend(paths)

    repo_key_select = "repo_key" if has_repo_key else "NULL as repo_key"
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    return conn.execute(
        f"""
        SELECT file_path, {repo_key_select}, language, content, start_line, end_line,
               vec_distance_L2(embedding, ?) as distance
        FROM code_chunks_vec
        {where}
        ORDER BY distance
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()


def _table_has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return any(row[1] == column_name for row in conn.execute(f"PRAGMA table_info({table_name})"))


def _repo_key_candidates(repo_keys: list[str] | None) -> list[str | None]:
    if repo_keys:
        return list(repo_keys)
    return [None]


def _language_candidates(languages: list[str] | None) -> list[str | None]:
    if languages:
        return list(languages)
    return [None]


async def query_codebase(
    query: str,
    target_sqlite_db_path: Path,
    env: Any,
    limit: int = 10,
    offset: int = 0,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    repo_keys: list[str] | None = None,
) -> list[QueryResult]:
    """
    Perform vector similarity search using vec0 KNN index.

    Uses sqlite-vec's vec0 virtual table for indexed nearest-neighbor search.
    Language filtering uses vec0 partition keys for exact index-level filtering.
    Path filtering triggers a full scan with distance computation.
    Repo-key filtering uses the vec0 partition key when available, and
    falls back to equivalent path filters for older indexes.
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
        has_repo_key = _table_has_column(conn, "code_chunks_vec", "repo_key")
        if paths:
            rows = _full_scan_query(
                conn, embedding_bytes, limit, offset, languages, paths, repo_keys
            )
        elif repo_keys and not has_repo_key:
            rows = _full_scan_query(
                conn, embedding_bytes, limit, offset, languages, None, repo_keys
            )
        elif (not languages or len(languages) == 1) and (not repo_keys or len(repo_keys) == 1):
            lang = languages[0] if languages else None
            repo_key = repo_keys[0] if repo_keys else None
            rows = _knn_query(conn, embedding_bytes, limit + offset, lang, repo_key, has_repo_key)
        else:
            fetch_k = limit + offset
            rows = heapq.nsmallest(
                fetch_k,
                (
                    row
                    for repo_key in _repo_key_candidates(repo_keys)
                    for lang in _language_candidates(languages)
                    for row in _knn_query(
                        conn, embedding_bytes, fetch_k, lang, repo_key, has_repo_key
                    )
                ),
                key=lambda r: r[6],
            )

    if not paths and not (repo_keys and not has_repo_key):
        rows = rows[offset:]

    return [
        QueryResult(
            file_path=file_path,
            repo_key=repo_key,
            language=language,
            content=content,
            start_line=start_line,
            end_line=end_line,
            score=_l2_to_score(distance),
        )
        for file_path, repo_key, language, content, start_line, end_line, distance in rows
    ]
