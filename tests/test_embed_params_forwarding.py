"""Tests that indexing_params / query_params are forwarded to embedder.embed().

Uses a stub embedder that records kwargs on each call, wired up via a minimal
``Project.create()`` so the context-var plumbing is exercised end-to-end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from cocoindex_code.project import Project
from cocoindex_code.settings import (
    ProjectSettings,
    save_project_settings,
)
from cocoindex_code.shared import Embedder


class _KwargRecordingEmbedder:
    """Stub that records each embed() call's kwargs for assertions."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def embed(self, text: str, **kwargs: Any) -> np.ndarray[Any, Any]:
        self.calls.append(dict(kwargs))
        return np.zeros(8, dtype=np.float32)

    async def _get_dim(self) -> int:
        return 8

    async def __coco_vector_schema__(self) -> Any:
        from cocoindex.resources import schema as _schema

        return _schema.VectorSchema(dtype=np.dtype(np.float32), size=8)

    def __coco_memo_key__(self) -> object:
        return ("stub", id(self))


@pytest.mark.asyncio
async def test_indexing_params_forwarded_to_embed(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    save_project_settings(
        project_root,
        ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=[]),
    )
    (project_root / "a.py").write_text("def foo():\n    return 1\n")

    stub = _KwargRecordingEmbedder()
    project = await Project.create(
        project_root,
        cast(Embedder, stub),
        indexing_params={"prompt_name": "passage"},
        query_params={"prompt_name": "query"},
    )
    await project.run_index()

    assert stub.calls, "embedder.embed was never called during indexing"
    for call in stub.calls:
        assert call.get("prompt_name") == "passage", (
            f"expected prompt_name=passage during indexing, got kwargs={call}"
        )


@pytest.mark.asyncio
async def test_query_params_forwarded_to_embed(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    save_project_settings(
        project_root,
        ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=[]),
    )
    (project_root / "a.py").write_text("def foo():\n    return 1\n")

    stub = _KwargRecordingEmbedder()
    project = await Project.create(
        project_root,
        cast(Embedder, stub),
        indexing_params={"prompt_name": "passage"},
        query_params={"prompt_name": "query"},
    )
    await project.run_index()

    # Clear indexing calls; search should add at least one call with the query params.
    stub.calls.clear()
    await project.search(query="foo")
    assert stub.calls, "embedder.embed was never called during search"
    assert stub.calls[0].get("prompt_name") == "query", (
        f"expected prompt_name=query during search, got kwargs={stub.calls[0]}"
    )
