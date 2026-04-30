"""Tests for multi_repo orchestration module."""

from pathlib import Path

from cocoindex_code.config import CodebaseConfig, DeclarationsConfig, GitHubConfig
from cocoindex_code.multi_repo import (
    DEFAULT_GITHUB_CACHE,
    DEFAULT_UNIFIED_ROOT,
    MultiRepoOrchestrator,
    read_changed_paths_file,
)


class TestMultiRepoBasics:
    """Test multi-repo orchestration basics."""

    def test_import_works(self):
        """Test that MultiRepoOrchestrator can be imported."""
        assert MultiRepoOrchestrator is not None

    def test_defaults_set(self):
        """Test that defaults are configured."""
        assert DEFAULT_UNIFIED_ROOT == Path.home() / ".cocoindex_code" / "unified_root"
        assert DEFAULT_GITHUB_CACHE == Path.home() / ".cocoindex_code" / "github_cache"

    def test_orchestrator_init_empty(self, tmp_path):
        """Test orchestrator initialization with empty config."""
        # Create minimal CodebaseConfig
        config = CodebaseConfig(
            repos=[],
            github=GitHubConfig(),
            declarations=DeclarationsConfig(),
        )
        config_file = tmp_path / "config.yml"
        config_file.write_text("repos: []")

        # Should initialize without error
        orchestrator = MultiRepoOrchestrator(
            config=config,
            config_path=config_file,
            unified_root=tmp_path / "unified",
            github_cache=tmp_path / "github",
        )
        assert orchestrator is not None
        assert orchestrator.unified_root == (tmp_path / "unified").resolve()
        assert orchestrator.config == config


def test_changed_paths_file_and_repo_inference(tmp_path):
    from cocoindex_code.config import RepoConfig, RepoType

    changed = tmp_path / "changed.txt"
    changed.write_text("\n# comment\napi/src/app.py\nweb/src/app.ts\napi/src/app.py\n")

    repo_api = RepoConfig(id="api", type=RepoType.local, path=str(tmp_path / "api"))
    repo_web = RepoConfig(id="web", type=RepoType.local, path=str(tmp_path / "web"))
    config = CodebaseConfig(repos=[repo_api, repo_web])
    orchestrator = MultiRepoOrchestrator(
        config=config,
        config_path=tmp_path / "cfg.yml",
        unified_root=tmp_path / "unified",
        github_cache=tmp_path / "cache",
        repo_root_hint=tmp_path,
    )

    paths = read_changed_paths_file(changed)

    assert paths == ["api/src/app.py", "web/src/app.ts"]
    assert orchestrator.repo_ids_for_changed_paths(paths) == ["api", "web"]
