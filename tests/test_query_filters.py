"""Tests for the SQL layer of codebase search (issue #270).

These run against a real in-memory ``vec0`` table built with the same DDL the
indexer produces, so they exercise actual sqlite-vec behaviour rather than a
mock: which query shapes yield a usable ``distance``, and which yield NULL.
No embedding model is involved — vectors are supplied directly.
"""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest

from cocoindex_code.query import (
    _checked,
    _full_scan_query,
    _knn_query,
    open_readonly_index,
    query_codebase_readonly,
)

DIM = 4

# Mirrors the vec0 table the indexer mounts: an INTEGER primary key, `language`
# as the partition key, the payload columns as auxiliary (`+`) columns, and the
# vector last.  See `indexer_main` in cocoindex_code/indexer.py.
_DDL = f"""
CREATE VIRTUAL TABLE "code_chunks_vec" USING vec0(
    id INTEGER primary key,
    +file_path TEXT,
    language TEXT partition key,
    +content TEXT,
    +start_line INTEGER,
    +end_line INTEGER,
    embedding float[{DIM}]
)
"""

_ROWS = [
    (0, "src/main.py", "python", "fibonacci", 1, 5, (1.0, 0.0, 0.0, 0.0)),
    (1, "src/util.py", "python", "parse csv", 1, 5, (0.0, 1.0, 0.0, 0.0)),
    (2, "lib/db.py", "python", "connect", 1, 5, (0.0, 0.0, 1.0, 0.0)),
    (3, "lib/api.rs", "rust", "handler", 1, 5, (0.0, 0.0, 0.0, 1.0)),
]


def _vec(values: tuple[float, ...]) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


@pytest.fixture
def conn() -> sqlite3.Connection:
    sqlite_vec = pytest.importorskip("sqlite_vec")
    c = sqlite3.connect(":memory:")
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    c.execute(_DDL)
    c.executemany(
        "INSERT INTO code_chunks_vec"
        "(id, file_path, language, content, start_line, end_line, embedding)"
        " VALUES (?,?,?,?,?,?,?)",
        [(*row[:6], _vec(row[6])) for row in _ROWS],
    )
    return c


def test_full_scan_query_with_path_filter_returns_usable_distances(
    conn: sqlite3.Connection,
) -> None:
    """`--path` filtering must produce real distances, not NULLs (issue #270)."""
    rows = _full_scan_query(conn, _vec((1.0, 0.0, 0.0, 0.0)), limit=10, offset=0, paths=["src/*"])

    assert [row[0] for row in rows] == ["src/main.py", "src/util.py"]
    assert all(isinstance(row[5], float) for row in rows)
    # Nearest first: main.py is the exact match.
    assert rows[0][5] == pytest.approx(0.0)


def test_full_scan_query_combines_language_and_path_filters(conn: sqlite3.Connection) -> None:
    rows = _full_scan_query(
        conn,
        _vec((0.0, 0.0, 1.0, 0.0)),
        limit=10,
        offset=0,
        languages=["python"],
        paths=["lib/*"],
    )

    assert [row[0] for row in rows] == ["lib/db.py"]
    assert rows[0][5] == pytest.approx(0.0)


def test_knn_query_returns_usable_distances(conn: sqlite3.Connection) -> None:
    unfiltered = _knn_query(conn, _vec((1.0, 0.0, 0.0, 0.0)), k=4)
    assert len(unfiltered) == 4
    assert all(isinstance(row[5], float) for row in unfiltered)

    partitioned = _knn_query(conn, _vec((0.0, 0.0, 0.0, 1.0)), k=4, language="rust")
    assert [row[0] for row in partitioned] == ["lib/api.rs"]


