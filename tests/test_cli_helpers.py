"""Unit tests for shared CLI helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from cocoindex_code.cli import (
    add_to_gitignore,
    remove_from_gitignore,
    require_project_root,
    resolve_default_path,
)


def test_require_project_root_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    (project / ".cocoindex_code").mkdir(parents=True)
    (project / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []")
    subdir = project / "src"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
    # Create global settings so require_project_root doesn't reject
    settings_dir = tmp_path / "ccc_home"
    settings_dir.mkdir()
    (settings_dir / "global_settings.yml").write_text(
        "embedding:\n  model: test\n  provider: litellm\n"
    )
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(settings_dir))
    assert require_project_root() == project


def test_require_project_root_exits_when_not_initialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    monkeypatch.chdir(standalone)
    # Create global settings so we test the "no project" check, not "no global settings"
    settings_dir = tmp_path / "ccc_home"
    settings_dir.mkdir()
    (settings_dir / "global_settings.yml").write_text(
        "embedding:\n  model: test\n  provider: litellm\n"
    )
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(settings_dir))
    from click.exceptions import Exit

    with pytest.raises(Exit):
        require_project_root()


def test_resolve_default_path_from_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    subdir = project_root / "src" / "lib"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)
    result = resolve_default_path(project_root)
    assert result == "src/lib/*"


def test_resolve_default_path_from_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    result = resolve_default_path(project_root)
    assert result is None


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


# ---------------------------------------------------------------------------
# .gitignore helpers
# ---------------------------------------------------------------------------


def test_add_to_gitignore_creates_file(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    add_to_gitignore(tmp_path)
    gitignore = tmp_path / ".gitignore"
    assert gitignore.is_file()
    content = gitignore.read_text()
    assert "# CocoIndex Code (ccc)" in content
    assert "/.cocoindex_code/" in content


def test_add_to_gitignore_appends_to_existing(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.pyc\n")
    add_to_gitignore(tmp_path)
    content = gitignore.read_text()
    assert "*.pyc" in content
    assert "/.cocoindex_code/" in content


def test_add_to_gitignore_idempotent(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("/.cocoindex_code/\n")
    add_to_gitignore(tmp_path)
    content = gitignore.read_text()
    assert content.count("/.cocoindex_code/") == 1


def test_add_to_gitignore_skips_when_no_git(tmp_path: Path) -> None:
    add_to_gitignore(tmp_path)
    assert not (tmp_path / ".gitignore").exists()


def test_remove_from_gitignore(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.pyc\n# CocoIndex Code (ccc)\n/.cocoindex_code/\n__pycache__/\n")
    remove_from_gitignore(tmp_path)
    content = gitignore.read_text()
    assert "/.cocoindex_code/" not in content
    assert "# CocoIndex Code (ccc)" not in content
    assert "*.pyc" in content
    assert "__pycache__/" in content


def test_remove_from_gitignore_no_entry(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    original = "*.pyc\n__pycache__/\n"
    gitignore.write_text(original)
    remove_from_gitignore(tmp_path)
    assert gitignore.read_text() == original
