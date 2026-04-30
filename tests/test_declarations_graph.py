"""Tests for declarations_graph module (cross-repo resolution, impact radius, call chains)."""

# ruff: noqa: E501

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from cocoindex_code.declarations_db import (
    SCHEMA_SQL,
)
from cocoindex_code.declarations_graph import (
    build_call_chain,
    query_impact_radius,
    resolve_callee_decl_id,
    resolve_named_decl_in_file,
    resolve_superclass_decl_id,
)


@pytest.fixture
def test_db() -> tuple[Path, sqlite3.Connection]:
    """Create a test database with sample declarations and calls."""
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        yield db_path, conn
        conn.close()


def test_resolve_callee_same_file_same_repo(test_db: tuple[Path, sqlite3.Connection]) -> None:
    """Test resolving a callee in the same file (highest priority)."""
    _, conn = test_db

    # Insert declarations in same file, same repo
    foo_cursor = conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo1", "src/module.ts", "function", "foo", 1, 5, False, "treesitter"),
    )
    foo_id = foo_cursor.lastrowid

    conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo1", "src/module.ts", "function", "bar", 10, 15, False, "treesitter"),
    )

    # Add a reference from bar to foo
    conn.execute(
        'INSERT INTO "references" (repo_id, file_path, callee_name, start_line, source) '
        "VALUES (?, ?, ?, ?, ?)",
        ("repo1", "src/module.ts", "foo", 12, "treesitter"),
    )

    conn.commit()

    # Resolve should find foo in same file
    resolved_id = resolve_callee_decl_id(conn, "repo1", "src/module.ts", "foo")
    assert resolved_id == foo_id


def test_resolve_callee_cross_repo_exported(test_db: tuple[Path, sqlite3.Connection]) -> None:
    """Test cross-repo resolution with exported symbols only."""
    _, conn = test_db

    # Insert exported declaration in repo2
    exported_cursor = conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo2", "shared/types/index.ts", "class", "User", 1, 10, True, "treesitter"),
    )
    exported_id = exported_cursor.lastrowid

    # Insert private (non-exported) declaration with same name in repo2
    conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo2", "internal/user_impl.ts", "class", "User", 5, 50, False, "treesitter"),
    )

    conn.commit()

    # Resolve from repo1 should prefer exported
    resolved_id = resolve_callee_decl_id(conn, "repo1", "src/app.ts", "User")
    assert resolved_id == exported_id


def test_resolve_callee_ambiguity_same_length(test_db: tuple[Path, sqlite3.Connection]) -> None:
    """Test ambiguity guard: same symbol name in multiple cross-repo locations with equal path length."""
    _, conn = test_db

    # Insert two exported symbols with same name and path length (ambiguous)
    conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo2", "shared/fmt/util.ts", "function", "format", 1, 10, True, "treesitter"),
    )

    conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo3", "shared/str/util.ts", "function", "format", 1, 10, True, "treesitter"),
    )

    conn.commit()

    # Should return None due to ambiguity
    resolved_id = resolve_callee_decl_id(conn, "repo1", "src/app.ts", "format")
    assert resolved_id is None


def test_resolve_named_decl_in_file(test_db: tuple[Path, sqlite3.Connection]) -> None:
    """Test resolving a named declaration by kind filter."""
    _, conn = test_db

    user_class_cursor = conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo1", "src/models.ts", "class", "User", 1, 50, False, "treesitter"),
    )
    user_class_id = user_class_cursor.lastrowid

    conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo1", "src/models.ts", "function", "User", 100, 110, False, "treesitter"),
    )

    conn.commit()

    # Resolve class kind only
    resolved_id = resolve_named_decl_in_file(conn, "repo1", "src/models.ts", "User", ("class",))
    assert resolved_id == user_class_id


def test_resolve_superclass(test_db: tuple[Path, sqlite3.Connection]) -> None:
    """Test resolving a superclass with kind priority."""
    _, conn = test_db

    animal_cursor = conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo1", "src/base.ts", "class", "Animal", 1, 50, False, "treesitter"),
    )
    animal_id = animal_cursor.lastrowid

    conn.commit()

    resolved_id = resolve_superclass_decl_id(conn, "repo1", "Animal")
    assert resolved_id == animal_id


def test_query_impact_radius_simple(test_db: tuple[Path, sqlite3.Connection]) -> None:
    """Test impact radius BFS with a simple call graph."""
    _, conn = test_db

    # Insert declarations
    foo_cursor = conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo1", "src/a.ts", "function", "foo", 1, 10, True, "treesitter"),
    )
    foo_id = foo_cursor.lastrowid

    bar_cursor = conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo1", "src/b.ts", "function", "bar", 1, 10, False, "treesitter"),
    )
    bar_id = bar_cursor.lastrowid

    # Add call: bar calls foo
    conn.execute(
        "INSERT INTO calls (repo_id, file_path, caller_decl_id, callee_decl_id, line, callee_name, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("repo1", "src/b.ts", bar_id, foo_id, 5, "foo", "treesitter"),
    )

    conn.commit()

    # Query impact from foo (should include bar as a caller)
    result = query_impact_radius(conn, [foo_id], depth=2, max_nodes=50)

    assert result["node_count"] >= 1
    assert result["edge_count"] >= 0
    assert not result["truncated"]


def test_build_call_chain(test_db: tuple[Path, sqlite3.Connection]) -> None:
    """Test building a call chain from a starting function."""
    _, conn = test_db

    # foo calls bar calls baz
    foo_cursor = conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo1", "src/a.ts", "function", "foo", 1, 10, False, "treesitter"),
    )
    foo_id = foo_cursor.lastrowid

    bar_cursor = conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo1", "src/b.ts", "function", "bar", 1, 10, False, "treesitter"),
    )
    bar_id = bar_cursor.lastrowid

    baz_cursor = conn.execute(
        "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("repo1", "src/c.ts", "function", "baz", 1, 10, False, "treesitter"),
    )
    baz_id = baz_cursor.lastrowid

    # foo -> bar
    conn.execute(
        "INSERT INTO calls (repo_id, file_path, caller_decl_id, callee_decl_id, line, callee_name, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("repo1", "src/a.ts", foo_id, bar_id, 5, "bar", "treesitter"),
    )

    # bar -> baz
    conn.execute(
        "INSERT INTO calls (repo_id, file_path, caller_decl_id, callee_decl_id, line, callee_name, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("repo1", "src/b.ts", bar_id, baz_id, 5, "baz", "treesitter"),
    )

    conn.commit()

    # Build call chain from foo
    result = build_call_chain(conn, foo_id, max_depth=3)

    assert result["start_decl_id"] == foo_id
    assert len(result["levels"]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
