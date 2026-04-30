from __future__ import annotations

import sqlite3

from cocoindex_code.query import _full_scan_query, _indexed_path_query, _path_matches


def test_path_matches_prefix_and_glob() -> None:
    assert _path_matches("src/app/main.py", ["src"])
    assert _path_matches("scripts/build.sh", ["scripts/*.sh"])
    assert not _path_matches("docs/readme.md", ["src", "scripts/*.sh"])


def test_full_scan_query_treats_plain_paths_as_prefixes() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE code_chunks_vec (
            file_path TEXT,
            language TEXT,
            content TEXT,
            start_line INTEGER,
            end_line INTEGER,
            embedding BLOB
        )
        """
    )
    conn.executemany(
        "INSERT INTO code_chunks_vec VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("src/a.py", "python", "a", 1, 1, b"x"),
            ("scripts/b.py", "python", "b", 1, 1, b"x"),
            ("docs/c.md", "markdown", "c", 1, 1, b"x"),
        ],
    )
    conn.create_function("vec_distance_L2", 2, lambda _a, _b: 0.0)

    rows = _full_scan_query(conn, b"embed", 10, 0, paths=["src", "scripts"])
    assert [row[0] for row in rows] == ["src/a.py", "scripts/b.py"]


def test_indexed_path_query_filters_ann_candidates_without_full_scan(monkeypatch) -> None:
    calls: list[tuple[int, str | None]] = []

    def fake_knn_query(_conn, _embedding_bytes, k: int, language: str | None = None):
        calls.append((k, language))
        return [
            ("src/a.py", "python", "a", 1, 1, 0.01),
            ("scripts/b.py", "python", "b", 2, 2, 0.02),
            ("docs/c.md", "markdown", "c", 3, 3, 0.03),
        ]

    monkeypatch.setattr("cocoindex_code.query._knn_query", fake_knn_query)

    rows = _indexed_path_query(
        sqlite3.connect(":memory:"),
        b"embed",
        limit=2,
        offset=0,
        languages=None,
        paths=["src", "scripts"],
    )
    assert [row[0] for row in rows] == ["src/a.py", "scripts/b.py"]
    assert calls


def test_indexed_path_query_falls_back_to_exact_scan_for_sparse_prefixes(monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    exact_calls: list[tuple[int, int, list[str] | None]] = []

    def fake_knn_query(_conn, _embedding_bytes, k: int, language: str | None = None):
        return [
            ("src/a.py", "python", "a", 1, 1, 0.01),
            ("docs/c.md", "markdown", "c", 3, 3, 0.03),
        ]

    def fake_full_scan_query(
        _conn,
        _embedding_bytes,
        limit: int,
        offset: int,
        languages=None,
        paths=None,
    ):
        exact_calls.append((limit, offset, paths))
        return [
            ("src/a.py", "python", "a", 1, 1, 0.01),
            ("src/deep/b.py", "python", "b", 2, 2, 0.02),
        ]

    monkeypatch.setattr("cocoindex_code.query._knn_query", fake_knn_query)
    monkeypatch.setattr("cocoindex_code.query._full_scan_query", fake_full_scan_query)

    rows = _indexed_path_query(conn, b"embed", limit=2, offset=0, paths=["src"])
    assert [row[0] for row in rows] == ["src/a.py", "src/deep/b.py"]
    assert exact_calls == [(2, 0, ["src"])]


def test_indexed_path_query_globs_use_exact_scan(monkeypatch) -> None:
    exact_calls: list[list[str] | None] = []

    def fake_full_scan_query(
        _conn,
        _embedding_bytes,
        limit: int,
        offset: int,
        languages=None,
        paths=None,
    ):
        exact_calls.append(paths)
        return [("scripts/build.py", "python", "b", 1, 1, 0.01)]

    monkeypatch.setattr("cocoindex_code.query._full_scan_query", fake_full_scan_query)

    rows = _indexed_path_query(
        sqlite3.connect(":memory:"),
        b"embed",
        limit=1,
        offset=0,
        paths=["scripts/*.py"],
    )
    assert [row[0] for row in rows] == ["scripts/build.py"]
    assert exact_calls == [["scripts/*.py"]]
