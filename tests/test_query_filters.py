"""Tests for the SQL layer of codebase search (issue #270).

These run against a real in-memory ``vec0`` table built with the same DDL the
indexer produces, so they exercise actual sqlite-vec behaviour rather than a
mock: which query shapes yield a usable ``distance``, and which yield NULL.
No embedding model is involved — vectors are supplied directly.
"""

from __future__ import annotations

import sqlite3
import struct

import pytest

from cocoindex_code.query import _checked, _full_scan_query, _knn_query

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
