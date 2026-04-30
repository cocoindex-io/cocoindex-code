from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from cocoindex_code.project import Project
from cocoindex_code.settings import ProjectSettings, save_project_settings
from cocoindex_code.shared import Embedder


class _StubEmbedder:
    async def embed(self, text: str, **kwargs: Any) -> np.ndarray[Any, Any]:
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
