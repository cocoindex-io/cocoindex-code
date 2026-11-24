"""CLI duplicate detection against a real, on-disk SQLite vector index."""

import json
import struct
import subprocess
import sys
import textwrap
from contextlib import closing
from pathlib import Path

import pytest
from cocoindex.connectors import sqlite
from typer.testing import CliRunner

from cocoindex_code import _duplicates, cli, settings

# (file_path, language, start_line, end_line, embedding)
_Row = tuple[str, str, int, int, tuple[float, float]]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(tmp_path / "user"))
    settings.save_user_settings(settings.default_user_settings())
    settings.save_project_settings(tmp_path, settings.default_project_settings())
    return tmp_path


def _write_index(project: Path, rows: list[_Row]) -> Path:
    path: Path = settings.target_sqlite_db_path(project)
    with closing(sqlite.connect(path, load_vec=True)) as db, db.transaction() as conn:
        conn.execute(
            "CREATE VIRTUAL TABLE code_chunks_vec USING vec0("
            "id INTEGER PRIMARY KEY, +file_path TEXT, language TEXT partition key, "
            "+content TEXT, +start_line INTEGER, +end_line INTEGER, embedding float[2])"
        )
        conn.executemany(
            "INSERT INTO code_chunks_vec VALUES (?, ?, ?, '', ?, ?, ?)",
            [
                (idx, file, language, start, end, struct.pack("ff", *vector))
                for idx, (file, language, start, end, vector) in enumerate(rows)
            ],
        )
    return path


@pytest.fixture
def index(project: Path) -> Path:
    return _write_index(
        project,
        [
            ("src/a.py", "python", 1, 5, (2.0, 0.0)),
            ("src/b.py", "python", 1, 5, (0.8, 0.6)),
            ("src/c.py", "python", 1, 5, (0.28, 0.96)),
            ("src/short.py", "python", 1, 2, (2.0, 0.0)),
            ("src/main.rs", "rust", 1, 5, (2.0, 0.0)),
            ("test/z.py", "python", 1, 5, (2.0, 0.0)),
            ("src/zero.py", "python", 1, 5, (0.0, 0.0)),
        ],
    )


def test_duplicate_groups_follow_transitive_edges_across_blocks(
    index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_duplicates, "_BLOCK_SIZE", 2)
    result = CliRunner().invoke(
        cli.app,
        ["duplicates", "--lang", "python", "--path", "src/*", "--threshold", "0.75", "--json"],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    groups = json.loads(result.output)
    assert [[chunk["file_path"] for chunk in group["chunks"]] for group in groups] == [
        ["src/a.py", "src/b.py", "src/c.py"]
    ]


def test_exact_match_and_text_output(index: Path) -> None:
    result = CliRunner().invoke(cli.app, ["duplicates", "--threshold", "1", "--min-lines", "3"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert "Group 1 (3 chunks)" in result.output
    assert "src/a.py:1-5" in result.output
    assert "src/main.rs:1-5" in result.output
    assert "test/z.py:1-5" in result.output


def test_default_threshold(project: Path) -> None:
    _write_index(
        project,
        [
            ("src/a.py", "python", 1, 5, (1.0, 0.0)),
            ("src/b.py", "python", 1, 5, (24.0, 7.0)),  # cosine 0.96 with a.py
            ("src/c.py", "python", 1, 5, (12.0, -5.0)),  # cosine 0.92 with a.py
        ],
    )
    result = CliRunner().invoke(cli.app, ["duplicates"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert result.output == "Group 1 (2 chunks)\n  src/a.py:1-5 [python]\n  src/b.py:1-5 [python]\n"


@pytest.mark.parametrize(
    ("second_start", "output"),
    [
        pytest.param(13, "No similar chunks found.\n", id="overlapping"),
        pytest.param(
            14,
            "Group 1 (2 chunks)\n  src/a.py:1-13 [python]\n  src/a.py:14-27 [python]\n",
            id="disjoint",
        ),
    ],
)
def test_chunks_of_one_file_match_only_without_shared_lines(
    project: Path, second_start: int, output: str
) -> None:
    _write_index(
        project,
        [
            ("src/a.py", "python", 1, 13, (1.0, 0.0)),
            ("src/a.py", "python", second_start, 27, (1.0, 0.0)),
        ],
    )
    result = CliRunner().invoke(cli.app, ["duplicates"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert result.output == output


def test_missing_index_does_not_create_database(index: Path) -> None:
    index.unlink()
    result = CliRunner().invoke(cli.app, ["duplicates"])
    assert result.exit_code == 1
    assert "Run `ccc index` first" in result.output
    assert not index.exists()


def test_index_file_without_table_reports_missing_index(project: Path) -> None:
    # The daemon creates the file before its first index run creates the table.
    path = settings.target_sqlite_db_path(project)
    sqlite.connect(path, load_vec=True).close()
    assert path.is_file()
    result = CliRunner().invoke(cli.app, ["duplicates"])
    assert result.exit_code == 1
    assert "Run `ccc index` first" in result.output


def test_rolls_back_the_journal_of_an_interrupted_write(index: Path) -> None:
    # The writer spills pages into the database file and dies before it commits.
    writer = textwrap.dedent(
        """
        import os, sqlite3, sys

        conn = sqlite3.connect(sys.argv[1], isolation_level=None)
        conn.execute("PRAGMA cache_size = 1")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE pad (data BLOB)")
        conn.execute("INSERT INTO pad VALUES (zeroblob(1000000))")
        os._exit(0)
        """
    )
    subprocess.run([sys.executable, "-c", writer, str(index)], check=True)
    journal = index.with_name(f"{index.name}-journal")
    assert journal.exists()
    result = CliRunner().invoke(cli.app, ["duplicates", "--threshold", "1"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert "Group 1 (3 chunks)" in result.output
    assert not journal.exists()


def test_nonfinite_threshold_is_rejected(index: Path) -> None:
    result = CliRunner().invoke(cli.app, ["duplicates", "--threshold", "nan"])
    assert result.exit_code == 2
    assert "must be finite" in result.output
