from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cocoindex_code._daemon_paths import daemon_state_dir
from cocoindex_code.git_context import normalize_remote_url, resolve_worktree_context
from cocoindex_code.layer_store import LayerKind, LayerStore
from cocoindex_code.layered_project import LayeredProject
from cocoindex_code.settings import default_project_settings, save_project_settings


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    save_project_settings(path, default_project_settings())
    (path / "main.py").write_text("def base_function() -> str:\n    return 'base'\n")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "initial")
    _git(path, "remote", "add", "origin", "git@github.com:Example/Repo.git")
    return path


def test_daemon_state_dir_defaults_to_xdg_data_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COCOINDEX_CODE_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    assert daemon_state_dir() == tmp_path / "xdg" / "cocoindex-code"


def test_normalize_remote_url_equates_common_github_forms() -> None:
    assert normalize_remote_url("git@github.com:Example/Repo.git") == normalize_remote_url(
        "https://github.com/example/repo"
    )


def test_resolve_worktree_context_has_stable_repo_id_across_worktrees(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-b", "feature", str(linked), "main")

    first = resolve_worktree_context(repo, base_ref="main", index_config_hash="cfg")
    second = resolve_worktree_context(linked, base_ref="main", index_config_hash="cfg")

    assert first.repo_id == second.repo_id
    assert first.worktree_id != second.worktree_id
    assert first.repo_root == repo.resolve()
    assert second.repo_root == linked.resolve()


def test_resolve_worktree_context_repo_id_survives_repo_move(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    moved = tmp_path / "moved-repo"
    shutil.copytree(repo, moved)

    first = resolve_worktree_context(repo, base_ref="main", index_config_hash="cfg")
    second = resolve_worktree_context(moved, base_ref="main", index_config_hash="cfg")

    assert first.repo_id == second.repo_id
    assert first.repo_root != second.repo_root


def test_resolve_worktree_context_worktree_id_uses_name_and_branch(tmp_path: Path) -> None:
    first_parent = tmp_path / "first"
    second_parent = tmp_path / "second"
    first_parent.mkdir()
    second_parent.mkdir()
    first = _init_repo(first_parent / "feature-1")
    second = _init_repo(second_parent / "feature-1")

    first_ctx = resolve_worktree_context(first, base_ref="main", index_config_hash="cfg")
    second_ctx = resolve_worktree_context(second, base_ref="main", index_config_hash="cfg")

    assert first_ctx.worktree_id == second_ctx.worktree_id
    assert first_ctx.repo_root != second_ctx.repo_root


def test_resolve_worktree_context_uses_configured_branch_upstream(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "branch", "-m", "master")
    origin_master = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/master", origin_master)
    _git(repo, "branch", "--set-upstream-to=origin/master", "master")
    (repo / "main.py").write_text("def changed() -> str:\n    return 'changed'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "local master advanced")

    ctx = resolve_worktree_context(repo, base_ref=None, index_config_hash="cfg")

    assert ctx.branch.base_ref == "origin/master"
    assert ctx.branch.base_commit == origin_master
    assert ctx.branch.head_commit != ctx.branch.base_commit


def test_resolve_worktree_context_uses_remote_head_when_no_branch_upstream(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    origin_default = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/upstream/default", origin_default)
    _git(repo, "symbolic-ref", "refs/remotes/upstream/HEAD", "refs/remotes/upstream/default")
    (repo / "main.py").write_text("def changed() -> str:\n    return 'changed'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "local branch advanced")

    ctx = resolve_worktree_context(repo, base_ref=None, index_config_hash="cfg")

    assert ctx.branch.base_ref == "upstream/default"
    assert ctx.branch.base_commit == origin_default


def test_layer_store_persists_ready_layers_and_manifests(tmp_path: Path) -> None:
    store = LayerStore(tmp_path / "daemon.db")
    record = store.upsert_layer(
        layer_id="layer-1",
        repo_id="repo",
        kind=LayerKind.BASE,
        ref_name="main",
        commit="abc",
        base_commit=None,
        base_layer_id=None,
        source_dir=tmp_path / "src",
        db_dir=tmp_path / "db",
        status="building",
    )
    store.replace_manifest(
        "layer-1",
        affected_paths=["a.py"],
        tombstoned_paths=["old.py"],
        expires_at=None,
    )
    store.mark_layer_ready("layer-1")

    reopened = LayerStore(tmp_path / "daemon.db")
    ready = reopened.get_layer("layer-1")
    assert ready is not None
    assert ready.layer_id == record.layer_id
    assert ready.status == "ready"
    manifest = reopened.get_manifest("layer-1")
    assert manifest is not None
    assert manifest.affected_paths == frozenset({"a.py"})
    assert manifest.tombstoned_paths == frozenset({"old.py"})


def test_layer_store_persists_overlay_policy(tmp_path: Path) -> None:
    store = LayerStore(tmp_path / "daemon.db")

    store.upsert_overlay_policy(repo_id="repo", base_ref="main")

    reopened = LayerStore(tmp_path / "daemon.db")
    assert reopened.get_overlay_base_ref("repo") == "main"


@pytest.mark.asyncio
async def test_layered_project_creates_base_and_branch_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from conftest import make_test_user_settings

    from cocoindex_code.daemon import _resolve_chunker_registry
    from cocoindex_code.embedder_params import resolve_embedder_params
    from cocoindex_code.shared import create_embedder

    monkeypatch.setenv("COCOINDEX_CODE_STATE_DIR", str(tmp_path / "state"))
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "checkout", "-b", "feature")
    (repo / "main.py").write_text("def branch_function() -> str:\n    return 'branch'\n")
    (repo / "extra.py").write_text("def extra() -> str:\n    return 'extra'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature")

    user_settings = make_test_user_settings()
    params = resolve_embedder_params(user_settings.embedding)
    state_dir = daemon_state_dir()
    project = LayeredProject(
        project_root=repo,
        cwd=repo,
        base_ref="main",
        state_dir=state_dir,
        store=LayerStore(state_dir / "daemon.db"),
        embedder=create_embedder(user_settings.embedding, indexing_params=params.indexing),
        indexing_params=params.indexing,
        query_params=params.query,
        chunker_registry=_resolve_chunker_registry(default_project_settings().chunkers),
        project_cache={},
    )

    await project.run_index()

    layers = project.store.list_layers()
    assert {layer.kind for layer in layers} == {LayerKind.BASE, LayerKind.BRANCH}
    branch_layer = next(layer for layer in layers if layer.kind == LayerKind.BRANCH)
    manifest = project.store.get_manifest(branch_layer.layer_id)
    assert manifest is not None
    assert manifest.affected_paths == frozenset({"extra.py", "main.py"})


@pytest.mark.asyncio
async def test_layered_project_builds_from_nearest_indexed_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any

    import cocoindex_code.layers.layer_stack as layer_stack
    from cocoindex_code.protocol import IndexingProgress

    class FakeRuntime:
        def __init__(self, layer: Any) -> None:
            self.layer = layer
            self.project = object()

        async def run_index(self, on_progress: object = None) -> None:
            self.layer.paths.target_sqlite.parent.mkdir(parents=True, exist_ok=True)
            self.layer.paths.target_sqlite.touch()
            if on_progress is not None:
                on_progress(IndexingProgress(1, 0, 1, 0, 0, 0))

    async def fake_runtime_create(**kwargs: Any) -> FakeRuntime:
        return FakeRuntime(kwargs["layer"])

    monkeypatch.setattr(
        layer_stack.LayerRuntime,
        "create",
        staticmethod(fake_runtime_create),
    )

    monkeypatch.setenv("COCOINDEX_CODE_STATE_DIR", str(tmp_path / "state"))
    repo = _init_repo(tmp_path / "repo")
    base_commit = _git(repo, "rev-parse", "HEAD")
    (repo / "main.py").write_text("def base_function() -> str:\n    return 'master'\n")
    (repo / "master.py").write_text("def master_only() -> str:\n    return 'master'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "master")
    master_head = _git(repo, "rev-parse", "HEAD")

    state_dir = daemon_state_dir()
    store = LayerStore(state_dir / "daemon.db")

    def make_project() -> LayeredProject:
        return LayeredProject(
            project_root=repo,
            cwd=repo,
            base_ref=base_commit,
            state_dir=state_dir,
            store=store,
            embedder=object(),
            indexing_params={},
            query_params={},
            chunker_registry={},
            project_cache={},
        )

    master_project = make_project()
    try:
        master_layers = await master_project.ensure_layer_results()
    finally:
        master_project.close()
    master_layer = next(layer for layer in master_layers if layer.layer.kind == LayerKind.BRANCH)
    assert master_layer.layer.commit_hash == master_head

    _git(repo, "checkout", "-b", "feature")
    (repo / "feature.py").write_text("def feature_only() -> str:\n    return 'feature'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature")
    feature_head = _git(repo, "rev-parse", "HEAD")

    feature_project = make_project()
    try:
        feature_layers = await feature_project.ensure_layer_results()
    finally:
        feature_project.close()

    assert [layer.layer.kind for layer in feature_layers] == [
        LayerKind.BRANCH,
        LayerKind.BRANCH,
        LayerKind.BASE,
    ]
    feature_layer, reused_master_layer, _base_layer = feature_layers
    assert feature_layer.built is True
    assert reused_master_layer.built is False
    assert reused_master_layer.layer.id == master_layer.layer.id
    assert feature_layer.layer.commit_hash == feature_head
    assert feature_layer.layer.base_commit_hash == master_head
    assert feature_layer.layer.base_layer_id == master_layer.layer.id
    assert feature_layer.manifest.affected_paths == frozenset({"feature.py"})
    assert feature_layer.manifest.tombstoned_paths == frozenset()
