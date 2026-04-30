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
