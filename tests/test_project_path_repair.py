from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from cocoindex_code.project import Project
from cocoindex_code.settings import ProjectSettings, save_project_settings
from cocoindex_code.shared import Embedder


class _StubEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def embed(self, text: str, **kwargs: Any) -> np.ndarray[Any, Any]:
        self.calls.append((text, dict(kwargs)))
        return np.zeros(8, dtype=np.float32)

    async def __coco_vector_schema__(self) -> Any:
        from cocoindex.resources import schema as _schema

        return _schema.VectorSchema(dtype=np.dtype(np.float32), size=8)

    def __coco_memo_key__(self) -> object:
        return ("stub", id(self))


@pytest.mark.asyncio
async def test_project_create_repairs_broken_cocoindex_db_symlink(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    save_project_settings(
        project_root,
        ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=[]),
    )
    (project_root / "a.py").write_text("def foo():\n    return 1\n")

    broken_target = tmp_path / "shared" / "index" / "cocoindex.db"
    broken_target.parent.mkdir(parents=True)
    cocoindex_db = project_root / ".cocoindex_code" / "cocoindex.db"
    cocoindex_db.parent.mkdir(parents=True, exist_ok=True)
    cocoindex_db.symlink_to(broken_target)

    project = await Project.create(
        project_root,
        cast(Embedder, _StubEmbedder()),
        indexing_params={},
        query_params={},
    )
    project.close()

    assert cocoindex_db.exists()
    assert cocoindex_db.is_dir()


@pytest.mark.asyncio
async def test_project_with_existing_index_is_queryable_without_auto_indexing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    save_project_settings(
        project_root,
        ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=[]),
    )
    (project_root / "a.py").write_text("def foo():\n    return 1\n")
    index_dir = project_root / ".cocoindex_code"
    index_dir.mkdir(exist_ok=True)
    (index_dir / "target_sqlite.db").write_bytes(b"non-empty")

    project = await Project.create(
        project_root,
        cast(Embedder, _StubEmbedder()),
        indexing_params={},
        query_params={},
    )

    called = False

    async def fail_run_index(*args: Any, **kwargs: Any) -> None:
        nonlocal called
        called = True
        raise AssertionError("run_index should not be called for an existing index")

    monkeypatch.setattr(project, "run_index", fail_run_index)
    await project.ensure_indexing_started()
    project.close()

    assert called is False


@pytest.mark.asyncio
async def test_project_query_cache_key_uses_cached_normalized_params(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    save_project_settings(
        project_root,
        ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=[]),
    )

    project = await Project.create(
        project_root,
        cast(Embedder, _StubEmbedder()),
        indexing_params={},
        query_params={"input_type": "query", "truncate": 128},
    )
    try:
        key = project._query_cache_key("hello")
        assert key == (
            "hello",
            (("input_type", "'query'"), ("truncate", "128")),
        )
    finally:
        project.close()


@pytest.mark.asyncio
async def test_project_coalesces_concurrent_query_embedding_requests(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    save_project_settings(
        project_root,
        ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=[]),
    )

    gate = asyncio.Event()

    class _BlockingEmbedder(_StubEmbedder):
        async def embed(self, text: str, **kwargs: Any) -> np.ndarray[Any, Any]:
            self.calls.append((text, dict(kwargs)))
            await gate.wait()
            return np.zeros(8, dtype=np.float32)

    embedder = _BlockingEmbedder()
    project = await Project.create(
        project_root,
        cast(Embedder, embedder),
        indexing_params={},
        query_params={"input_type": "query"},
    )
    try:
        first = asyncio.create_task(project._get_query_embedding("hello"))
        second = asyncio.create_task(project._get_query_embedding("hello"))
        for _ in range(10):
            if embedder.calls:
                break
            await asyncio.sleep(0)
        assert len(embedder.calls) == 1

        gate.set()
        await asyncio.gather(first, second)
        assert len(embedder.calls) == 1
    finally:
        project.close()
