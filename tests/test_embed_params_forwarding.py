"""Tests that indexing_params / query_params are forwarded to embedder.embed().

Uses a stub embedder that records kwargs on each call, wired up via a minimal
``Project.create()`` so the context-var plumbing is exercised end-to-end.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import StubEmbedder

from cocoindex_code.project import Project
from cocoindex_code.settings import (
    ProjectSettings,
    save_project_settings,
)


@pytest.mark.asyncio
async def test_indexing_params_forwarded_to_embed(
    tmp_path: Path, stub_embedder: StubEmbedder
) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    save_project_settings(
        project_root,
        ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=[]),
    )
    (project_root / "a.py").write_text("def foo():\n    return 1\n")

    project = await Project.create(
        project_root,
        stub_embedder,
        indexing_params={"prompt_name": "passage"},
        query_params={"prompt_name": "query"},
    )
    await project.run_index()

    assert stub_embedder.calls, "embedder.embed was never called during indexing"
    for call in stub_embedder.calls:
        assert call.get("prompt_name") == "passage", (
            f"expected prompt_name=passage during indexing, got kwargs={call}"
        )


@pytest.mark.asyncio
async def test_query_params_forwarded_to_embed(tmp_path: Path, stub_embedder: StubEmbedder) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    save_project_settings(
        project_root,
        ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=[]),
    )
    (project_root / "a.py").write_text("def foo():\n    return 1\n")

    project = await Project.create(
        project_root,
        stub_embedder,
        indexing_params={"prompt_name": "passage"},
        query_params={"prompt_name": "query"},
    )
    await project.run_index()

    # Clear indexing calls; search should add at least one call with the query params.
    stub_embedder.calls.clear()
    await project.search(query="foo")
    assert stub_embedder.calls, "embedder.embed was never called during search"
    assert stub_embedder.calls[0].get("prompt_name") == "query", (
        f"expected prompt_name=query during search, got kwargs={stub_embedder.calls[0]}"
    )


class _NaNQueryEmbedder(StubEmbedder):  # type: ignore[misc]
    """Returns a NaN query vector, as a broken embedding model might."""

    async def embed(self, text: str, **kwargs: Any) -> np.ndarray:
        vector: np.ndarray = await super().embed(text, **kwargs)
        if kwargs.get("prompt_name") == "query":
            vector[0] = np.nan
        return vector


@pytest.mark.asyncio
async def test_search_rejects_nan_query_vector(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    save_project_settings(
        project_root,
        ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=[]),
    )
    (project_root / "a.py").write_text("def foo():\n    return 1\n")

    project = await Project.create(
        project_root,
        _NaNQueryEmbedder(),
        indexing_params={"prompt_name": "passage"},
        query_params={"prompt_name": "query"},
    )
    await project.run_index()

    with pytest.raises(RuntimeError, match="NaN"):
        await project.search(query="foo", paths=["*.py"])


class _NaNChunkEmbedder(StubEmbedder):  # type: ignore[misc]
    """Returns a NaN vector for chunks that mention "broken", as a broken model might."""

    async def embed(self, text: str, **kwargs: Any) -> np.ndarray:
        vector: np.ndarray = await super().embed(text, **kwargs)
        if "broken" in text:
            vector[0] = np.nan
        return vector


@pytest.mark.asyncio
async def test_index_leaves_out_chunks_with_nan_vectors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A NaN vector in the index has no usable distance: a plain search's KNN
    plan returns it with a NULL distance, and the search fails (#270)."""
    project_root = tmp_path / "proj"
    project_root.mkdir()
    save_project_settings(
        project_root,
        ProjectSettings(include_patterns=["**/*.py", "**/*.rs"], exclude_patterns=[]),
    )
    (project_root / "a.py").write_text("def foo():\n    return 1\n")
    # Alone in its language partition, as in the report on #327.
    (project_root / "b.rs").write_text("fn broken() {}\n")

    project = await Project.create(
        project_root,
        _NaNChunkEmbedder(),
        indexing_params={"prompt_name": "passage"},
        query_params={"prompt_name": "query"},
    )
    with caplog.at_level(logging.WARNING, logger="cocoindex_code.indexer"):
        await project.run_index()

    results = await project.search(query="foo")
    assert [r.file_path for r in results] == ["a.py"]
    assert project.get_status().total_chunks == 1
    assert "Not indexing b.rs" in caplog.text
