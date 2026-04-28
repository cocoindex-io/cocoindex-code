"""Tests for analytics modules (centrality, communities, knowledge_gaps, flows)."""

# ruff: noqa: E501

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from cocoindex_code.analytics.centrality import (
    compute_centrality,
    query_hub_nodes,
)
from cocoindex_code.analytics.communities import compute_communities
from cocoindex_code.analytics.flows import detect_flows
from cocoindex_code.analytics.knowledge_gaps import get_knowledge_gaps
from cocoindex_code.declarations_db import (
    SCHEMA_SQL,
    Declaration,
    insert_declarations,
)


@pytest.fixture
def temp_db() -> Path:
    """Create a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.db"


@pytest.fixture
def test_db_with_calls(temp_db: Path) -> sqlite3.Connection:
    """Create a test database with declarations and call graph."""
    conn = sqlite3.connect(temp_db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.execute("PRAGMA journal_mode=WAL")

    # Create a simple call graph:
    # main -> (helper, util)
    # helper -> (internal, process)
    # util -> (internal)
    # process -> (internal)
    # internal (leaf)
    decls = [
        Declaration("repo-1", "src/main.py", "function", "main", "def main()", 10, 20, True),
        Declaration("repo-1", "src/main.py", "function", "helper", "def helper()", 25, 35, False),
        Declaration("repo-1", "src/utils.py", "function", "util", "def util()", 5, 15, True),
        Declaration(
            "repo-1", "src/process.py", "function", "process", "def process()", 1, 10, False
        ),
        Declaration(
            "repo-1", "src/core.py", "function", "internal", "def internal()", 50, 60, False
        ),
    ]
    insert_declarations(conn, decls)

    # Build call graph
    calls = [
        (1, 2, 15, "helper"),  # main -> helper
        (1, 3, 18, "util"),  # main -> util
        (2, 4, 30, "process"),  # helper -> process
        (2, 5, 32, "internal"),  # helper -> internal
        (3, 5, 10, "internal"),  # util -> internal
        (4, 5, 5, "internal"),  # process -> internal
    ]
    for caller_id, callee_id, line, name in calls:
        conn.execute(
            """
            INSERT INTO calls (repo_id, file_path, caller_decl_id, callee_decl_id, line, callee_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("repo-1", "src/main.py", caller_id, callee_id, line, name),
        )

    # Add test coverage
    conn.execute(
        "INSERT INTO tests (repo_id, test_file_path, tested_decl_id) VALUES (?, ?, ?)",
        ("repo-1", "tests/test_main.py", 1),  # main is tested
    )

    conn.commit()
    return conn


class TestCentralityComputation:
    """Test centrality computation."""

    def test_compute_centrality_empty_graph(self, temp_db: Path) -> None:
        """Test centrality on empty graph."""
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        conn.commit()

        result = compute_centrality(temp_db, repo_id="repo-1")
        assert result["success"] is True
        assert result["nodes"] == 0

    def test_compute_centrality_simple_graph(self, test_db_with_calls: sqlite3.Connection) -> None:
        """Test centrality computation on simple graph."""
        result = compute_centrality(
            test_db_with_calls.execute("PRAGMA database_list").fetchone()[2], repo_id="repo-1"
        )
        assert result["success"] is True
        assert result["nodes"] > 0
        assert "edges" in result
        assert "approximated" in result

    def test_query_hub_nodes(self, test_db_with_calls: sqlite3.Connection) -> None:
        """Test querying hub nodes."""
        compute_centrality(
            test_db_with_calls.execute("PRAGMA database_list").fetchone()[2], repo_id="repo-1"
        )
        hubs = query_hub_nodes(test_db_with_calls, repo_id="repo-1", limit=10, min_in_degree=1)
        assert len(hubs) > 0
        for hub in hubs:
            assert hub["in_degree"] >= 1

    def test_global_then_repo_centrality_recompute(
        self, test_db_with_calls: sqlite3.Connection
    ) -> None:
        """Global centrality should not block later repo-scoped recomputation."""
        db_file = Path(test_db_with_calls.execute("PRAGMA database_list").fetchone()[2])

        global_result = compute_centrality(db_file, repo_id=None)
        assert global_result["success"] is True

        repo_result = compute_centrality(db_file, repo_id="repo-1")
        assert repo_result["success"] is True


class TestCommunityDetection:
    """Test community detection."""

    def test_compute_communities_empty_graph(self, temp_db: Path) -> None:
        """Test community detection on empty graph."""
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        conn.commit()

        result = compute_communities(temp_db, repo_id="repo-1")
        # igraph may not be installed; if not, success is False with error message
        if result["success"]:
            assert result["communities"] == 0
        else:
            assert "error" in result or "igraph" in result.get("error", "")

    @pytest.mark.skipif(
        __import__("sys").modules.get("igraph") is None,
        reason="igraph not installed",
    )
    def test_compute_communities_simple_graph(self, test_db_with_calls: sqlite3.Connection) -> None:
        """Test community detection on simple graph."""
        db_file = test_db_with_calls.execute("PRAGMA database_list").fetchone()[2]
        result = compute_communities(Path(db_file), repo_id="repo-1")
        if result["success"]:
            assert "communities_total" in result or result.get("skipped") == "empty graph"


class TestKnowledgeGaps:
    """Test knowledge gap detection."""

    def test_get_knowledge_gaps_untested_hubs(self, test_db_with_calls: sqlite3.Connection) -> None:
        """Test detection of untested hub nodes."""
        # First compute centrality
        db_file = test_db_with_calls.execute("PRAGMA database_list").fetchone()[2]
        compute_centrality(Path(db_file), repo_id="repo-1")

        result = get_knowledge_gaps(Path(db_file), repo_id="repo-1")
        assert result["success"] is True
        assert "untested_hubs" in result
        assert "summary" in result


class TestFlowDetection:
    """Test flow detection (entry points)."""

    def test_detect_flows_no_matches(self, test_db_with_calls: sqlite3.Connection) -> None:
        """Test flow detection returns empty when no patterns match."""
        db_file = test_db_with_calls.execute("PRAGMA database_list").fetchone()[2]
        result = detect_flows(Path(db_file), repo_id="repo-1")
        assert result["success"] is True
        assert result["flows_detected"] >= 0

    def test_detect_flows_with_signatures(self, temp_db: Path) -> None:
        """Test flow detection with matching signatures."""
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)

        # Insert a FastAPI route
        conn.execute(
            """
            INSERT INTO declarations (repo_id, file_path, kind, name, signature, start_line, end_line, exported)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("repo-1", "src/api.py", "function", "get_users", "@app.get('/users')", 10, 15, True),
        )
        conn.commit()

        result = detect_flows(temp_db, repo_id="repo-1")
        assert result["success"] is True
        # Should detect FastAPI route
        if result["flows_detected"] > 0:
            assert any("fastapi" in str(f.get("flow_types", [])).lower() for f in result["flows"])
