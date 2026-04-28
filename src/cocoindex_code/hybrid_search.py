"""Hybrid (vector + BM25) search over the cocoindex sqlite-vec index.

Why
---
Our default MCP ``search`` tool uses pure vector similarity via
``cocoindex_code.client.search``. Pure vector retrieval misses exact-match
queries like ``@injectable()``, ``SIGNAL_GATEWAY_PORT``, or verbatim API names
where embedding similarity under-weights surface form. A BM25 keyword index
trivially wins on those.

We borrow the pattern from ``cocoindex-io/awesome-cocoindex``'s ``coco-search``
(hybrid retrieval with reciprocal rank fusion) without the hard dependency on
that package.

How
---
1. ``ensure_fts_index`` builds an FTS5 sidecar table in the **same** sqlite DB
   that cocoindex writes to (``target_sqlite.db``). The table mirrors the
   ``content`` + ``file_path`` columns of ``code_chunks_vec``, so it stays
   readable even if cocoindex never touches FTS5.

2. ``keyword_search`` runs a BM25 query.

3. ``reciprocal_rank_fusion`` merges vector results (from the standard MCP
   search tool) with the BM25 results. RRF is parameter-free and robust:
   ``score(doc) = Σ 1 / (k + rank_i(doc))`` where k = 60 (Cormack 2009).

FTS5 rebuild is incremental — we compare a cheap content signature and only
rebuild when the vector table has actually changed.
"""

from __future__ import annotations

import logging
import sqlite3
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)


_RRF_K = 60  # Cormack et al. standard constant.


class VectorResultDict(TypedDict, total=False):
    """Result from vector search."""

    file_path: str
    start_line: int
    end_line: int
    content: str
    language: str | None
    score: float


class HybridResultDict(TypedDict):
    """Result from hybrid (vector + keyword) search."""

    file_path: str
    start_line: int
    end_line: int
    content: str
    language: str | None
    score: float
    hybrid_score: float
    sources: list[str]


@dataclass(frozen=True)
class KeywordHit:
    file_path: str
    content: str
    start_line: int
    end_line: int
    score: float  # BM25 score — higher is better


class _ContentSignature:
    """Streaming aggregate for source rows used to decide FTS invalidation."""

    def __init__(self) -> None:
        self.checksum = 1

    def step(self, *values: object) -> None:
        payload = "\x1f".join("" if value is None else str(value) for value in values)
        self.checksum = zlib.adler32(payload.encode("utf-8", errors="replace"), self.checksum)

    def finalize(self) -> str:
        return str(self.checksum)


def _ensure_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.create_aggregate("coco_content_signature", 6, _ContentSignature)
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        # Plain sqlite fixtures and platforms without sqlite-vec still support
        # the rowid table path below.
        pass
    return conn


def _columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    return {str(row[1]) for row in cur.execute(f"PRAGMA table_info({table})")}


def _source_select_sql(cur: sqlite3.Cursor) -> str:
    try:
        cols = _columns(cur, "code_chunks_vec")
        id_col = "id" if "id" in cols else "rowid"
        return f"""
            SELECT file_path, content, language, start_line, end_line, {id_col} AS vec_rowid
            FROM code_chunks_vec
        """
    except sqlite3.OperationalError:
        return """
            SELECT a.value00 AS file_path,
                   a.value01 AS content,
                   NULL AS language,
                   a.value02 AS start_line,
                   a.value03 AS end_line,
                   r.rowid AS vec_rowid
            FROM code_chunks_vec_rowids r
            JOIN code_chunks_vec_auxiliary a ON r.rowid = a.rowid
        """


def _ordered_source_sql(cur: sqlite3.Cursor) -> str:
    return f"""
        SELECT file_path, content, language, start_line, end_line, vec_rowid
        FROM ({_source_select_sql(cur)})
        ORDER BY file_path, start_line, end_line, vec_rowid
    """


def _source_rows(cur: sqlite3.Cursor) -> list[sqlite3.Row]:
    return cur.execute(_ordered_source_sql(cur)).fetchall()


def _source_stats(cur: sqlite3.Cursor) -> tuple[int, str]:
    row = cur.execute(
        f"""
        SELECT COUNT(*) AS row_count,
               COALESCE(
                   coco_content_signature(
                       file_path, content, language, start_line, end_line, vec_rowid
                   ),
                   '1'
               ) AS signature
        FROM ({_ordered_source_sql(cur)})
        """
    ).fetchone()
    return int(row["row_count"]), str(row["signature"])


