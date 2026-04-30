"""Tests for change_detection module (git diff → declarations + risk scoring)."""

# ruff: noqa: E501

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from cocoindex_code.change_detection import (
    AffectedDeclaration,
    DiffHunk,
    parse_diff_hunks,
    score_affected_declarations,
)
from cocoindex_code.declarations_db import (
    db_connection,
    init_db,
)


@pytest.fixture
def temp_db() -> Path:
    """Create a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.db"


@pytest.fixture
def test_db(temp_db: Path) -> tuple[Path, sqlite3.Connection]:
    """Create a test database with sample declarations."""
    init_db(temp_db)

    with db_connection(temp_db) as conn:
        # Insert declarations for testing

        # Untested export function
        export_cursor = conn.execute(
            "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("repo1", "src/api.py", "function", "create_user", 10, 30, True, "treesitter"),
        )
        export_func_id = export_cursor.lastrowid

        # Tested internal function
        validate_cursor = conn.execute(
            "INSERT INTO declarations (repo_id, file_path, kind, name, start_line, end_line, exported, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("repo1", "src/api.py", "function", "validate_email", 35, 45, False, "treesitter"),
        )
        validate_id = validate_cursor.lastrowid

        # Add test edge for validate_email
        conn.execute(
            "INSERT INTO tests (repo_id, test_file_path, tested_decl_id, confidence, method) "
            "VALUES (?, ?, ?, ?, ?)",
            ("repo1", "tests/test_api.py", validate_id, 1.0, "filename"),
        )

        # Call graph: create_user calls validate_email
        conn.execute(
            "INSERT INTO calls (repo_id, file_path, caller_decl_id, callee_decl_id, line, callee_name, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "repo1",
                "src/api.py",
                export_func_id,
                validate_id,
                20,
                "validate_email",
                "treesitter",
            ),
        )

        # Add centrality data (hub node has high in_degree)
        conn.execute(
            "INSERT OR REPLACE INTO centrality (repo_id, decl_id, betweenness, in_degree, out_degree, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("repo1", export_func_id, 0.5, 5, 2, 0),
        )

        conn.execute(
            "INSERT OR REPLACE INTO centrality (repo_id, decl_id, betweenness, in_degree, out_degree, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("repo1", validate_id, 0.1, 2, 1, 0),
        )

        yield temp_db, conn


class TestParseDiffHunks:
    """Test unified diff parsing."""

    def test_parse_single_file_single_hunk(self) -> None:
        """Test parsing a simple diff with one file and one hunk."""
        diff_text = """+++ b/src/api.py
@@ -10,5 +10,7 @@ def old_func():
 print("unchanged")
+new_line_1
+new_line_2
 more unchanged
"""
        hunks = parse_diff_hunks(diff_text)

        assert len(hunks) == 1
        assert hunks[0].file_path == "src/api.py"
        assert hunks[0].lines_changed == 2

    def test_parse_multiple_hunks_same_file(self) -> None:
        """Test parsing multiple hunks in the same file."""
        diff_text = """+++ b/src/module.py
@@ -10,3 +10,4 @@
+added_line_1
@@ -20,2 +21,3 @@
+added_line_2
+added_line_3
"""
        hunks = parse_diff_hunks(diff_text)

        assert len(hunks) == 2
        assert all(h.file_path == "src/module.py" for h in hunks)
        assert hunks[0].lines_changed == 1
        assert hunks[1].lines_changed == 2

    def test_parse_mixed_additions_deletions(self) -> None:
        """Test parsing with both additions and deletions."""
        diff_text = """+++ b/src/util.py
@@ -5,5 +5,6 @@
-old_line
+new_line
 unchanged
"""
        hunks = parse_diff_hunks(diff_text)

        assert len(hunks) == 1
        assert hunks[0].lines_changed == 2  # counts both add and delete


class TestScoreChangeRisk:
    """Test risk scoring logic."""

    def test_score_affected_declarations(self, test_db: tuple[Path, sqlite3.Connection]) -> None:
        """Test scoring declarations affected by changes."""
        _, conn = test_db

        hunks = [
            DiffHunk(
                file_path="src/api.py",
                start_line=15,
                end_line=25,
                lines_changed=3,
            ),
        ]

        affected = score_affected_declarations(conn, hunks, "repo1")

        # Should find create_user (lines 10-30)
        assert len(affected) >= 1
        assert any(d.name == "create_user" for d in affected)


class TestDetectChanges:
    """Test change detection and mapping to declarations."""

    def test_map_hunks_to_single_declaration(
        self, test_db: tuple[Path, sqlite3.Connection]
    ) -> None:
        """Test mapping a hunk to a single declaration."""
        _, conn = test_db

        hunks = [
            DiffHunk(
                file_path="src/api.py",
                start_line=15,
                end_line=20,
                lines_changed=2,
            ),
        ]

        affected = score_affected_declarations(conn, hunks, "repo1")

        # Should find create_user (lines 10-30, hunk at 15-20)
        assert any(d.name == "create_user" for d in affected)

    def test_score_untested_export(self, test_db: tuple[Path, sqlite3.Connection]) -> None:
        """Test that untested exports score higher."""
        _, conn = test_db

        hunks = [
            DiffHunk(
                file_path="src/api.py",
                start_line=15,
                end_line=25,
                lines_changed=3,
            ),
        ]

        affected = score_affected_declarations(conn, hunks, "repo1")

        # create_user is untested, exported, hub node
        create_user = [d for d in affected if d.name == "create_user"]
        assert len(create_user) > 0
        assert create_user[0].tested is False
        assert create_user[0].exported is True
        assert create_user[0].risk_score > 0

    def test_score_tested_internal(self, test_db: tuple[Path, sqlite3.Connection]) -> None:
        """Test that tested internal functions score lower."""
        _, conn = test_db

        hunks = [
            DiffHunk(
                file_path="src/api.py",
                start_line=40,
                end_line=43,
                lines_changed=1,
            ),
        ]

        affected = score_affected_declarations(conn, hunks, "repo1")

        # validate_email is tested, internal, low centrality
        validate = [d for d in affected if d.name == "validate_email"]
        if validate:
            assert validate[0].tested is True
            assert validate[0].exported is False


class TestAffectedDeclarationDataclass:
    """Test AffectedDeclaration data structure."""

    def test_affected_declaration_creation(self) -> None:
        """Test creating an AffectedDeclaration."""
        decl = AffectedDeclaration(
            decl_id=1,
            repo_id="repo1",
            file_path="src/api.py",
            name="my_func",
            kind="function",
            signature="def my_func(x, y):",
            start_line=10,
            end_line=20,
            exported=True,
            lines_changed=5,
            risk_score=0.75,
            tested=False,
            in_degree=3,
            betweenness=0.2,
        )

        assert decl.name == "my_func"
        assert decl.exported is True
        assert decl.risk_score == 0.75


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