def test_bare_distance_column_is_null_outside_the_knn_plan(conn: sqlite3.Connection) -> None:
    """The one shape that yields NULL distances — the failure `_checked` guards.

    `distance` is a hidden vec0 column: sqlite-vec fills it in only under the
    KNN plan and returns NULL for it on a full scan.  Locking this in documents
    *why* the guard exists, and would catch sqlite-vec changing the contract.
    """
    rows = conn.execute(
        "SELECT file_path, language, content, start_line, end_line, distance "
        "FROM code_chunks_vec WHERE file_path GLOB 'src/*'"
    ).fetchall()

    assert rows, "expected the full scan to match rows"
    assert all(row[5] is None for row in rows)

    with pytest.raises(RuntimeError) as excinfo:
        _checked(rows, "knn language='python'")

    message = str(excinfo.value)
    assert "no distance" in message
    assert "knn language='python'" in message
    assert "src/main.py" in message
    assert "issues" in message


def test_checked_passes_through_rows_with_distances() -> None:
    rows = [("src/main.py", "python", "body", 1, 5, 0.5)]
    assert _checked(rows, "knn unfiltered") is rows


async def test_member_query_does_not_create_a_missing_index(tmp_path: Path) -> None:
    missing = tmp_path / "shared" / "target_sqlite.db"

    class _EmbedderMustNotRun:
        async def embed(self, _query: str, **_kwargs: object) -> None:
            pytest.fail("missing snapshot must fail before embedding")

    with pytest.raises(RuntimeError, match="team leader"):
        await query_codebase_readonly(
            query="anything",
            target_sqlite_db_path=missing,
            embedder=_EmbedderMustNotRun(),
            query_params={},
        )

    assert not missing.exists()
    assert not missing.parent.exists()


def _write_vec_snapshot(path: Path, *, first_content: str = "fibonacci") -> None:
    sqlite_vec = pytest.importorskip("sqlite_vec")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [(0, "src/main.py", "python", first_content, 1, 5, _ROWS[0][6]), *_ROWS[1:]]
    conn = sqlite3.connect(path)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute(_DDL)
        conn.executemany(
            "INSERT INTO code_chunks_vec"
            "(id, file_path, language, content, start_line, end_line, embedding)"
            " VALUES (?,?,?,?,?,?,?)",
            [(*row[:6], _vec(row[6])) for row in rows],
        )
        conn.commit()
    finally:
        conn.close()


def test_open_readonly_index_enforces_query_only(tmp_path: Path) -> None:
    snapshot = tmp_path / "shared" / "target_sqlite.db"
    _write_vec_snapshot(snapshot)

    db = open_readonly_index(snapshot)
    try:
        with db.readonly() as conn:
            query_only = conn.execute("PRAGMA query_only").fetchone()
            assert query_only is not None
            assert query_only[0] == 1
            with pytest.raises(sqlite3.OperationalError, match="readonly|read-only|query_only"):
                conn.execute("CREATE TABLE member_must_not_write (id INTEGER)")
    finally:
        db.close()

    with sqlite3.connect(snapshot) as verify:
        tables = {
            row[0]
            for row in verify.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "member_must_not_write" not in tables


def test_open_readonly_index_sees_atomically_replaced_snapshot(tmp_path: Path) -> None:
    published = tmp_path / "shared" / "target_sqlite.db"
    first = tmp_path / "shared" / "first.db"
    second = tmp_path / "shared" / "second.db"
    _write_vec_snapshot(first, first_content="fibonacci")
    _write_vec_snapshot(second, first_content="replacement snapshot")
    first.replace(published)

    first_handle = open_readonly_index(published)
    try:
        with first_handle.readonly() as conn:
            before = conn.execute("SELECT content FROM code_chunks_vec WHERE id = 0").fetchone()
        assert before == ("fibonacci",)
        second.replace(published)
        with first_handle.readonly() as conn:
            still_open = conn.execute("SELECT content FROM code_chunks_vec WHERE id = 0").fetchone()
        assert still_open == ("fibonacci",)
    finally:
        first_handle.close()

    next_handle = open_readonly_index(published)
    try:
        with next_handle.readonly() as conn:
            after = conn.execute("SELECT content FROM code_chunks_vec WHERE id = 0").fetchone()
        assert after == ("replacement snapshot",)
    finally:
        next_handle.close()
