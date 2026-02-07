"""Tests for cocoindex-code: config discovery and end-to-end indexing/querying."""

import shutil
from pathlib import Path

import pytest

from cocoindex_code.config import _discover_codebase_root
from cocoindex_code.indexer import app
from cocoindex_code.query import query_codebase

pytest_plugins = ("pytest_asyncio",)

# === Sample codebase files ===

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

SAMPLE_ML_MODEL_PY = '''\
"""Machine learning model implementation."""

class NeuralNetwork:
    """A simple neural network for classification."""

    def __init__(self, layers: list[int]):
        self.layers = layers
        self.weights = []

    def train(self, data: list, labels: list) -> None:
        """Train the neural network on provided data."""
        pass

    def predict(self, input_data: list) -> float:
        """Make a prediction using the trained model."""
        return 0.0
'''

SAMPLE_UTILS_AUTH_PY = '''\
"""Utility functions for authentication."""

def authenticate_user(username: str, password: str) -> bool:
    """Authenticate a user with username and password."""
    return username == "admin" and password == "secret"

def create_login_session(user_id: int) -> str:
    """Create a new login session for the authenticated user."""
    return f"session_{user_id}"
'''


# === Helper functions ===


def clear_codebase(codebase: Path) -> None:
    """Remove all files from the codebase (except .cocoindex_code)."""
    for item in codebase.iterdir():
        if item.name != ".cocoindex_code":
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()


def setup_base_codebase(codebase: Path) -> None:
    """Set up the base codebase files."""
    clear_codebase(codebase)
    (codebase / "main.py").write_text(SAMPLE_MAIN_PY)
    (codebase / "utils.py").write_text(SAMPLE_UTILS_PY)

    lib_dir = codebase / "lib"
    lib_dir.mkdir(exist_ok=True)
    (lib_dir / "database.py").write_text(SAMPLE_DATABASE_PY)


# === Tests ===


class TestEndToEnd:
    """End-to-end tests for the complete index-query workflow."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_index_and_query_codebase(
        self, test_codebase_root: Path, coco_runtime: None
    ) -> None:
        """Should index a codebase and return relevant query results."""
        setup_base_codebase(test_codebase_root)
        await app.update(report_to_stdout=False)

        # Verify index was created
        index_dir = test_codebase_root / ".cocoindex_code"
        assert index_dir.exists()
        assert (index_dir / "target_sqlite.db").exists()

        # Query for Fibonacci
        results = await query_codebase("fibonacci calculation")
        assert len(results) > 0
        assert "main.py" in results[0].file_path
        assert "fibonacci" in results[0].content.lower()

        # Query for database connection
        results = await query_codebase("database connection")
        assert len(results) > 0
        assert "database.py" in results[0].file_path

    @pytest.mark.asyncio(loop_scope="session")
    async def test_incremental_update_add_file(
        self, test_codebase_root: Path, coco_runtime: None
    ) -> None:
        """Should reflect newly added files after re-indexing."""
        setup_base_codebase(test_codebase_root)
        await app.update(report_to_stdout=False)

        # Query for ML content - should not find it
        results = await query_codebase("machine learning neural network")
        has_ml = any(
            "neural" in r.content.lower() or "machine learning" in r.content.lower()
            for r in results
        )
        assert not has_ml or results[0].score < 0.5

        # Add a new ML file
        (test_codebase_root / "ml_model.py").write_text(SAMPLE_ML_MODEL_PY)

        # Re-index and query again
        await app.update(report_to_stdout=False)
        results = await query_codebase("neural network machine learning")

        assert len(results) > 0
        assert "ml_model.py" in results[0].file_path

    @pytest.mark.asyncio(loop_scope="session")
    async def test_incremental_update_modify_file(
        self, test_codebase_root: Path, coco_runtime: None
    ) -> None:
        """Should reflect file modifications after re-indexing."""
        setup_base_codebase(test_codebase_root)
        await app.update(report_to_stdout=False)

        # Modify utils.py to add authentication
        (test_codebase_root / "utils.py").write_text(SAMPLE_UTILS_AUTH_PY)

        # Re-index and query for authentication
        await app.update(report_to_stdout=False)
        results = await query_codebase("user authentication login")

        assert len(results) > 0
        assert "utils.py" in results[0].file_path
        content_lower = results[0].content.lower()
        assert "authenticate" in content_lower or "login" in content_lower

    @pytest.mark.asyncio(loop_scope="session")
    async def test_incremental_update_delete_file(
        self, test_codebase_root: Path, coco_runtime: None
    ) -> None:
        """Should no longer return results from deleted files after re-indexing."""
        setup_base_codebase(test_codebase_root)
        await app.update(report_to_stdout=False)

        # Query for database - should find it
        results = await query_codebase("database connection execute query")
        assert any("database.py" in r.file_path for r in results)

        # Delete the database file
        (test_codebase_root / "lib" / "database.py").unlink()

        # Re-index and query again - should no longer find database.py
        await app.update(report_to_stdout=False)
        results = await query_codebase("database connection execute query")
        assert not any("database.py" in r.file_path for r in results)


class TestCodebaseRootDiscovery:
    """Tests for codebase root discovery logic - stateless, no global config needed."""

    def test_prefers_cocoindex_code_over_git(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should prefer .cocoindex_code directory over .git when both exist."""
        # Create both markers in parent
        parent = tmp_path / "project"
        parent.mkdir()
        (parent / ".cocoindex_code").mkdir()
        (parent / ".git").mkdir()

        # Run from a subdirectory
        subdir = parent / "src" / "lib"
        subdir.mkdir(parents=True)

        monkeypatch.chdir(subdir)
        result = _discover_codebase_root()
        assert result == parent

    def test_finds_git_in_parent_hierarchy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should find .git in parent when deeply nested."""
        # Create .git at root level
        (tmp_path / ".git").mkdir()

        # Create deep nesting
        deep_dir = tmp_path / "a" / "b" / "c" / "d" / "e"
        deep_dir.mkdir(parents=True)

        monkeypatch.chdir(deep_dir)
        result = _discover_codebase_root()
        assert result == tmp_path

    def test_falls_back_to_cwd_when_no_markers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should fall back to cwd when no .git or .cocoindex_code found."""
        # Create empty directory with no markers
        empty_dir = tmp_path / "standalone"
        empty_dir.mkdir()

        monkeypatch.chdir(empty_dir)
        result = _discover_codebase_root()
        assert result == empty_dir
