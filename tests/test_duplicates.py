"""CLI duplicate detection against a real, on-disk SQLite vector index."""

import json
import struct
from contextlib import closing
from pathlib import Path

import pytest
from cocoindex.connectors import sqlite
from typer.testing import CliRunner

from cocoindex_code import _duplicates, cli, settings


@pytest.fixture
def index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(tmp_path / "user"))
    settings.save_user_settings(settings.default_user_settings())
    settings.save_project_settings(tmp_path, settings.default_project_settings())
    path: Path = settings.target_sqlite_db_path(tmp_path)
    with closing(sqlite.connect(path, load_vec=True)) as db, db.transaction() as conn:
        conn.execute(
            "CREATE VIRTUAL TABLE code_chunks_vec USING vec0("
            "id INTEGER PRIMARY KEY, +file_path TEXT, language TEXT partition key, "
            "+content TEXT, +start_line INTEGER, +end_line INTEGER, embedding float[2])"
        )
        rows = [
            ("src/a.py", "python", 5, (2.0, 0.0)),
            ("src/b.py", "python", 5, (0.8, 0.6)),
            ("src/c.py", "python", 5, (0.28, 0.96)),
            ("src/short.py", "python", 2, (2.0, 0.0)),
            ("src/main.rs", "rust", 5, (2.0, 0.0)),
            ("test/z.py", "python", 5, (2.0, 0.0)),
            ("src/zero.py", "python", 5, (0.0, 0.0)),
        ]
        conn.executemany(
            "INSERT INTO code_chunks_vec VALUES (?, ?, ?, '', 1, ?, ?)",
            [
                (idx, file, language, end, struct.pack("ff", *vector))
                for idx, (file, language, end, vector) in enumerate(rows)
            ],
        )
    return path


def test_duplicate_groups_follow_transitive_edges_across_blocks(
    index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_duplicates, "_BLOCK_SIZE", 2)
    result = CliRunner().invoke(
        cli.app,
        ["duplicates", "--lang", "python", "--path", "src/*", "--threshold", "0.75", "--json"],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert [[chunk["file_path"] for chunk in group] for group in json.loads(result.output)] == [
        ["src/a.py", "src/b.py", "src/c.py"]
    ]


def test_exact_match_and_text_output(index: Path) -> None:
    result = CliRunner().invoke(cli.app, ["duplicates", "--threshold", "1", "--min-lines", "3"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert "Group 1 (3 chunks)" in result.output
    assert "src/a.py:1-5" in result.output
    assert "src/main.rs:1-5" in result.output
    assert "test/z.py:1-5" in result.output


def test_missing_index_does_not_create_database(index: Path) -> None:
    index.unlink()
    result = CliRunner().invoke(cli.app, ["duplicates"])
    assert result.exit_code == 1
    assert "Run `ccc index` first" in result.output
    assert not index.exists()


def test_nonfinite_threshold_is_rejected(index: Path) -> None:
    result = CliRunner().invoke(cli.app, ["duplicates", "--threshold", "nan"])
    assert result.exit_code == 2
    assert "must be finite" in result.output
