"""Index-freshness tests.

A project reports stale only when its source tree no longer matches the
fingerprint recorded by the last successful index pass. These use
``Project.create`` with a stub embedder so no embedding model is loaded, and
drive a real index run.
"""

from __future__ import annotations

import gc
from pathlib import Path

from conftest import StubEmbedder

from cocoindex_code.project import Project
from cocoindex_code.settings import (
    ProjectSettings,
    index_state_path,
    save_project_settings,
)


def _settings() -> ProjectSettings:
    return ProjectSettings(include_patterns=["**/*.py"], exclude_patterns=["**/.cocoindex_code"])


async def _create_project(project_root: Path) -> Project:
    save_project_settings(project_root, _settings())
    return await Project.create(project_root, StubEmbedder(), indexing_params={}, query_params={})


async def _reopen(project_root: Path) -> Project:
    """Load a project again, as a daemon restart does."""
    return await _create_project(project_root)


def _close_and_release(project: Project) -> None:
    """Release a project's handles. The core CocoIndex environment is process
    global and only freed once the owning Project is garbage collected, so the
    caller must not keep a reference."""
    project.close()


async def test_status_is_fresh_after_a_successful_index(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def one():\n    return 1\n")
    project = await _create_project(tmp_path)

    await project.run_index()

    status = project.get_status()
    assert status.index_exists is True
    assert status.stale is False
    assert status.indexed_at_us is not None
    assert index_state_path(tmp_path).is_file()


async def test_editing_a_file_marks_the_index_stale(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("def one():\n    return 1\n")
    project = await _create_project(tmp_path)
    await project.run_index()
    assert project.get_status().stale is False

    source.write_text("def one():\n    return 1\n\n\ndef two():\n    return 2\n")

    assert project.get_status().stale is True

    await project.run_index()
    assert project.get_status().stale is False


async def test_adding_an_indexable_file_marks_the_index_stale(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def one():\n    return 1\n")
    project = await _create_project(tmp_path)
    await project.run_index()

    (tmp_path / "extra.py").write_text("def extra():\n    return 0\n")

    assert project.get_status().stale is True


async def test_editing_project_settings_marks_the_index_stale(tmp_path: Path) -> None:
    """The include patterns decide what the index covers, so they are fingerprinted."""
    (tmp_path / "main.py").write_text("def one():\n    return 1\n")
    (tmp_path / "notes.md").write_text("# notes\n")
    project = await _create_project(tmp_path)
    await project.run_index()

    save_project_settings(
        tmp_path,
        ProjectSettings(
            include_patterns=["**/*.py", "**/*.md"], exclude_patterns=["**/.cocoindex_code"]
        ),
    )

    assert project.get_status().stale is True


async def test_freshness_survives_reloading_the_project(tmp_path: Path) -> None:
    """Freshness lives on disk, so a daemon restart does not report a false stale."""
    (tmp_path / "main.py").write_text("def one():\n    return 1\n")
    project = await _create_project(tmp_path)
    await project.run_index()
    indexed_at_us = project.get_status().indexed_at_us
    _close_and_release(project)
    del project
    gc.collect()

    reopened = await _reopen(tmp_path)

    status = reopened.get_status()
    assert status.stale is False
    assert status.indexed_at_us == indexed_at_us


async def test_missing_state_reports_stale(tmp_path: Path) -> None:
    """An index built by an older version has no state file: freshness is unknown."""
    (tmp_path / "main.py").write_text("def one():\n    return 1\n")
    project = await _create_project(tmp_path)
    await project.run_index()
    index_state_path(tmp_path).unlink()
    _close_and_release(project)
    del project
    gc.collect()

    reopened = await _reopen(tmp_path)

    assert reopened.get_status().stale is True


async def test_unreadable_state_reports_stale(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def one():\n    return 1\n")
    project = await _create_project(tmp_path)
    await project.run_index()
    index_state_path(tmp_path).write_text("not json")
    _close_and_release(project)
    del project
    gc.collect()

    reopened = await _reopen(tmp_path)

    assert reopened.get_status().stale is True
