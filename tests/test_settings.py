"""Unit tests for the settings module."""

from __future__ import annotations

from pathlib import Path

import pytest

from cocoindex_code.settings import (
    DEFAULT_EXCLUDED_PATTERNS,
    DEFAULT_INCLUDED_PATTERNS,
    EmbeddingSettings,
    LanguageOverride,
    ProjectSettings,
    UserSettings,
    default_project_settings,
    default_user_settings,
    find_parent_with_marker,
    find_project_root,
    load_project_settings,
    load_user_settings,
    save_project_settings,
    save_user_settings,
)


@pytest.fixture()
def _patch_user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect user_settings_dir() to a temp directory."""
    monkeypatch.setattr(
        "cocoindex_code.settings.user_settings_dir",
        lambda: tmp_path / ".cocoindex_code",
    )
    monkeypatch.setattr(
        "cocoindex_code.settings.user_settings_path",
        lambda: tmp_path / ".cocoindex_code" / "global_settings.yml",
    )


def test_default_user_settings() -> None:
    s = default_user_settings()
    assert s.embedding.provider == "sentence-transformers"
    assert "all-MiniLM-L6-v2" in s.embedding.model
    assert s.embedding.device is None
    assert s.envs == {}


def test_default_project_settings() -> None:
    s = default_project_settings()
    assert s.include_patterns == DEFAULT_INCLUDED_PATTERNS
    assert s.exclude_patterns == DEFAULT_EXCLUDED_PATTERNS
    assert s.language_overrides == []


@pytest.mark.usefixtures("_patch_user_dir")
def test_save_and_load_user_settings(tmp_path: Path) -> None:
    settings = UserSettings(
        embedding=EmbeddingSettings(
            provider="litellm",
            model="gemini/text-embedding-004",
            device="cpu",
        ),
        envs={"GEMINI_API_KEY": "test-key"},
    )
    save_user_settings(settings)
    loaded = load_user_settings()
    assert loaded.embedding.provider == settings.embedding.provider
    assert loaded.embedding.model == settings.embedding.model
    assert loaded.embedding.device == settings.embedding.device
    assert loaded.envs == settings.envs


def test_save_and_load_project_settings(tmp_path: Path) -> None:
    settings = ProjectSettings(
        include_patterns=["**/*.py", "**/*.rs"],
        exclude_patterns=["**/target"],
        language_overrides=[LanguageOverride(ext="inc", lang="php")],
    )
    save_project_settings(tmp_path, settings)
    loaded = load_project_settings(tmp_path)
    assert loaded.include_patterns == settings.include_patterns
    assert loaded.exclude_patterns == settings.exclude_patterns
    assert len(loaded.language_overrides) == 1
    assert loaded.language_overrides[0].ext == "inc"
    assert loaded.language_overrides[0].lang == "php"


@pytest.mark.usefixtures("_patch_user_dir")
def test_load_user_settings_missing_file_returns_defaults() -> None:
    loaded = load_user_settings()
    expected = default_user_settings()
    assert loaded.embedding.provider == expected.embedding.provider
    assert loaded.embedding.model == expected.embedding.model
    assert loaded.envs == expected.envs


def test_load_project_settings_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_project_settings(tmp_path)


def test_find_project_root_from_subdirectory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".cocoindex_code").mkdir(parents=True)
    (project / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []")
    subdir = project / "src" / "lib"
    subdir.mkdir(parents=True)
    assert find_project_root(subdir) == project


def test_find_project_root_from_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".cocoindex_code").mkdir(parents=True)
    (project / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []")
    assert find_project_root(project) == project


def test_find_project_root_returns_none_when_not_initialized(tmp_path: Path) -> None:
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    assert find_project_root(standalone) is None


def test_find_parent_with_marker_finds_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    subdir = repo / "src"
    subdir.mkdir()
    assert find_parent_with_marker(subdir) == repo


def test_find_parent_with_marker_prefers_cocoindex_code(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".cocoindex_code").mkdir(parents=True)
    subdir = repo / "src"
    subdir.mkdir()
    assert find_parent_with_marker(subdir) == repo


@pytest.mark.usefixtures("_patch_user_dir")
def test_user_settings_litellm_round_trip() -> None:
    settings = UserSettings(
        embedding=EmbeddingSettings(
            provider="litellm",
            model="gemini/text-embedding-004",
        ),
        envs={"GEMINI_API_KEY": "test"},
    )
    save_user_settings(settings)
    loaded = load_user_settings()
    assert loaded.embedding.provider == "litellm"
    assert loaded.embedding.model == "gemini/text-embedding-004"
    assert loaded.envs == {"GEMINI_API_KEY": "test"}


def test_project_settings_with_language_overrides(tmp_path: Path) -> None:
    settings = ProjectSettings(
        language_overrides=[LanguageOverride(ext="inc", lang="php")],
    )
    save_project_settings(tmp_path, settings)
    loaded = load_project_settings(tmp_path)
    assert len(loaded.language_overrides) == 1
    assert loaded.language_overrides[0].ext == "inc"
    assert loaded.language_overrides[0].lang == "php"
