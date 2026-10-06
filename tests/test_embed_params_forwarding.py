"""Tests that indexing_params / query_params are forwarded to embedder.embed().

Uses a stub embedder that records kwargs on each call, wired up via a minimal
``Project.create()`` so the context-var plumbing is exercised end-to-end.
"""

from __future__ import annotations

from pathlib import Path

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
