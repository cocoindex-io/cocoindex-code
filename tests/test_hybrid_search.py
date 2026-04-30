import sqlite3


def test_import_hybrid_search():
    import cocoindex_code.hybrid_search as hs

    assert hasattr(hs, "reciprocal_rank_fusion")
    assert hasattr(hs, "keyword_search")


def test_rrf_shape():
    from cocoindex_code.hybrid_search import KeywordHit, reciprocal_rank_fusion

    # Vector results: two mock dicts
    vector_results = [
        {
            "file_path": "a.py",
            "start_line": 1,
            "end_line": 3,
            "content": "x",
            "language": "python",
            "score": 0.9,
        },
        {
            "file_path": "b.py",
            "start_line": 10,
            "end_line": 12,
            "content": "y",
            "language": "python",
            "score": 0.8,
        },
    ]

    keyword_results = [
        KeywordHit(file_path="a.py", content="x", start_line=1, end_line=3, score=1.2)
    ]

    fused = reciprocal_rank_fusion(
        vector_results=vector_results,
        keyword_results=keyword_results,
        limit=5,
    )
    assert isinstance(fused, list)
    assert len(fused) >= 1
    assert fused[0]["file_path"] == "a.py"


def test_ensure_fts_refreshes_same_row_count_content(tmp_path):
    from cocoindex_code.hybrid_search import ensure_fts_index, keyword_search

    db_path = tmp_path / "target_sqlite.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE code_chunks_vec (
                file_path TEXT,
                content TEXT,
                language TEXT,
                start_line INTEGER,
                end_line INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO code_chunks_vec VALUES (?, ?, ?, ?, ?)",
            ("a.py", "old_token", "python", 1, 1),
        )

    first = ensure_fts_index(db_path)
    assert first["rebuilt"] is True
    assert keyword_search(db_path, "old_token")

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE code_chunks_vec SET content = ?", ("new_token",))

    refreshed = ensure_fts_index(db_path)
    assert refreshed["rebuilt"] is True
    assert keyword_search(db_path, "new_token")

    clean = ensure_fts_index(db_path)
    assert clean["rebuilt"] is False


def test_keyword_search_accepts_multiple_path_prefixes(tmp_path):
    from cocoindex_code.hybrid_search import ensure_fts_index, keyword_search

    db_path = tmp_path / "target_sqlite.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE code_chunks_vec (
                file_path TEXT,
                content TEXT,
                language TEXT,
                start_line INTEGER,
                end_line INTEGER
            )
            """
        )
        conn.executemany(
            "INSERT INTO code_chunks_vec VALUES (?, ?, ?, ?, ?)",
            [
                ("src/app.py", "deploy spinner", "python", 1, 1),
                ("scripts/build.py", "deploy table", "python", 1, 1),
                ("docs/readme.md", "deploy logs", "markdown", 1, 1),
            ],
        )

    ensure_fts_index(db_path)
    hits = keyword_search(db_path, "deploy", path_prefixes=["src", "scripts"], limit=10)
    assert [hit.file_path for hit in hits] == ["scripts/build.py", "src/app.py"] or [
        hit.file_path for hit in hits
    ] == ["src/app.py", "scripts/build.py"]
    assert all(not hit.file_path.startswith("docs/") for hit in hits)


def test_ensure_fts_index_uses_process_cache_for_unchanged_db(tmp_path, monkeypatch):
    import cocoindex_code.hybrid_search as hs

    db_path = tmp_path / "target_sqlite.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE code_chunks_vec (
                file_path TEXT,
                content TEXT,
                language TEXT,
                start_line INTEGER,
                end_line INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO code_chunks_vec VALUES (?, ?, ?, ?, ?)",
            ("a.py", "cached_token", "python", 1, 1),
        )

    hs._ENSURE_FTS_CACHE.clear()
    first = hs.ensure_fts_index(db_path)
    assert first["rebuilt"] is True
    cache_key = db_path.resolve()
    cached = hs._ENSURE_FTS_CACHE[cache_key]

    def fail_connect(_db_path):
        raise AssertionError("ensure_fts_index should reuse the process cache for unchanged DBs")

    monkeypatch.setattr(hs, "_db_signature", lambda _path: cached[0])
    monkeypatch.setattr(hs, "_ensure_connection", fail_connect)
    second = hs.ensure_fts_index(db_path)
    assert second["rebuilt"] is False
    assert second["fts_rows"] == first["fts_rows"]


def test_ensure_fts_index_cache_tracks_wal_sidecar(tmp_path):
    import cocoindex_code.hybrid_search as hs

    db_path = tmp_path / "target_sqlite.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE code_chunks_vec (file_path TEXT, content TEXT, language TEXT, start_line INTEGER, end_line INTEGER)")
        conn.execute(
            "INSERT INTO code_chunks_vec VALUES (?, ?, ?, ?, ?)",
            ("a.py", "token", "python", 1, 1),
        )

    hs._ENSURE_FTS_CACHE.clear()
    hs.ensure_fts_index(db_path)
    cached = hs._ENSURE_FTS_CACHE[db_path.resolve()]
    base_sig = cached[0]

    wal_path = db_path.with_name(f"{db_path.name}-wal")
    wal_path.write_bytes(b"wal-data")

    changed_sig = hs._db_signature(db_path.resolve())
    assert changed_sig is not None
    assert changed_sig != base_sig
