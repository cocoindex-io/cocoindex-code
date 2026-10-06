"""Editing embedding.indexing_params must re-embed unchanged files, and unchanged
params must not.

process_file is memoized on its arguments and on the change-detected context values
it reads. The indexing params used to be read without change detection, so a new
value (say ``prompt_name: passage``) left unchanged files with their old embeddings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import StubEmbedder, assert_all_unchanged, index_project

from cocoindex_code.daemon import ProjectRegistry
from cocoindex_code.settings import ProjectSettings, save_project_settings

_NUM_FILES = 2


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("def foo():\n    return 1\n")
    (root / "b.py").write_text("def bar():\n    return 2\n")
    save_project_settings(root, ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=[]))
    return root


def _restart_daemon(
    registry: ProjectRegistry, embedder: StubEmbedder, indexing_params: dict[str, Any]
) -> ProjectRegistry:
    """What a daemon restart after a global_settings.yml edit does."""
    registry.close_all()
    return ProjectRegistry(embedder, indexing_params=indexing_params)


@pytest.mark.parametrize(
    ("old_params", "new_params"),
    [
        ({}, {"prompt_name": "passage"}),
        ({"prompt_name": "query"}, {"prompt_name": "passage"}),
        ({"prompt_name": "passage"}, {}),
    ],
    ids=["added", "changed", "removed"],
)
async def test_indexing_params_change_reembeds_unchanged_files(
    old_params: dict[str, Any],
    new_params: dict[str, Any],
    project_root: Path,
    stub_embedder: StubEmbedder,
) -> None:
    registry = ProjectRegistry(stub_embedder, indexing_params=old_params)
    try:
        stats = await index_project(registry, project_root)
        assert stats.num_adds == _NUM_FILES
        assert stub_embedder.calls
        assert all(call == old_params for call in stub_embedder.calls)

        # Same file bytes; only the params changed.
        stub_embedder.calls.clear()
        registry = _restart_daemon(registry, stub_embedder, new_params)
        stats = await index_project(registry, project_root)
        assert stats.num_reprocesses == _NUM_FILES
        assert stub_embedder.calls
        assert all(call == new_params for call in stub_embedder.calls)
    finally:
        registry.close_all()


async def test_unchanged_indexing_params_leave_files_unchanged(
    project_root: Path, stub_embedder: StubEmbedder
) -> None:
    registry = ProjectRegistry(
        stub_embedder, indexing_params={"prompt_name": "passage", "normalize_embeddings": True}
    )
    try:
        stats = await index_project(registry, project_root)
        assert stats.num_adds == _NUM_FILES

        stub_embedder.calls.clear()
        assert_all_unchanged(await index_project(registry, project_root), _NUM_FILES)

        # Equal params rebuilt from settings on restart; key order doesn't matter.
        registry = _restart_daemon(
            registry, stub_embedder, {"normalize_embeddings": True, "prompt_name": "passage"}
        )
        assert_all_unchanged(await index_project(registry, project_root), _NUM_FILES)
        assert stub_embedder.calls == []
    finally:
        registry.close_all()
