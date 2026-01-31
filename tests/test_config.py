"""Tests for configuration module."""

from pathlib import Path

import pytest


class TestCodebaseRootDiscovery:
    """Tests for codebase root discovery logic - this is non-trivial."""

    def test_prefers_cocoindex_code_over_git(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should prefer .cocoindex_code directory over .git when both exist."""
        from cocoindex_code.config import _discover_codebase_root

        # Create both markers in parent
        parent = tmp_path / "project"
        parent.mkdir()
        (parent / ".cocoindex_code").mkdir()
        (parent / ".git").mkdir()

        # Run from a subdirectory
        subdir = parent / "src" / "lib"
        subdir.mkdir(parents=True)

        monkeypatch.chdir(subdir)
        result = _discover_codebase_root()
        assert result == parent

    def test_finds_git_in_parent_hierarchy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should find .git in parent when deeply nested."""
        from cocoindex_code.config import _discover_codebase_root

        # Create .git at root level
        (tmp_path / ".git").mkdir()

        # Create deep nesting
        deep_dir = tmp_path / "a" / "b" / "c" / "d" / "e"
        deep_dir.mkdir(parents=True)

        monkeypatch.chdir(deep_dir)
        result = _discover_codebase_root()
        assert result == tmp_path

    def test_falls_back_to_cwd_when_no_markers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should fall back to cwd when no .git or .cocoindex_code found."""
        from cocoindex_code.config import _discover_codebase_root

        # Create empty directory with no markers
        empty_dir = tmp_path / "standalone"
        empty_dir.mkdir()

        monkeypatch.chdir(empty_dir)
        result = _discover_codebase_root()
        assert result == empty_dir
