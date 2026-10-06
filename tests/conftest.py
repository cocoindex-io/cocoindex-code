"""Pytest configuration and fixtures."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
from cocoindex.resources.schema import VectorSchema

if TYPE_CHECKING:
    from cocoindex_code.daemon import ProjectRegistry
    from cocoindex_code.protocol import IndexingProgress
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


@pytest.fixture(autouse=True)
async def _empty_context_default_executor() -> None:
    """Give each test's event loop the default executor the daemon's loop has."""
    from cocoindex_code.daemon import EmptyContextThreadPoolExecutor

    asyncio.get_running_loop().set_default_executor(EmptyContextThreadPoolExecutor())


@pytest.fixture(scope="session")
def test_codebase_root() -> Path:
    """Session-scoped test codebase directory."""
    return _TEST_DIR


_STUB_EMBED_DIM = 4  # tiny dimension — enough to satisfy the vector table schema


class StubEmbedder:
    """Zero-vector embedder for indexing tests that don't need a real model.

    Records the kwargs of each ``embed()`` call in ``calls``.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __coco_memo_key__(self) -> str:
        return "stub-embedder"

    async def __coco_vector_schema__(self) -> VectorSchema:
        return VectorSchema(dtype=np.dtype("float32"), size=_STUB_EMBED_DIM)

    async def embed(self, text: str, **kwargs: Any) -> np.ndarray:
        self.calls.append(kwargs)
        return np.zeros(_STUB_EMBED_DIM, dtype=np.float32)


@pytest.fixture
def stub_embedder() -> StubEmbedder:
    return StubEmbedder()


async def index_project(registry: ProjectRegistry, project_root: Path) -> IndexingProgress:
    """Run one index pass through the daemon's registry; return the final file stats."""
    project = await registry.get_project(str(project_root))
    progress: list[IndexingProgress] = []
    await project.run_index(on_progress=progress.append)
    return progress[-1]


def assert_all_unchanged(stats: IndexingProgress, num_files: int) -> None:
    assert stats.num_unchanged == num_files
    assert stats.num_adds == stats.num_reprocesses == stats.num_deletes == 0
