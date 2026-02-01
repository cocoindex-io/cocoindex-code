"""Pytest configuration and fixtures."""

import os
import tempfile
from pathlib import Path

import pytest

# === Environment setup BEFORE any imports ===
# This must happen before cocoindex_code modules are imported


# Use tiny model for faster tests
# os.environ["COCOINDEX_CODE_EMBEDDING_MODEL"] = "sentence-transformers/paraphrase-MiniLM-L3-v2"

# Create test directory and set it BEFORE any module imports
_TEST_DIR = Path(tempfile.mkdtemp(prefix="cocoindex_test_"))
os.environ["COCOINDEX_CODE_ROOT_PATH"] = str(_TEST_DIR)


@pytest.fixture(scope="session")
def test_codebase_root() -> Path:
    """Session-scoped test codebase directory."""
    return _TEST_DIR
