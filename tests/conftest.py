"""Pytest configuration and fixtures."""

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

# === Environment setup BEFORE any cocoindex_code imports ===
# Create test directory and set it BEFORE any module imports
_TEST_DIR = Path(tempfile.mkdtemp(prefix="cocoindex_test_"))
os.environ["COCOINDEX_CODE_ROOT_PATH"] = str(_TEST_DIR)


@pytest.fixture(scope="session")
def test_codebase_root() -> Path:
    """Session-scoped test codebase directory."""
    return _TEST_DIR


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def coco_runtime() -> AsyncIterator[None]:
    """
    Set up CocoIndex project for the entire test session.

    Uses session-scoped event loop to ensure CocoIndex environment
    persists across all tests.
    """
    from cocoindex_code.project import default_project

    await default_project()
    yield
