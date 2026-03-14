"""Unit tests for shared CLI helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from cocoindex_code.cli import require_project_root, resolve_default_path


# 39
def test_require_project_root_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    (project / ".cocoindex_code").mkdir(parents=True)
    (project / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []")
    subdir = project / "src"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
    assert require_project_root() == project


# 40
def test_require_project_root_exits_when_not_initialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    monkeypatch.chdir(standalone)
    from click.exceptions import Exit

    with pytest.raises(Exit):
        require_project_root()


# 41
def test_resolve_default_path_from_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    subdir = project_root / "src" / "lib"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)
    result = resolve_default_path(project_root)
    assert result == "src/lib/*"


# 42
def test_resolve_default_path_from_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    result = resolve_default_path(project_root)
    assert result is None


# 43
def test_resolve_default_path_outside_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    result = resolve_default_path(project_root)
    assert result is None
