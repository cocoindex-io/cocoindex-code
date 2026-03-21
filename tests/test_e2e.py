"""End-to-end tests exercising the full CLI → daemon → index → search flow.

Each test function represents a complete session: a series of CLI commands
executed in order, verifying compound stateful effects.  Tests use a real
daemon subprocess (via COCOINDEX_CODE_DIR env var) and the actual CLI
commands through typer's CliRunner.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from cocoindex.connectors import sqlite as coco_sqlite
from typer.testing import CliRunner

from cocoindex_code.cli import app
from cocoindex_code.client import stop_daemon
from cocoindex_code.settings import (
    default_project_settings,
    find_parent_with_marker,
    save_project_settings,
)

runner = CliRunner()

SAMPLE_MAIN_PY = '''\
"""Main application entry point."""

def calculate_fibonacci(n: int) -> int:
    """Calculate the nth Fibonacci number recursively."""
    if n <= 1:
        return n
    return calculate_fibonacci(n - 1) + calculate_fibonacci(n - 2)

def greet_user(name: str) -> str:
    """Return a personalized greeting message."""
    return f"Hello, {name}! Welcome to the application."

if __name__ == "__main__":
    print(greet_user("World"))
    print(calculate_fibonacci(10))
'''

SAMPLE_UTILS_PY = '''\
"""Utility functions for data processing."""

def parse_csv_line(line: str) -> list[str]:
    """Parse a CSV line into a list of values."""
    return line.strip().split(",")

def format_currency(amount: float) -> str:
    """Format a number as USD currency."""
    return f"${amount:,.2f}"

def validate_email(email: str) -> bool:
    """Check if an email address is valid."""
    return "@" in email and "." in email
'''

SAMPLE_DATABASE_PY = '''\
"""Database connection and query utilities."""

class DatabaseConnection:
    """Manages database connections."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._connected = False

    def connect(self) -> None:
        """Establish connection to the database."""
        self._connected = True

    def execute_query(self, sql: str) -> list[dict]:
        """Execute a SQL query and return results."""
        if not self._connected:
            raise RuntimeError("Not connected to database")
        return []
