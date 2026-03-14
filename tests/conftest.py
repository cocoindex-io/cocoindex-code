"""Pytest configuration and fixtures."""

import os
import tempfile
from pathlib import Path

import pytest

# === Environment setup BEFORE any cocoindex_code imports ===
_TEST_DIR = Path(tempfile.mkdtemp(prefix="cocoindex_test_"))
os.environ["COCOINDEX_CODE_ROOT_PATH"] = str(_TEST_DIR)


@pytest.fixture(scope="session")
def test_codebase_root() -> Path:
    """Session-scoped test codebase directory."""
    return _TEST_DIR
