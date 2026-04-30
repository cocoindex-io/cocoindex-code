"""Tests for declarations_db schema migrations and backward compatibility."""

# ruff: noqa: E501

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cocoindex_code.declarations_db import (
    db_connection,
    init_db,
)


@pytest.fixture
def temp_db() -> Path:
    """Create a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.db"


class TestSchemaCreation:
    """Test database schema creation."""

    def test_fresh_db_has_all_tables(self, temp_db: Path) -> None:
        """Test that a fresh database has all required tables."""
        init_db(temp_db)

        with db_connection(temp_db) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = {row[0] for row in tables}

        required_tables = {
            "declarations",
            "imports",
            "references",
            "calls",
            "inherits",
            "jobs",
            "tests",
            "file_signatures",
            "centrality",
            "communities",
        }

        assert required_tables.issubset(table_names), (
            f"Missing tables: {required_tables - table_names}"
        )

    def test_declarations_table_schema(self, temp_db: Path) -> None:
        """Test declarations table has required columns."""
        init_db(temp_db)

        with db_connection(temp_db) as conn:
            cols = conn.execute("PRAGMA table_info(declarations)").fetchall()
            col_names = {row[1] for row in cols}

        required_cols = {
            "id",
            "repo_id",
            "file_path",
            "kind",
            "name",
            "start_line",
            "end_line",
            "exported",
            "source",
        }

        assert required_cols.issubset(col_names), f"Missing columns: {required_cols - col_names}"

    def test_calls_table_schema(self, temp_db: Path) -> None:
        """Test calls table has all required columns."""
        init_db(temp_db)

        with db_connection(temp_db) as conn:
            cols = conn.execute("PRAGMA table_info(calls)").fetchall()
            col_names = {row[1] for row in cols}

        required_cols = {
            "id",
            "repo_id",
            "file_path",
            "caller_decl_id",
            "callee_decl_id",
            "line",
            "callee_name",
            "source",
        }

        assert required_cols.issubset(col_names), f"Missing columns: {required_cols - col_names}"

    def test_inherits_table_schema(self, temp_db: Path) -> None:
        """Test inherits table has all required columns."""
        init_db(temp_db)

        with db_connection(temp_db) as conn:
            cols = conn.execute("PRAGMA table_info(inherits)").fetchall()
            col_names = {row[1] for row in cols}

        required_cols = {
            "id",
            "repo_id",
            "file_path",
            "subclass_decl_id",
            "superclass_decl_id",
            "line",
        }

        assert required_cols.issubset(col_names), f"Missing columns: {required_cols - col_names}"

    def test_jobs_table_schema(self, temp_db: Path) -> None:
        """Test jobs table has all required columns."""
        init_db(temp_db)

        with db_connection(temp_db) as conn:
            cols = conn.execute("PRAGMA table_info(jobs)").fetchall()
            col_names = {row[1] for row in cols}

        required_cols = {
            "id",
            "started_at",
            "updated_at",
            "progress",
            "total",
            "status",
            "last_error",
        }

        assert required_cols.issubset(col_names), f"Missing columns: {required_cols - col_names}"

    def test_centrality_table_exists(self, temp_db: Path) -> None:
        """Test centrality table for analytics."""
        init_db(temp_db)

        with db_connection(temp_db) as conn:
            cols = conn.execute("PRAGMA table_info(centrality)").fetchall()
            col_names = {row[1] for row in cols}

        required_cols = {"decl_id", "betweenness", "in_degree", "out_degree"}
        assert required_cols.issubset(col_names)

    def test_communities_table_exists(self, temp_db: Path) -> None:
        """Test communities table for analytics."""
        init_db(temp_db)

        with db_connection(temp_db) as conn:
            cols = conn.execute("PRAGMA table_info(communities)").fetchall()
            col_names = {row[1] for row in cols}

        required_cols = {"community_id", "decl_id"}
        assert required_cols.issubset(col_names)


class TestMigrationBackwardCompat:
    """Test backward compatibility with existing databases."""

    def test_init_db_on_existing_db(self, temp_db: Path) -> None:
        """Test that init_db can be called on existing database."""
        # Create initial db
        init_db(temp_db)

        with db_connection(temp_db) as conn:
            conn.execute(
                "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("repo1", "test.py", "function", "foo", 1, 5, False, "treesitter"),
            )
            conn.commit()
            initial_count = conn.execute("SELECT COUNT(*) FROM declarations").fetchone()[0]

        # Call init_db again
        init_db(temp_db)

        # Data should be preserved
        with db_connection(temp_db) as conn:
            final_count = conn.execute("SELECT COUNT(*) FROM declarations").fetchone()[0]

        assert final_count == initial_count, "Data was lost during re-init"

    def test_init_db_with_connection(self, temp_db: Path) -> None:
        """Test init_db with a connection object."""
        with db_connection(temp_db) as conn:
            init_db(conn)

            # Verify tables exist
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            assert len(tables) > 0

    def test_indices_created(self, temp_db: Path) -> None:
        """Test that all expected indices are created."""
        init_db(temp_db)

        with db_connection(temp_db) as conn:
            indices = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
            index_names = {row[0] for row in indices}

        expected_indices = {
            "idx_decl_name",
            "idx_decl_kind",
            "idx_decl_repo_file",
            "idx_decl_name_exported",
            "idx_imports_module",
            "idx_refs_callee",
            "idx_calls_callee_decl",
            "idx_calls_caller_decl",
            "idx_calls_repo_file",
            "idx_inherits_super",
            "idx_inherits_sub",
            "idx_inherits_repo_file",
        }

        assert expected_indices.issubset(index_names), (
            f"Missing indices: {expected_indices - index_names}"
        )

    def test_fk_constraints_enabled(self, temp_db: Path) -> None:
        """Test that foreign key constraints are properly set up."""
        init_db(temp_db)

        with db_connection(temp_db) as conn:
            # FK constraints should prevent inserting invalid references
            fk_status = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk_status == 1, "Foreign keys should be enabled"


class TestDataIntegrity:
    """Test data integrity and constraints."""

    def test_declarations_upsert(self, temp_db: Path) -> None:
        """Test upserting declarations."""
        init_db(temp_db)

        with db_connection(temp_db) as conn:
            # Insert
            conn.execute(
                "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("repo1", "test.py", "function", "foo", 1, 5, False, "treesitter"),
            )
            conn.commit()

            count1 = conn.execute("SELECT COUNT(*) FROM declarations").fetchone()[0]

            # Insert same file again (should cascade delete)
            conn.execute(
                "DELETE FROM declarations WHERE repo_id = ? AND file_path = ?", ("repo1", "test.py")
            )
            conn.commit()

            count2 = conn.execute("SELECT COUNT(*) FROM declarations").fetchone()[0]

            assert count1 == 1
            assert count2 == 0

    def test_calls_cascade_delete(self, temp_db: Path) -> None:
        """Test that deleting a caller declaration cascades to calls."""
        init_db(temp_db)

        with db_connection(temp_db) as conn:
            # Create declarations
            d1 = conn.execute(
                "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("repo1", "a.py", "function", "foo", 1, 5, False, "treesitter"),
            )
            d1_id = d1.lastrowid

            d2 = conn.execute(
                "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("repo1", "b.py", "function", "bar", 1, 5, False, "treesitter"),
            )
            d2_id = d2.lastrowid

            # Create call edge: bar calls foo
            conn.execute(
                "INSERT INTO calls (repo_id, file_path, caller_decl_id, callee_decl_id, line, callee_name, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("repo1", "b.py", d2_id, d1_id, 3, "foo", "treesitter"),
            )
            conn.commit()

            # Verify call exists
            call_count = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
            assert call_count == 1

            # Delete caller declaration (should cascade delete the call)
            conn.execute("DELETE FROM declarations WHERE id = ?", (d2_id,))
            conn.commit()

            # Call should be deleted (caller_decl_id has ON DELETE CASCADE)
            call_count = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
            assert call_count == 0, "Call should be deleted when caller declaration is deleted"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