'''

SAMPLE_APP_JS = """\
/** Express web application server. */

const express = require('express');
const app = express();

function handleRequest(req, res) {
    const name = req.query.name || 'World';
    res.json({ message: `Hello, ${name}!` });
}

module.exports = { handleRequest };
"""


@pytest.fixture()
def e2e_project() -> Iterator[Path]:
    """Set up a temp project dir with sample files.

    Cleans up with ``ccc reset --all -f`` and daemon stop.
    """
    base_dir = Path(tempfile.mkdtemp(prefix="ccc_e2e_"))
    project_dir = base_dir / "project"
    project_dir.mkdir()
    (project_dir / "main.py").write_text(SAMPLE_MAIN_PY)
    (project_dir / "utils.py").write_text(SAMPLE_UTILS_PY)
    lib_dir = project_dir / "lib"
    lib_dir.mkdir()
    (lib_dir / "database.py").write_text(SAMPLE_DATABASE_PY)
    (project_dir / ".git").mkdir()

    old_env = os.environ.get("COCOINDEX_CODE_DIR")
    os.environ["COCOINDEX_CODE_DIR"] = str(base_dir)
    old_cwd = os.getcwd()
    os.chdir(project_dir)

    try:
        yield project_dir
    finally:
        os.chdir(project_dir)
        runner.invoke(app, ["reset", "--all", "-f"])
        stop_daemon()
        os.chdir(old_cwd)
        if old_env is None:
            os.environ.pop("COCOINDEX_CODE_DIR", None)
        else:
            os.environ["COCOINDEX_CODE_DIR"] = old_env


# ---------------------------------------------------------------------------
# Session tests — each function is a complete scenario
# ---------------------------------------------------------------------------


def test_session_happy_path(e2e_project: Path) -> None:
    """Init → init (idempotent) → index → status → search variants → daemon status."""
    # Init
    result = runner.invoke(app, ["init"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert (e2e_project / ".cocoindex_code" / "settings.yml").exists()
    assert "Created project settings" in result.output or "settings" in result.output

    # Init again — already initialized
    result = runner.invoke(app, ["init"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "already initialized" in result.output

    # Index
    result = runner.invoke(app, ["index"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "Chunks:" in result.output
    assert "Files:" in result.output

    # Status
    result = runner.invoke(app, ["status"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "Chunks:" in result.output

    # Search — fibonacci
    result = runner.invoke(app, ["search", "fibonacci", "calculation"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "main.py" in result.output

    # Search — database
    result = runner.invoke(app, ["search", "database", "connection"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "database.py" in result.output

    # Search — --lang filter
    result = runner.invoke(app, ["search", "function", "--lang", "python"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "python" in result.output.lower()

    # Search — --path filter
    result = runner.invoke(app, ["search", "function", "--path", "lib/*"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "lib/" in result.output

    # Search — no results
    result = runner.invoke(
        app, ["search", "xyzzy_nonexistent_symbol_12345"], catch_exceptions=False
    )
    assert result.exit_code == 0

    # Daemon status
    result = runner.invoke(app, ["daemon", "status"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "Daemon version:" in result.output


def test_session_incremental_index(e2e_project: Path) -> None:
    """Init → index → add new file → re-index → search finds new content."""
    runner.invoke(app, ["init"], catch_exceptions=False)
    result = runner.invoke(app, ["index"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    # Add a new file
    (e2e_project / "app.js").write_text(SAMPLE_APP_JS)

    # Re-index
    result = runner.invoke(app, ["index"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    # Search should find the new file
    result = runner.invoke(app, ["search", "handleRequest"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "app.js" in result.output


def test_session_reset_databases(e2e_project: Path) -> None:
    """Init → index → search → reset (dbs only) → re-index → search works again."""
    runner.invoke(app, ["init"], catch_exceptions=False)
    runner.invoke(app, ["index"], catch_exceptions=False)

    # Search works before reset
    result = runner.invoke(app, ["search", "fibonacci"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "main.py" in result.output

    # Reset databases only
    result = runner.invoke(app, ["reset", "-f"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "Databases deleted" in result.output

    # Settings should still exist
    assert (e2e_project / ".cocoindex_code" / "settings.yml").exists()

    # DB files should be gone
    assert not (e2e_project / ".cocoindex_code" / "cocoindex.db").exists()
    assert not (e2e_project / ".cocoindex_code" / "target_sqlite.db").exists()

    # Restart daemon to fully release LMDB handles.
    # On free-threaded Python (3.14t), deferred refcounting in the daemon
    # process prevents the Rust LMDB environment from being freed promptly
    # after remove_project; restarting is the reliable way to ensure cleanup.
    runner.invoke(app, ["daemon", "restart"], catch_exceptions=False)

    # Re-index — project is still initialized, just databases gone
    result = runner.invoke(app, ["index"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    # Search works again
    result = runner.invoke(app, ["search", "fibonacci"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "main.py" in result.output


def test_session_reset_all(e2e_project: Path) -> None:
    """Init → index → reset --all → verify full cleanup → search errors."""
    runner.invoke(app, ["init"], catch_exceptions=False)
    runner.invoke(app, ["index"], catch_exceptions=False)

    # .gitignore should have the entry (project has .git dir)
    gitignore = e2e_project / ".gitignore"
    assert gitignore.is_file()
    assert "/.cocoindex_code/" in gitignore.read_text()

    # Reset --all
    result = runner.invoke(app, ["reset", "--all", "-f"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "fully reset" in result.output

    # Settings should be gone
    assert not (e2e_project / ".cocoindex_code" / "settings.yml").exists()

    # .gitignore entry should be removed
    assert "/.cocoindex_code/" not in gitignore.read_text()

    # Search should fail — not initialized
    result = runner.invoke(app, ["search", "fibonacci"])
    assert result.exit_code != 0
    assert "ccc init" in result.output


def test_session_reset_then_full_reinit(e2e_project: Path) -> None:
    """Init → index → reset --all → re-init → re-index → search works again."""
    runner.invoke(app, ["init"], catch_exceptions=False)
    runner.invoke(app, ["index"], catch_exceptions=False)

    # Reset everything
    runner.invoke(app, ["reset", "--all", "-f"], catch_exceptions=False)

    # Restart daemon to fully release LMDB handles (see test_session_reset_databases).
    runner.invoke(app, ["daemon", "restart"], catch_exceptions=False)

    # Re-init from scratch
    result = runner.invoke(app, ["init"], catch_exceptions=False)
    assert result.exit_code == 0
    assert (e2e_project / ".cocoindex_code" / "settings.yml").exists()

    # Re-index
    result = runner.invoke(app, ["index"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    # Search works again
    result = runner.invoke(app, ["search", "fibonacci"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "main.py" in result.output


def test_session_respects_gitignore(e2e_project: Path) -> None:
    """Indexing should skip files ignored by .gitignore while honoring negations."""
    gitignore_path = e2e_project / ".gitignore"
    gitignore_path.write_text("ignored.py\nignored_dir/\n!important.py\n")

    (e2e_project / "ignored.py").write_text("IGNORED_TOKEN = True\n")
    ignored_dir = e2e_project / "ignored_dir"
    ignored_dir.mkdir()
    (ignored_dir / "nested.py").write_text("NESTED_IGNORED = True\n")
    (e2e_project / "important.py").write_text("IMPORTANT_TOKEN = True\n")

    runner.invoke(app, ["init"], catch_exceptions=False)
    result = runner.invoke(app, ["index"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    db_path = e2e_project / ".cocoindex_code" / "target_sqlite.db"
    conn = coco_sqlite.connect(str(db_path), load_vec=True)
    try:
        with conn.readonly() as db:
            file_paths = {
                row[0] for row in db.execute("SELECT DISTINCT file_path FROM code_chunks_vec")
            }
    finally:
        conn.close()

    assert "ignored.py" not in file_paths
    assert "ignored_dir/nested.py" not in file_paths
    assert "important.py" in file_paths


@pytest.mark.usefixtures("e2e_project")
def test_session_daemon_stop_and_auto_start() -> None:
    """Init → index → daemon stop → index auto-starts daemon → search works."""
    runner.invoke(app, ["init"], catch_exceptions=False)
    runner.invoke(app, ["index"], catch_exceptions=False)

    # Stop daemon
    result = runner.invoke(app, ["daemon", "stop"], catch_exceptions=False)
    assert result.exit_code == 0

    # Index should auto-start daemon via ensure_daemon()
    result = runner.invoke(app, ["index"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    # Search should work with the new daemon
    result = runner.invoke(app, ["search", "fibonacci"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "main.py" in result.output


@pytest.mark.usefixtures("e2e_project")
def test_session_daemon_restart() -> None:
    """Init → index → daemon restart → re-index → search works."""
    runner.invoke(app, ["init"], catch_exceptions=False)
    runner.invoke(app, ["index"], catch_exceptions=False)

    # Restart daemon
    result = runner.invoke(app, ["daemon", "restart"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "restarted" in result.output.lower()

    # Re-index in the new daemon
    result = runner.invoke(app, ["index"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    # Search should work
    result = runner.invoke(app, ["search", "fibonacci"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "main.py" in result.output


@pytest.mark.usefixtures("e2e_project")
def test_session_search_refresh() -> None:
    """Init (no explicit index) → search --refresh indexes then searches."""
    runner.invoke(app, ["init"], catch_exceptions=False)

    # search --refresh without prior explicit index
    result = runner.invoke(app, ["search", "--refresh", "fibonacci"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "main.py" in result.output


@pytest.mark.usefixtures("e2e_project")
def test_session_index_not_initialized_errors() -> None:
    """Running ``ccc index`` from uninitialized dir should error."""
    result = runner.invoke(app, ["index"])
    assert result.exit_code != 0
    assert "ccc init" in result.output


def test_session_subdirectory_path_default(e2e_project: Path) -> None:
    """Search from a subdirectory defaults path filter to that subdirectory."""
    runner.invoke(app, ["init"], catch_exceptions=False)
    runner.invoke(app, ["index"], catch_exceptions=False)

    # Search from project root — should find main.py
    result = runner.invoke(app, ["search", "fibonacci"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "main.py" in result.output

    # Search from lib/ — default path filter restricts to lib/*
    os.chdir(e2e_project / "lib")
    result = runner.invoke(app, ["search", "database", "connection"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "database.py" in result.output

    # From lib/, searching for fibonacci should NOT find main.py (outside lib/)
    result = runner.invoke(app, ["search", "fibonacci"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "main.py" not in result.output

    # Back to project root
    os.chdir(e2e_project)


def test_session_not_initialized_errors(e2e_project: Path) -> None:
    """Search and status from uninitialized dir should error with guidance."""
    standalone = Path(tempfile.mkdtemp(prefix="ccc_standalone_"))
    os.chdir(standalone)

    result = runner.invoke(app, ["search", "hello"])
    assert result.exit_code != 0
    assert "ccc init" in result.output

    result = runner.invoke(app, ["status"])
    assert result.exit_code != 0
    assert "ccc init" in result.output

    # Return to project dir so fixture cleanup works
    os.chdir(e2e_project)


def test_session_doctor_happy_path(e2e_project: Path) -> None:
    """Init → index → doctor shows global settings, daemon, model, project, and index info."""
    runner.invoke(app, ["init"], catch_exceptions=False)
    result = runner.invoke(app, ["index"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    # Global settings section
    assert "Global Settings" in result.output
    assert "global_settings.yml" in result.output
    assert "provider=" in result.output
    assert "model=" in result.output

    # Daemon section
    assert "Daemon" in result.output
    assert "Version:" in result.output
    assert "Uptime:" in result.output

    # Model check
    assert "[OK] Model Check" in result.output
    assert "Embedding dimension:" in result.output

    # Project settings section
    assert "Project Settings" in result.output
    assert "settings.yml" in result.output
    assert "Include patterns (" in result.output
    assert "Exclude patterns (" in result.output

    # File walk
    assert "[OK] File Walk" in result.output
    assert "Total matched files:" in result.output
    # Our sample project has .py files
    assert ".py:" in result.output

    # Index status
    assert "[OK] Index Status" in result.output
    assert "Chunks:" in result.output
    assert "Files:" in result.output

    # Log files section
    assert "Log Files" in result.output
    assert "daemon.log" in result.output


def test_session_doctor_no_index(e2e_project: Path) -> None:
    """Doctor before indexing should show index not created yet."""
    runner.invoke(app, ["init"], catch_exceptions=False)

    result = runner.invoke(app, ["doctor"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    assert "[OK] Model Check" in result.output
    assert "Index not created yet" in result.output


def test_session_doctor_no_project(e2e_project: Path) -> None:
    """Doctor outside a project should still show global + daemon checks."""
    # Init to create global settings and start daemon
    runner.invoke(app, ["init"], catch_exceptions=False)

    # Move to a standalone directory (not a project)
    standalone = Path(tempfile.mkdtemp(prefix="ccc_standalone_"))
    old_cwd = os.getcwd()
    os.chdir(standalone)
    try:
        result = runner.invoke(app, ["doctor"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        # Global + daemon checks should be present
        assert "Global Settings" in result.output
        assert "Daemon" in result.output
        assert "[OK] Model Check" in result.output

        # Project-specific sections should NOT be present
        assert "Project Settings" not in result.output
        assert "File Walk" not in result.output
        assert "Index Status" not in result.output

        # Log files always present
        assert "Log Files" in result.output
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Daemon startup failure tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def e2e_project_no_global_settings() -> Iterator[Path]:
    """Set up a project with project settings but NO global_settings.yml.

    This reproduces the scenario from issue #113 where a user creates project
    settings manually but hasn't run ``ccc init`` (which creates global settings).
    """
    base_dir = Path(tempfile.mkdtemp(prefix="ccc_e2e_"))
    project_dir = base_dir / "project"
    project_dir.mkdir()
    (project_dir / "main.py").write_text(SAMPLE_MAIN_PY)
    (project_dir / ".git").mkdir()

    old_env = os.environ.get("COCOINDEX_CODE_DIR")
    os.environ["COCOINDEX_CODE_DIR"] = str(base_dir)
    old_cwd = os.getcwd()
    os.chdir(project_dir)

    # Create project settings but NOT global settings — this is the bug scenario
    save_project_settings(project_dir, default_project_settings())

    try:
        yield project_dir
    finally:
        os.chdir(old_cwd)
        stop_daemon()
        if old_env is None:
            os.environ.pop("COCOINDEX_CODE_DIR", None)
        else:
            os.environ["COCOINDEX_CODE_DIR"] = old_env


@pytest.mark.usefixtures("e2e_project_no_global_settings")
def test_session_missing_global_settings_early_error() -> None:
    """When global_settings.yml is missing, project commands should fail early with guidance."""
    # `ccc status` should detect missing global settings before even starting the daemon.
    result = runner.invoke(app, ["status"])
    assert result.exit_code != 0, f"Expected failure but got: {result.output}"
    assert "Global settings not found" in result.output
    assert "global_settings.yml" in result.output
    assert "ccc init" in result.output


@pytest.mark.usefixtures("e2e_project_no_global_settings")
def test_session_daemon_restart_missing_global_settings() -> None:
    """``ccc daemon restart`` should fail fast with daemon log when global settings are missing."""
    # `daemon restart` doesn't go through require_project_root, so it hits the
    # daemon start path where the process dies. Should show the daemon log.
    result = runner.invoke(app, ["daemon", "restart"])
    assert result.exit_code != 0, f"Expected failure but got: {result.output}"
    assert "Daemon log:" in result.output
    assert "User settings not found" in result.output


# ---------------------------------------------------------------------------
# Unit tests (not session-based)
# ---------------------------------------------------------------------------


class TestCodebaseRootDiscovery:
    """Tests for find_parent_with_marker helper."""

    def test_prefers_cocoindex_code_over_git(self, tmp_path: Path) -> None:
        parent = tmp_path / "project"
        parent.mkdir()
        (parent / ".cocoindex_code").mkdir()
        (parent / ".git").mkdir()
        subdir = parent / "src" / "lib"
        subdir.mkdir(parents=True)
        assert find_parent_with_marker(subdir) == parent

    def test_finds_git_in_parent_hierarchy(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        deep_dir = tmp_path / "a" / "b" / "c" / "d" / "e"
        deep_dir.mkdir(parents=True)
        assert find_parent_with_marker(deep_dir) == tmp_path

    def test_falls_back_to_none_when_no_markers(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "standalone"
        empty_dir.mkdir()
        assert find_parent_with_marker(empty_dir) is None
