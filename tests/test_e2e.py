"""End-to-end tests exercising the full CLI → daemon → index → search flow.

Each test uses a real daemon subprocess (via COCOINDEX_CODE_DIR env var)
and the actual CLI commands through typer's CliRunner.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cocoindex_code.cli import app
from cocoindex_code.client import stop_daemon
from cocoindex_code.settings import find_parent_with_marker

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


@pytest.fixture(scope="module")
def e2e_env() -> Iterator[Path]:
    """Set up a temp project dir with sample files and a daemon subprocess.

    Uses COCOINDEX_CODE_DIR to redirect the daemon to a temp directory,
    so the subprocess picks up the right paths.
    """
    base_dir = Path(tempfile.mkdtemp(prefix="ccc_e2e_"))
    project_dir = base_dir / "project"
    project_dir.mkdir()
    (project_dir / "main.py").write_text(SAMPLE_MAIN_PY)
    (project_dir / "utils.py").write_text(SAMPLE_UTILS_PY)
    lib_dir = project_dir / "lib"
    lib_dir.mkdir()
    (lib_dir / "database.py").write_text(SAMPLE_DATABASE_PY)

    old_env = os.environ.get("COCOINDEX_CODE_DIR")
    os.environ["COCOINDEX_CODE_DIR"] = str(base_dir)

    try:
        yield project_dir
    finally:
        stop_daemon()
        if old_env is None:
            os.environ.pop("COCOINDEX_CODE_DIR", None)
        else:
            os.environ["COCOINDEX_CODE_DIR"] = old_env


class TestCLIEndToEnd:
    """Tests that exercise ccc init → index → search → status via the real CLI."""

    def test_init_creates_settings(self, e2e_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(e2e_env)
        result = runner.invoke(app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert (e2e_env / ".cocoindex_code" / "settings.yml").exists()
        assert "Created project settings" in result.output or "already initialized" in result.output

    def test_init_already_initialized(self, e2e_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(e2e_env)
        result = runner.invoke(app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "already initialized" in result.output

    def test_index(self, e2e_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(e2e_env)
        result = runner.invoke(app, ["index"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "Chunks:" in result.output
        assert "Files:" in result.output

    def test_status(self, e2e_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(e2e_env)
        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "Chunks:" in result.output

    def test_search_fibonacci(self, e2e_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(e2e_env)
        result = runner.invoke(app, ["search", "fibonacci", "calculation"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "main.py" in result.output

    def test_search_database(self, e2e_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(e2e_env)
        result = runner.invoke(app, ["search", "database", "connection"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "database.py" in result.output

    def test_search_with_lang_filter(self, e2e_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(e2e_env)
        result = runner.invoke(
            app, ["search", "function", "--lang", "python"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        assert "python" in result.output.lower()

    def test_search_with_path_filter(self, e2e_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(e2e_env)
        result = runner.invoke(
            app, ["search", "function", "--path", "lib/*"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        assert "lib/" in result.output

    def test_search_no_results(self, e2e_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(e2e_env)
        result = runner.invoke(
            app,
            ["search", "xyzzy_nonexistent_symbol_12345"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

    def test_daemon_status(self, e2e_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(e2e_env)
        result = runner.invoke(app, ["daemon", "status"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "Daemon version:" in result.output

    def test_index_shows_stats(self, e2e_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Indexing should complete and show final stats."""
        monkeypatch.chdir(e2e_env)
        result = runner.invoke(app, ["index"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "Chunks:" in result.output
        assert "Files:" in result.output

    def test_incremental_index_new_file(
        self, e2e_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Adding a file and re-indexing should make it searchable."""
        monkeypatch.chdir(e2e_env)
        (e2e_env / "app.js").write_text(SAMPLE_APP_JS)

        result = runner.invoke(app, ["index"], catch_exceptions=False)
        assert result.exit_code == 0

        result = runner.invoke(app, ["search", "handleRequest"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "app.js" in result.output

    def test_not_initialized_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Running commands outside an initialized project should fail."""
        standalone = tmp_path / "standalone"
        standalone.mkdir()
        monkeypatch.chdir(standalone)
        result = runner.invoke(app, ["index"])
        assert result.exit_code != 0
        assert "ccc init" in result.output


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
