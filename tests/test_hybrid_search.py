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
