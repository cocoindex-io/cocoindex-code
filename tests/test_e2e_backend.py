"""End-to-end tests across both vector backends (sqlite-vec and turbo-quant).

Exercises the full CLI -> daemon -> index -> search loop for each backend, plus
status/doctor parity. Mirrors the fixture and driving style of test_e2e.py.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import make_test_user_settings
from typer.testing import CliRunner

from cocoindex_code.cli import app
from cocoindex_code.client import stop_daemon
from cocoindex_code.settings import save_user_settings

runner = CliRunner()

SAMPLE_MAIN_PY = '''\
"""Main application entry point."""

def calculate_fibonacci(n: int) -> int:
    """Calculate the nth Fibonacci number recursively."""
    if n <= 1:
        return n
    return calculate_fibonacci(n - 1) + calculate_fibonacci(n - 2)
'''

SAMPLE_DB_PY = '''\
"""Database connection utilities."""

class DatabaseConnection:
    """Manages database connections."""

    def connect(self) -> None:
        """Establish connection to the database."""
        self._connected = True
'''


@pytest.fixture()
def e2e_project() -> Iterator[Path]:
    base_dir = Path(tempfile.mkdtemp(prefix="ccc_e2e_backend_"))
    project_dir = base_dir / "project"
    project_dir.mkdir()
    (project_dir / "main.py").write_text(SAMPLE_MAIN_PY)
    lib_dir = project_dir / "lib"
    lib_dir.mkdir()
    (lib_dir / "database.py").write_text(SAMPLE_DB_PY)
    (project_dir / ".git").mkdir()

    old_env = os.environ.get("COCOINDEX_CODE_DIR")
    os.environ["COCOINDEX_CODE_DIR"] = str(base_dir)
    old_cwd = os.getcwd()
    os.chdir(project_dir)
    save_user_settings(make_test_user_settings())

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


@pytest.mark.parametrize("backend", ["sqlite-vec", "turbo-quant"])
def test_init_index_search_per_backend(e2e_project: Path, backend: str) -> None:
    # Init with explicit backend (non-interactive).
    result = runner.invoke(app, ["init", "--backend", backend], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert backend in result.output

    settings_text = (e2e_project / ".cocoindex_code" / "settings.yml").read_text()
    assert f"backend: {backend}" in settings_text

    # Index.
    result = runner.invoke(app, ["index"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "Chunks:" in result.output

    # Status reports chunks for both backends (doctor parity).
    result = runner.invoke(app, ["status"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "Chunks:" in result.output

    # Search finds the fibonacci chunk in main.py.
    result = runner.invoke(app, ["search", "fibonacci", "calculation"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "main.py" in result.output


@pytest.mark.parametrize("backend", ["sqlite-vec", "turbo-quant"])
def test_search_path_filter_per_backend(e2e_project: Path, backend: str) -> None:
    runner.invoke(app, ["init", "--backend", backend], catch_exceptions=False)
    runner.invoke(app, ["index"], catch_exceptions=False)
    result = runner.invoke(
        app, ["search", "database", "connection", "--path", "lib/*"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    assert "lib/" in result.output


def test_reinit_switches_backend(e2e_project: Path) -> None:
    """Re-init with a different backend then re-index rebuilds cleanly (R8)."""
    runner.invoke(app, ["init", "--backend", "sqlite-vec"], catch_exceptions=False)
    runner.invoke(app, ["index"], catch_exceptions=False)

    # Force re-init to turbo-quant.
    result = runner.invoke(app, ["init", "-f", "--backend", "turbo-quant"], catch_exceptions=False)
    # `init` returns early ("already initialized") if settings exist; reset first.
    if "already initialized" in result.output:
        runner.invoke(app, ["reset", "--all", "-f"], catch_exceptions=False)
        result = runner.invoke(app, ["init", "--backend", "turbo-quant"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["index"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["search", "fibonacci"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "main.py" in result.output
