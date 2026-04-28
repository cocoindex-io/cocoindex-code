"""Tests for native declaration graph indexing."""

from __future__ import annotations

from pathlib import Path

from cocoindex_code.code_graph_indexer import index_code_declarations
from cocoindex_code.declarations_db import db_connection


def test_index_code_declarations_populates_symbols_and_calls(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text(
        "def helper():\n"
        "    return 1\n\n"
        "def main():\n"
        "    return helper()\n",
        encoding="utf-8",
    )
    db_path = tmp_path / ".cocoindex_code" / "target_sqlite.db"

    result = index_code_declarations(tmp_path, db_path)

    assert result["success"] is True
    assert result["files"] == 1
    assert result["declarations"] == 2
    with db_connection(db_path) as conn:
        declarations = conn.execute(
            "SELECT name FROM declarations ORDER BY name"
        ).fetchall()
        calls = conn.execute(
            "SELECT callee_name, caller_decl_id, callee_decl_id FROM calls"
        ).fetchall()

    assert [row["name"] for row in declarations] == ["helper", "main"]
    assert any(
        row["callee_name"] == "helper"
        and row["caller_decl_id"] is not None
        and row["callee_decl_id"] is not None
        for row in calls
    )


def test_incremental_index_removes_deleted_code_file(tmp_path: Path) -> None:
    source = tmp_path / "stale.py"
    source.write_text("def stale_symbol():\n    return 1\n", encoding="utf-8")
    db_path = tmp_path / ".cocoindex_code" / "target_sqlite.db"

    index_code_declarations(tmp_path, db_path)
    source.unlink()
    result = index_code_declarations(tmp_path, db_path, changed_paths=["stale.py"])

    assert result["success"] is True
    assert result["files"] == 0
    assert result["deleted_files"] == 1
    with db_connection(db_path) as conn:
        declarations = conn.execute("SELECT name FROM declarations").fetchall()
        signatures = conn.execute("SELECT file_path FROM file_signatures").fetchall()

    assert declarations == []
    assert signatures == []
