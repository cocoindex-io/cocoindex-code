"""Pytest configuration and fixtures."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from cocoindex_code.settings import UserSettings

# === Environment setup BEFORE any cocoindex_code imports ===
_TEST_DIR = Path(tempfile.mkdtemp(prefix="cocoindex_test_"))
os.environ["COCOINDEX_CODE_ROOT_PATH"] = str(_TEST_DIR)


# Lighter than the production default (Snowflake/snowflake-arctic-embed-xs)
# so tests keep CI cache costs low while still exercising the full embedder
# code path.
TEST_EMBEDDING_MODEL = "sentence-transformers/paraphrase-MiniLM-L3-v2"


# NOTE: deliberately NOT prefixed with `test_` — pytest auto-collects any
# top-level `test_*` function as a test case.
def make_test_user_settings() -> UserSettings:
    """Lightweight UserSettings for tests — uses a smaller model than the production default."""
    from cocoindex_code.settings import EmbeddingSettings, UserSettings

    return UserSettings(
        embedding=EmbeddingSettings(
            provider="sentence-transformers",
            model=TEST_EMBEDDING_MODEL,
        ),
    )


@pytest.fixture(scope="session")
def test_codebase_root() -> Path:
    """Session-scoped test codebase directory."""
    return _TEST_DIR


# ---------------------------------------------------------------------------
# Keep the test suite fast by skipping integration / e2e tests by default.
# Set RUN_SLOW_TESTS=1 in the environment to run the full suite (including e2e).
# ---------------------------------------------------------------------------
RUN_SLOW = os.getenv("RUN_SLOW_TESTS", "0").lower() in ("1", "true", "yes")


def pytest_collection_modifyitems(config, items):
    """Skip end-to-end and integration tests by default to keep the suite fast.

    Heuristic: tests under directories named 'e2e' or 'e2e_docker', tests whose
    filename starts with 'test_e2e', or filenames containing 'integration' are
    considered slow/integration and are skipped unless RUN_SLOW_TESTS=1.
    """
    if RUN_SLOW:
        return

    skip = pytest.mark.skip(
        reason="Skipping integration/e2e tests by default; set RUN_SLOW_TESTS=1 to run them"
    )
    for item in list(items):
        p = Path(str(item.fspath))
        parts = [pp.lower() for pp in p.parts]
        name = p.name.lower()

        if (
            "e2e" in parts
            or "e2e_docker" in parts
            or name.startswith("test_e2e")
            or "test_integration" in name
            or "e2e_" in name
        ):
            item.add_marker(skip)
