"""End-to-end tests for indexing and querying."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# === Subprocess scripts ===

INDEXER_SCRIPT = """
import os
os.environ["COCOINDEX_CODE_ROOT_PATH"] = os.environ["TEST_CODEBASE_PATH"]

from cocoindex_code.indexer import app
app.update(report_to_stdout=False)
"""

QUERY_SCRIPT_TEMPLATE = """
import os
import json
os.environ["COCOINDEX_CODE_ROOT_PATH"] = os.environ["TEST_CODEBASE_PATH"]

from cocoindex_code.query import query_codebase

results = query_codebase(query={query!r}, limit={limit})

output = [
    {{
        "file_path": r.file_path,
        "content": r.content,
        "score": r.score,
    }}
    for r in results
]
print(json.dumps(output))
"""

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


def run_index(codebase_path: Path) -> None:
    """Run the indexer in a subprocess."""
    result = subprocess.run(
        [sys.executable, "-c", INDEXER_SCRIPT],
        env={**os.environ, "TEST_CODEBASE_PATH": str(codebase_path)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Indexer failed: {result.stderr}")


def run_query(codebase_path: Path, query: str, limit: int = 5) -> list[dict]:
    """Run a query in a subprocess and return results."""
    script = QUERY_SCRIPT_TEMPLATE.format(query=query, limit=limit)
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "TEST_CODEBASE_PATH": str(codebase_path)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Query failed: {result.stderr}")

    return json.loads(result.stdout.strip())


# === Fixtures ===


@pytest.fixture
def isolated_codebase(tmp_path: Path) -> Path:
    """Create an isolated codebase for testing."""
    (tmp_path / "main.py").write_text(SAMPLE_MAIN_PY)
    (tmp_path / "utils.py").write_text(SAMPLE_UTILS_PY)

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "database.py").write_text(SAMPLE_DATABASE_PY)

    return tmp_path


# === Tests ===


class TestEndToEnd:
    """End-to-end tests for the complete index-query workflow."""

    def test_index_and_query_codebase(self, isolated_codebase: Path) -> None:
        """Should index a codebase and return relevant query results."""
        run_index(isolated_codebase)

        # Verify index was created
        index_dir = isolated_codebase / ".cocoindex_code"
        assert index_dir.exists()
        assert (index_dir / "target_sqlite.db").exists()

        # Query for Fibonacci
        results = run_query(isolated_codebase, "fibonacci calculation")
        assert len(results) > 0
        assert "main.py" in results[0]["file_path"]
        assert "fibonacci" in results[0]["content"].lower()

        # Query for database connection
        results = run_query(isolated_codebase, "database connection")
        assert len(results) > 0
        assert "database.py" in results[0]["file_path"]

    def test_incremental_update_add_file(self, isolated_codebase: Path) -> None:
        """Should reflect newly added files after re-indexing."""
        run_index(isolated_codebase)

        # Query for ML content - should not find it
        results = run_query(isolated_codebase, "machine learning neural network")
        has_ml = any(
            "neural" in r["content"].lower() or "machine learning" in r["content"].lower()
            for r in results
        )
        assert not has_ml or results[0]["score"] < 0.5

        # Add a new ML file
        (isolated_codebase / "ml_model.py").write_text(SAMPLE_ML_MODEL_PY)

        # Re-index and query again
        run_index(isolated_codebase)
        results = run_query(isolated_codebase, "neural network machine learning")

        assert len(results) > 0
        assert "ml_model.py" in results[0]["file_path"]

    def test_incremental_update_modify_file(self, isolated_codebase: Path) -> None:
        """Should reflect file modifications after re-indexing."""
        run_index(isolated_codebase)

        # Modify utils.py to add authentication
        (isolated_codebase / "utils.py").write_text(SAMPLE_UTILS_AUTH_PY)

        # Re-index and query for authentication
        run_index(isolated_codebase)
        results = run_query(isolated_codebase, "user authentication login")

        assert len(results) > 0
        assert "utils.py" in results[0]["file_path"]
        content_lower = results[0]["content"].lower()
        assert "authenticate" in content_lower or "login" in content_lower

    def test_incremental_update_delete_file(self, isolated_codebase: Path) -> None:
        """Should no longer return results from deleted files after re-indexing."""
        run_index(isolated_codebase)

        # Query for database - should find it
        results = run_query(isolated_codebase, "database connection execute query")
        assert any("database.py" in r["file_path"] for r in results)

        # Delete the database file
        (isolated_codebase / "lib" / "database.py").unlink()

        # Re-index and query again - should no longer find database.py
        run_index(isolated_codebase)
        results = run_query(isolated_codebase, "database connection execute query")
        assert not any("database.py" in r["file_path"] for r in results)
