from __future__ import annotations

from pathlib import Path

import cocoindex as coco
import pytest
from cocoindex.connectors import sqlite as coco_sqlite

from cocoindex_code import index_changes
from cocoindex_code.settings import ProjectSettings, save_project_settings


@pytest.mark.asyncio
async def test_find_index_changes_without_existing_index(tmp_path: Path) -> None:
    save_project_settings(
        tmp_path,
        ProjectSettings(
            include_patterns=["**/*.py"],
            exclude_patterns=[],
            max_file_size=15,
        ),
    )
    (tmp_path / "main.py").write_text("print('main')\n")
    (tmp_path / "oversized.py").write_text("x" * 16)
    (tmp_path / "notes.txt").write_text("not indexed\n")

    changes = await index_changes.find_index_changes(tmp_path)

    assert changes.added == ("main.py",)
    assert changes.updated == ()
    assert changes.deleted == ()


@pytest.mark.asyncio
async def test_find_index_changes_compares_persisted_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_project_settings(
        tmp_path,
        ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=[]),
    )
    (tmp_path / "existing.py").write_text("print('existing')\n")
    (tmp_path / "new.py").write_text("print('new')\n")

    async def indexed_paths(_project_root: Path) -> set[str]:
        return {"deleted.py", "existing.py"}

    monkeypatch.setattr(index_changes, "_indexed_paths", indexed_paths)

    changes = await index_changes.find_index_changes(tmp_path)

    assert changes.added == ("new.py",)
    assert changes.updated is None
    assert changes.deleted == ("deleted.py",)


@pytest.mark.asyncio
async def test_find_index_changes_compares_manifest_fingerprints(tmp_path: Path) -> None:
    save_project_settings(
        tmp_path,
        ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=[]),
    )
    (tmp_path / "modified.py").write_text("value = 1\n")
    (tmp_path / "deleted.py").write_text("value = 2\n")

    context = coco.ContextProvider()
    context.provide(index_changes.CODEBASE_DIR, tmp_path)
    target_db = coco_sqlite.connect(
        str(index_changes.target_sqlite_db_path(tmp_path)),
        load_vec=True,
    )
    index_changes.prepare_file_manifest(target_db)
    context.provide(index_changes.SQLITE_DB, target_db)
    env = coco.Environment(
        coco.Settings.from_env(index_changes.cocoindex_db_path(tmp_path)),
        context_provider=context,
    )
    app = index_changes.create_file_manifest_app(env)
    await app.update()
    data_path = index_changes.target_sqlite_db_path(tmp_path)
    data_before = data_path.read_bytes()

    (tmp_path / "modified.py").write_text("value = 3\n")
    (tmp_path / "deleted.py").unlink()
    (tmp_path / "added.py").write_text("value = 4\n")

    first = await index_changes.find_index_changes(tmp_path)
    second = await index_changes.find_index_changes(tmp_path)
    data_after = data_path.read_bytes()
    target_db.close()

    assert first == second
    assert first.added == ("added.py",)
    assert first.updated == ("modified.py",)
    assert first.deleted == ("deleted.py",)
    assert data_after == data_before
