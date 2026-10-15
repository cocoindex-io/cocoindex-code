"""Query implementation for codebase search."""

from __future__ import annotations

import heapq
import sqlite3
from pathlib import Path
from typing import Any

from cocoindex.connectors import sqlite as coco_sqlite
from cocoindex.connectors.sqlite import ManagedConnection

from .schema import QueryResult
from .shared import Embedder


def _l2_to_score(distance: float) -> float:
    """Convert L2 distance to cosine similarity (exact for unit vectors)."""
    return 1.0 - distance * distance / 2.0


def _checked(rows: list[tuple[Any, ...]], query_shape: str) -> list[tuple[Any, ...]]:
    """Return *rows*, failing loudly if any of them carries a NULL distance.

    ``code_chunks_vec`` is a vec0 virtual table whose ``distance`` is a *hidden*
    column: sqlite-vec populates it only under the KNN query plan and yields
    NULL for it on a plain full scan.  A NULL therefore means the plan we asked
    for is not the plan we got, which is a bug worth reporting rather than a
    result worth ranking.  Left unchecked, the NULL flows into ``_l2_to_score``
    and surfaces as an unrelated-looking ``TypeError: unsupported operand
    type(s) for *: 'NoneType' and 'NoneType'`` (issue #270).

    Only KNN queries need this guard: ``_full_scan_query`` computes its own
    ``vec_distance_L2(...)``, which works under any plan and raises (never
    returns NULL) on bad input.
    """
    bad = next((row for row in rows if row[5] is None), None)
    if bad is not None:
        raise RuntimeError(
            f"Vector index returned a row with no distance ({query_shape}, "
            f"file_path={bad[0]!r}) — the sqlite-vec KNN query plan was not "
            "used. Please report this at "
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
    params: list[Any] = [embedding_bytes]

    if languages:
        placeholders = ",".join("?" for _ in languages)
        conditions.append(f"language IN ({placeholders})")
        params.extend(languages)

    if paths:
        path_clauses = " OR ".join("file_path GLOB ?" for _ in paths)
        conditions.append(f"({path_clauses})")
        params.extend(paths)

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
    db: ManagedConnection,
    embedder: Embedder,
    query_params: dict[str, Any],
    limit: int = 10,
    offset: int = 0,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
) -> list[QueryResult]:
    """Perform vector similarity search using vec0 KNN index.

    Database and embedding dependencies are explicit so member-mode searches
    can use a short-lived read-only SQLite connection without constructing a
    CocoIndex app or opening its LMDB state database.
    """
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


def open_readonly_index(path: Path) -> ManagedConnection:
    """Open an existing index without creating or modifying any DB files."""
    if not path.is_file():
        raise RuntimeError(
            f"Shared index database not found at {path}. Indexing is disabled for this "
            "member runtime; ask the team leader to publish an index snapshot."
        )

    # `mode=ro` is enforced by SQLite itself. A fresh connection per request
    # also means an atomically replaced snapshot is observed on the next query
    # instead of keeping the old file handle cached in the daemon.
    uri = f"{path.resolve().as_uri()}?mode=ro"
    db: ManagedConnection | None = None
    try:
        db = coco_sqlite.connect(uri, uri=True, load_vec=True)
        with db.readonly() as conn:
            conn.execute("PRAGMA query_only = ON")
        return db
    except Exception as exc:
        if db is not None:
            db.close()
        raise RuntimeError(
            f"Unable to open shared index database read-only at {path}: {exc}"
        ) from exc


async def query_codebase_readonly(
    query: str,
    target_sqlite_db_path: Path,
    embedder: Embedder,
    query_params: dict[str, Any],
    limit: int = 10,
    offset: int = 0,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
) -> list[QueryResult]:
    """Search a published snapshot through a fresh read-only connection."""
    db = open_readonly_index(target_sqlite_db_path)
    try:
        return await query_codebase(
            query=query,
            db=db,
            embedder=embedder,
            query_params=query_params,
            limit=limit,
            offset=offset,
            languages=languages,
            paths=paths,
        )
    finally:
        db.close()