def ensure_fts_index(db_path: Path, *, force_rebuild: bool = False) -> dict[str, int | str | bool]:
    """Create or refresh an FTS5 mirror of ``code_chunks_vec``.

    Returns ``{"vec_rows": N, "fts_rows": N, "rebuilt": bool, "signature": S}``.
    Safe to call repeatedly. By default it rebuilds only when a content
    signature changes, so same-row-count edits still refresh keyword search.
    """
    logger.debug(f"Ensuring FTS5 index in {db_path}")
    conn = _ensure_connection(db_path)
    try:
        cur = conn.cursor()
        # Sanity-check the source table exists. We don't create it — that's
        # cocoindex-code's job. Calling this before the index is populated
        # would just create an empty FTS table, which is harmless.
        tables = {
            row[0]
            for row in cur.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            )
        }
        if "code_chunks_vec" not in tables:
            logger.debug("code_chunks_vec table not found, FTS index cannot be created yet")
            return {"vec_rows": 0, "fts_rows": 0, "rebuilt": False}

        cur.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS code_chunks_fts USING fts5(
                file_path,
                content,
                language UNINDEXED,
                start_line UNINDEXED,
                end_line UNINDEXED,
                vec_rowid UNINDEXED,
                tokenize = 'porter unicode61'
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS code_chunks_fts_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

        vec_rows, signature = _source_stats(cur)
        fts_rows = cur.execute("SELECT COUNT(*) FROM code_chunks_fts").fetchone()[0]
        stored_signature = cur.execute(
            "SELECT value FROM code_chunks_fts_meta WHERE key = 'source_signature'"
        ).fetchone()
        if (
            not force_rebuild
            and vec_rows == fts_rows
            and stored_signature is not None
            and stored_signature["value"] == signature
        ):
            logger.debug(f"FTS index up-to-date: {vec_rows} rows")
            return {
                "vec_rows": vec_rows,
                "fts_rows": fts_rows,
                "rebuilt": False,
                "signature": signature,
            }

        logger.info("Rebuilding FTS index: rows/signature changed")
        source_rows = _source_rows(cur)
        cur.execute("DELETE FROM code_chunks_fts")
        cur.executemany(
            """
            INSERT INTO code_chunks_fts
                (file_path, content, language, start_line, end_line, vec_rowid)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["file_path"],
                    row["content"],
                    row["language"],
                    row["start_line"],
                    row["end_line"],
                    row["vec_rowid"],
                )
                for row in source_rows
            ],
        )
        cur.execute(
            """
            INSERT INTO code_chunks_fts_meta (key, value)
            VALUES ('source_signature', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (signature,),
        )
        conn.commit()
        fts_rows = cur.execute("SELECT COUNT(*) FROM code_chunks_fts").fetchone()[0]
        return {
            "vec_rows": vec_rows,
            "fts_rows": fts_rows,
            "rebuilt": True,
            "signature": signature,
        }
    finally:
        conn.close()


def _quote_fts(query: str) -> str:
    """Wrap each whitespace-separated term in double quotes so FTS5 treats
    special characters (@, {}, dots, colons) literally rather than as operators.
    """
    terms = [t for t in query.split() if t]
    if not terms:
        return ""
    quoted = [t.replace('"', '""') for t in terms]
    return " ".join(f'"{t}"' for t in quoted)


def keyword_search(
    db_path: Path,
    query: str,
    *,
    limit: int = 10,
    path_prefix: str | None = None,
    language: str | None = None,
) -> list[KeywordHit]:
    """Run a BM25 query against the FTS5 sidecar table."""
    fts_query = _quote_fts(query)
    if not fts_query:
        return []

    conn = _ensure_connection(db_path)
    try:
        sql = (
            "SELECT file_path, content, language, start_line, end_line, "
            "       bm25(code_chunks_fts) AS score "
            "FROM code_chunks_fts "
            "WHERE code_chunks_fts MATCH ?"
        )
        params: list[object] = [fts_query]
        if path_prefix:
            sql += " AND file_path LIKE ?"
            params.append(f"{path_prefix}%")
        if language:
            sql += " AND language = ?"
            params.append(language)
        # bm25() returns lower is better; invert so higher = better for fusion.
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [
            KeywordHit(
                file_path=row["file_path"],
                content=row["content"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                score=-float(row["score"]),
            )
            for row in rows
        ]
    except sqlite3.OperationalError:
        # Missing FTS table or bad query syntax — return empty, let caller fall
        # back to vector-only.
        return []
    finally:
        conn.close()


def reciprocal_rank_fusion(
    *,
    vector_results: list[VectorResultDict],
    keyword_results: list[KeywordHit],
    k: int = _RRF_K,
    limit: int = 10,
) -> list[HybridResultDict]:
    """Merge ranked lists via reciprocal rank fusion.

    Returns dicts shaped like the vector MCP tool's ``results`` entries, with
    an added ``hybrid_score`` + ``sources`` field so callers can reason about
    which retriever contributed.
    """
    scored: dict[tuple[str, int], dict] = {}

    def _key(doc: dict | KeywordHit) -> tuple[str, int]:
        if isinstance(doc, KeywordHit):
            return (doc.file_path, doc.start_line)
        return (doc["file_path"], doc["start_line"])

    for rank, doc in enumerate(vector_results, start=1):
        key = _key(doc)
        entry = dict(doc)
        entry["hybrid_score"] = 1 / (k + rank)
        entry["sources"] = ["vector"]
        scored[key] = entry

    for rank, kw in enumerate(keyword_results, start=1):
        key = _key(kw)
        boost = 1 / (k + rank)
        existing = scored.get(key)
        if existing is not None:
            existing["hybrid_score"] += boost
            existing["sources"].append("keyword")
            continue
        scored[key] = {
            "file_path": kw.file_path,
            "language": None,
            "content": kw.content,
            "start_line": kw.start_line,
            "end_line": kw.end_line,
            "score": kw.score,
            "hybrid_score": boost,
            "sources": ["keyword"],
        }

    fused = sorted(scored.values(), key=lambda d: d["hybrid_score"], reverse=True)
    return fused[:limit]
