from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cocoindex_code._daemon_paths import daemon_state_dir
from cocoindex_code.git_context import normalize_remote_url, resolve_worktree_context
from cocoindex_code.layer_store import LayerKind, LayerStore
from cocoindex_code.layered_project import LayeredProject
from cocoindex_code.protocol import IndexingProgress
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


class _FakeRuntime:
    def __init__(self, layer: Any) -> None:
        self.layer = layer
        self.project = object()

    async def run_index(self, on_progress: Any = None) -> None:
        self.layer.paths.target_sqlite.parent.mkdir(parents=True, exist_ok=True)
        self.layer.paths.target_sqlite.touch()
        if on_progress is not None:
            on_progress(IndexingProgress(1, 0, 1, 0, 0, 0))


async def _fake_runtime_create(**kwargs: Any) -> _FakeRuntime:
    return _FakeRuntime(kwargs["layer"])


def _install_fake_layer_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import cocoindex_code.layers.layer_stack as layer_stack

    monkeypatch.setattr(
        layer_stack.LayerRuntime,
        "create",
        staticmethod(_fake_runtime_create),
    )


def _fake_layered_project(
    *,
    repo: Path,
    base_ref: str,
    state_dir: Path,
    store: LayerStore,
) -> LayeredProject:
    return LayeredProject(
        project_root=repo,
        cwd=repo,
        base_ref=base_ref,
        state_dir=state_dir,
        store=store,
        embedder=object(),
        indexing_params={},
        query_params={},
        chunker_registry={},
        project_cache={},
    )


def _touch_layer_target(layer: Any) -> None:
    layer.paths.target_sqlite.parent.mkdir(parents=True, exist_ok=True)
    layer.paths.target_sqlite.touch()


def _upsert_ready_layer(
    *,
    store: LayerStore,
    state_dir: Path,
    repo_id: str,
    layer_id: str,
    kind: LayerKind,
    ref_name: str,
    commit: str,
    base_commit: str | None,
    base_layer_id: str | None,
    config_hash: str,
    affected_paths: list[str],
    tombstoned_paths: list[str] | None = None,
) -> Any:
    root = state_dir / "manual-layers" / layer_id
    layer = store.upsert_layer(
        layer_id=layer_id,
        repo_id=repo_id,
        kind=kind,
        ref_name=ref_name,
        commit=commit,
        base_commit=base_commit,
        base_layer_id=base_layer_id,
        source_dir=root / "src",
        db_dir=root / "db",
        status="building",
        config_hash=config_hash,
    )
    store.replace_manifest(
        layer_id,
        affected_paths=affected_paths,
        tombstoned_paths=tombstoned_paths or [],
        expires_at=None,
    )
    _touch_layer_target(layer)
    store.mark_layer_ready(layer_id)
    ready = store.get_layer(layer_id)
    assert ready is not None
    return ready


def test_daemon_state_dir_defaults_to_xdg_data_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COCOINDEX_CODE_STATE_DIR", raising=False)
    monkeypatch.delenv("COCOINDEX_CODE_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    assert daemon_state_dir() == tmp_path / "xdg" / "cocoindex-code"


def test_daemon_state_dir_uses_isolated_code_dir_without_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COCOINDEX_CODE_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(tmp_path / "code"))

    assert daemon_state_dir() == tmp_path / "code" / "state"


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


def test_ancestor_distances_only_returns_commits_reachable_from_head(tmp_path: Path) -> None:
    from cocoindex_code.version_control.git import ancestor_distances

    repo = _init_repo(tmp_path / "repo")
    base_commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "other")
    (repo / "other.py").write_text("def other() -> str:\n    return 'other'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "other")
    other_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")
    (repo / "main.py").write_text("def main() -> str:\n    return 'main'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "main")
    main_head = _git(repo, "rev-parse", "HEAD")

    assert ancestor_distances(
        repo,
        head=main_head,
        candidate_commits=[base_commit, other_head, main_head, "missing"],
    ) == {base_commit: 1, main_head: 0}


def test_dirty_snapshot_hash_streams_content_without_reading_file_into_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cocoindex_code.version_control.change_set import GitStatusEntry
    from cocoindex_code.version_control.git import _dirty_snapshot_hash

    repo = _init_repo(tmp_path / "repo")
    dirty_path = repo / "dirty.py"
    dirty_path.write_text("def dirty() -> str:\n    return 'dirty'\n")

    def _forbid_read_bytes(self: Path) -> bytes:
        raise AssertionError(f"read_bytes should not be used for dirty hashing: {self}")

    monkeypatch.setattr(Path, "read_bytes", _forbid_read_bytes)

    digest = _dirty_snapshot_hash(
        repo,
        (
            GitStatusEntry(
                index_status=" ",
                worktree_status="?",
                path="dirty.py",
            ),
        ),
    )

    assert digest is not None
    assert len(digest) == 24


def test_resolve_worktree_context_excludes_gitignored_untracked_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("ignored.py\n")
    (repo / "ignored.py").write_text("IGNORED = True\n")
    (repo / "visible.py").write_text("VISIBLE = True\n")

    ctx = resolve_worktree_context(repo, base_ref="main", index_config_hash="cfg")

    assert "visible.py" in ctx.dirty.affected_paths
    assert "ignored.py" not in ctx.dirty.affected_paths


def test_dirty_git_rename_tombstones_old_path_and_affects_new_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "mv", "main.py", "renamed.py")

    ctx = resolve_worktree_context(repo, base_ref="main", index_config_hash="cfg")

    assert "renamed.py" in ctx.dirty.affected_paths
    assert "main.py" in ctx.dirty.tombstoned_paths
    assert "main.py" not in ctx.dirty.affected_paths
    assert "renamed.py" not in ctx.dirty.tombstoned_paths


def test_materialize_paths_from_worktree_rejects_symlinks(tmp_path: Path) -> None:
    from cocoindex_code.version_control.git import materialize_paths_from_worktree

    repo = _init_repo(tmp_path / "repo")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    (repo / "link.py").symlink_to(outside)
    source_dir = tmp_path / "layer-src"

    materialize_paths_from_worktree(repo, ("link.py",), source_dir)

    assert not (source_dir / "link.py").exists()


def test_materialize_commit_skips_git_symlinks(tmp_path: Path) -> None:
    from cocoindex_code.version_control.git import materialize_commit

    repo = _init_repo(tmp_path / "repo")
    (repo / "link.py").symlink_to("main.py")
    _git(repo, "add", "link.py")
    _git(repo, "commit", "-m", "add symlink")
    commit = _git(repo, "rev-parse", "HEAD")
    source_dir = tmp_path / "layer-src"

    materialize_commit(repo, commit, source_dir)

    assert (source_dir / "main.py").is_file()
    assert not (source_dir / "link.py").exists()


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
    _install_fake_layer_runtime(monkeypatch)
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
        return _fake_layered_project(
            repo=repo,
            base_ref=base_commit,
            state_dir=state_dir,
            store=store,
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


@pytest.mark.asyncio
async def test_layered_project_does_not_build_configured_base_when_ancestor_chain_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_layer_runtime(monkeypatch)
    monkeypatch.setenv("COCOINDEX_CODE_STATE_DIR", str(tmp_path / "state"))
    repo = _init_repo(tmp_path / "repo")
    base_commit = _git(repo, "rev-parse", "HEAD")
    (repo / "master.py").write_text("def master_only() -> str:\n    return 'master'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "master")
    master_head = _git(repo, "rev-parse", "HEAD")

    state_dir = daemon_state_dir()
    store = LayerStore(state_dir / "daemon.db")
    master_project = _fake_layered_project(
        repo=repo,
        base_ref=base_commit,
        state_dir=state_dir,
        store=store,
    )
    try:
        master_layers = await master_project.ensure_layer_results()
    finally:
        master_project.close()
    master_layer = next(layer for layer in master_layers if layer.layer.kind == LayerKind.BRANCH)

    _git(repo, "checkout", "-b", "feature")
    (repo / "feature.py").write_text("def feature_only() -> str:\n    return 'feature'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature")

    feature_project = _fake_layered_project(
        repo=repo,
        base_ref=master_head,
        state_dir=state_dir,
        store=store,
    )
    try:
        feature_layers = await feature_project.ensure_layer_results()
    finally:
        feature_project.close()

    feature_layer, reused_master_layer, _base_layer = feature_layers
    assert feature_layer.manifest.affected_paths == frozenset({"feature.py"})
    assert reused_master_layer.layer.id == master_layer.layer.id
    assert not any(
        layer.kind == LayerKind.BASE and layer.commit_hash == master_head
        for layer in store.list_layers(repo_id=master_layer.layer.repo_id)
    )


@pytest.mark.asyncio
async def test_layered_project_ignores_unusable_indexed_ancestor_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cocoindex_code.layered_project import build_index_config_hash
    from cocoindex_code.version_control import resolve_worktree

    _install_fake_layer_runtime(monkeypatch)
    monkeypatch.setenv("COCOINDEX_CODE_STATE_DIR", str(tmp_path / "state"))
    repo = _init_repo(tmp_path / "repo")
    base_commit = _git(repo, "rev-parse", "HEAD")
    (repo / "master.py").write_text("def master_only() -> str:\n    return 'master'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "master")
    master_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "feature")
    (repo / "feature.py").write_text("def feature_only() -> str:\n    return 'feature'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature")

    state_dir = daemon_state_dir()
    store = LayerStore(state_dir / "daemon.db")
    config_hash = build_index_config_hash(repo, indexing_params={}, query_params={})
    worktree = resolve_worktree(repo, base_ref=base_commit, index_config_hash=config_hash)
    invalid_layer = store.upsert_layer(
        layer_id="missing-target-db",
        repo_id=worktree.repository.id,
        kind=LayerKind.BRANCH,
        ref_name="master",
        commit=master_head,
        base_commit=base_commit,
        base_layer_id="missing-base",
        source_dir=state_dir / "invalid" / "src",
        db_dir=state_dir / "invalid" / "db",
        status="building",
        config_hash=config_hash,
    )
    store.replace_manifest(
        invalid_layer.id,
        affected_paths=["master.py"],
        tombstoned_paths=[],
        expires_at=None,
    )
    store.mark_layer_ready(invalid_layer.id)

    project = _fake_layered_project(
        repo=repo,
        base_ref=base_commit,
        state_dir=state_dir,
        store=store,
    )
    try:
        layers = await project.ensure_layer_results()
    finally:
        project.close()

    branch_layer = layers[0]
    assert branch_layer.layer.base_commit_hash == base_commit
    assert branch_layer.manifest.affected_paths == frozenset({"feature.py", "master.py"})


@pytest.mark.asyncio
async def test_layered_project_ignores_indexed_ancestor_with_broken_parent_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cocoindex_code.layered_project import build_index_config_hash
    from cocoindex_code.version_control import resolve_worktree

    _install_fake_layer_runtime(monkeypatch)
    monkeypatch.setenv("COCOINDEX_CODE_STATE_DIR", str(tmp_path / "state"))
    repo = _init_repo(tmp_path / "repo")
    base_commit = _git(repo, "rev-parse", "HEAD")
    (repo / "master.py").write_text("def master_only() -> str:\n    return 'master'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "master")
    master_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "feature")
    (repo / "feature.py").write_text("def feature_only() -> str:\n    return 'feature'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature")

    state_dir = daemon_state_dir()
    store = LayerStore(state_dir / "daemon.db")
    config_hash = build_index_config_hash(repo, indexing_params={}, query_params={})
    worktree = resolve_worktree(repo, base_ref=base_commit, index_config_hash=config_hash)
    broken_layer = _upsert_ready_layer(
        store=store,
        state_dir=state_dir,
        repo_id=worktree.repository.id,
        layer_id="broken-parent-chain",
        kind=LayerKind.BRANCH,
        ref_name="master",
        commit=master_head,
        base_commit=base_commit,
        base_layer_id="missing-base",
        config_hash=config_hash,
        affected_paths=["master.py"],
    )
    assert broken_layer.paths.target_sqlite.exists()

    project = _fake_layered_project(
        repo=repo,
        base_ref=base_commit,
        state_dir=state_dir,
        store=store,
    )
    try:
        layers = await project.ensure_layer_results()
    finally:
        project.close()

    branch_layer = layers[0]
    assert branch_layer.layer.base_commit_hash == base_commit
    assert branch_layer.manifest.affected_paths == frozenset({"feature.py", "master.py"})


@pytest.mark.asyncio
async def test_dirty_layer_identity_includes_selected_parent_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_layer_runtime(monkeypatch)
    monkeypatch.setenv("COCOINDEX_CODE_STATE_DIR", str(tmp_path / "state"))
    repo = _init_repo(tmp_path / "repo")
    base_commit = _git(repo, "rev-parse", "HEAD")
    (repo / "master.py").write_text("def master_only() -> str:\n    return 'master'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "master")
    master_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "feature")
    (repo / "feature.py").write_text("def feature_only() -> str:\n    return 'feature'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature")
    (repo / "dirty.py").write_text("def dirty() -> str:\n    return 'dirty'\n")

    state_dir = daemon_state_dir()
    store = LayerStore(state_dir / "daemon.db")
    feature_project = _fake_layered_project(
        repo=repo,
        base_ref=base_commit,
        state_dir=state_dir,
        store=store,
    )
    try:
        original_layers = await feature_project.ensure_layer_results()
    finally:
        feature_project.close()
    old_dirty, old_branch, base_layer = original_layers
    assert old_dirty.layer.kind == LayerKind.DIRTY
    old_branch.layer.paths.target_sqlite.unlink()

    master_layer = _upsert_ready_layer(
        store=store,
        state_dir=state_dir,
        repo_id=base_layer.layer.repo_id,
        layer_id="manual-master-layer",
        kind=LayerKind.BRANCH,
        ref_name="master",
        commit=master_head,
        base_commit=base_commit,
        base_layer_id=base_layer.layer.id,
        config_hash=base_layer.layer.config_hash or "",
        affected_paths=["master.py"],
    )

    refreshed_project = _fake_layered_project(
        repo=repo,
        base_ref=base_commit,
        state_dir=state_dir,
        store=store,
    )
    try:
        refreshed_layers = await refreshed_project.ensure_layer_results()
    finally:
        refreshed_project.close()

    new_dirty, new_branch, reused_master, _reused_base = refreshed_layers
    assert new_dirty.layer.kind == LayerKind.DIRTY
    assert new_dirty.layer.id != old_dirty.layer.id
    assert new_dirty.layer.base_layer_id == new_branch.layer.id
    assert new_branch.layer.base_layer_id == master_layer.id
    assert reused_master.layer.id == master_layer.id


@pytest.mark.asyncio
async def test_layered_project_prefers_smaller_layer_at_same_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cocoindex_code.layered_project import build_index_config_hash
    from cocoindex_code.version_control import resolve_worktree

    _install_fake_layer_runtime(monkeypatch)
    monkeypatch.setenv("COCOINDEX_CODE_STATE_DIR", str(tmp_path / "state"))
    repo = _init_repo(tmp_path / "repo")
    base_commit = _git(repo, "rev-parse", "HEAD")
    (repo / "main.py").write_text("def changed() -> str:\n    return 'changed'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "head")
    head_commit = _git(repo, "rev-parse", "HEAD")

    state_dir = daemon_state_dir()
    store = LayerStore(state_dir / "daemon.db")
    config_hash = build_index_config_hash(repo, indexing_params={}, query_params={})
    worktree = resolve_worktree(repo, base_ref=base_commit, index_config_hash=config_hash)
    base_layer = _upsert_ready_layer(
        store=store,
        state_dir=state_dir,
        repo_id=worktree.repository.id,
        layer_id="manual-base-layer",
        kind=LayerKind.BASE,
        ref_name="base",
        commit=base_commit,
        base_commit=None,
        base_layer_id=None,
        config_hash=config_hash,
        affected_paths=[],
    )
    _upsert_ready_layer(
        store=store,
        state_dir=state_dir,
        repo_id=worktree.repository.id,
        layer_id="large-layer-at-head",
        kind=LayerKind.BRANCH,
        ref_name="feature",
        commit=head_commit,
        base_commit=base_commit,
        base_layer_id=base_layer.id,
        config_hash=config_hash,
        affected_paths=[f"file_{i}.py" for i in range(20)],
    )
    small_layer = _upsert_ready_layer(
        store=store,
        state_dir=state_dir,
        repo_id=worktree.repository.id,
        layer_id="small-layer-at-head",
        kind=LayerKind.BRANCH,
        ref_name="feature",
        commit=head_commit,
        base_commit=base_commit,
        base_layer_id=base_layer.id,
        config_hash=config_hash,
        affected_paths=["main.py"],
    )

    project = _fake_layered_project(
        repo=repo,
        base_ref=base_commit,
        state_dir=state_dir,
        store=store,
    )
    try:
        layers = await project.ensure_layer_results()
    finally:
        project.close()

    assert layers[0].layer.id == small_layer.id
    assert layers[0].built is False


@pytest.mark.asyncio
async def test_overlay_prune_closes_cached_layer_projects_before_deleting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cocoindex_code.daemon import ProjectRegistry, _dispatch
    from cocoindex_code.protocol import OverlayPruneRequest, OverlayPruneResponse

    monkeypatch.setenv("COCOINDEX_CODE_STATE_DIR", str(tmp_path / "state"))
    registry = ProjectRegistry(embedder=object())
    layer_root = registry.state_dir / "repos" / "repo" / "layers" / "expired"
    layer = registry.layer_store.upsert_layer(
        layer_id="expired",
        repo_id="repo",
        kind=LayerKind.DIRTY,
        ref_name="feature",
        commit="abc",
        base_commit="base",
        base_layer_id="base-layer",
        source_dir=layer_root / "src",
        db_dir=layer_root / "db",
        status="ready",
    )
    layer.paths.source.mkdir(parents=True)
    layer.paths.db_dir.mkdir(parents=True)
    registry.layer_store.replace_manifest(
        "expired",
        affected_paths=["dirty.py"],
        tombstoned_paths=[],
        expires_at=0.0,
    )

    class _CachedProject:
        closed = False

        def close(self) -> None:
            self.closed = True

    cached_project = _CachedProject()
    registry._layer_project_cache["expired"] = cached_project  # noqa: SLF001

    resp = await _dispatch(
        OverlayPruneRequest(),
        registry,
        start_time=0.0,
        on_shutdown=lambda: None,
        settings_env_names=[],
    )

    assert isinstance(resp, OverlayPruneResponse)
    assert resp.pruned_layer_ids == ["expired"]
    assert resp.failures == []
    assert cached_project.closed is True
    assert "expired" not in registry._layer_project_cache  # noqa: SLF001
    assert not layer_root.exists()


@pytest.mark.asyncio
async def test_layer_rebuild_closes_cached_project_before_deleting_layer_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cocoindex_code.layered_project import build_index_config_hash
    from cocoindex_code.layers.layer_paths import LayerPaths
    from cocoindex_code.layers.layer_stack import LayerStack
    from cocoindex_code.version_control import resolve_worktree

    _install_fake_layer_runtime(monkeypatch)
    repo = _init_repo(tmp_path / "repo")
    state_dir = tmp_path / "state"
    store = LayerStore(state_dir / "daemon.db")
    config_hash = build_index_config_hash(repo, indexing_params={}, query_params={})
    worktree = resolve_worktree(repo, base_ref="main", index_config_hash=config_hash)
    layer_id = "stale-layer"
    paths = LayerPaths.for_layer(state_dir, worktree.repository.id, layer_id)
    store.upsert_layer(
        layer_id=layer_id,
        repo_id=worktree.repository.id,
        kind=LayerKind.DIRTY,
        ref_name="main",
        commit=worktree.branch.head_commit,
        base_commit=worktree.branch.base_commit,
        base_layer_id="base-layer",
        source_dir=paths.source,
        db_dir=paths.db_dir,
        status="ready",
        config_hash=config_hash,
    )
    paths.root.mkdir(parents=True)
    stale_file = paths.root / "stale.txt"
    stale_file.write_text("stale\n")

    class _CachedProject:
        closed = False

        def close(self) -> None:
            self.closed = True

    cached_project = _CachedProject()
    project_cache: dict[str, Any] = {layer_id: cached_project}
    stack = LayerStack(
        project_root=repo,
        state_dir=state_dir,
        store=store,
        embedder=object(),
        indexing_params={},
        query_params={},
        chunker_registry={},
        project_cache=project_cache,
    )

    await stack._ensure_layer(  # noqa: SLF001
        worktree=worktree,
        layer_id=layer_id,
        kind=LayerKind.DIRTY,
        ref_name="main",
        commit=worktree.branch.head_commit,
        base_commit=worktree.branch.base_commit,
        merge_base=worktree.branch.merge_base,
        base_layer_id="base-layer",
        worktree_id=worktree.id,
        config_hash=config_hash,
        expires_at=0.0,
        materialize=lambda source_dir: (source_dir / "fresh.txt").write_text("fresh\n"),
        affected_paths=("fresh.txt",),
        tombstoned_paths=(),
        on_progress=None,
    )

    assert cached_project.closed is True
    assert layer_id not in project_cache
    assert not stale_file.exists()
    assert (paths.source / "fresh.txt").exists()


def test_layered_project_close_preserves_registry_owned_project_cache(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")

    class _CachedProject:
        closed = False

        def close(self) -> None:
            self.closed = True

    cached_project = _CachedProject()
    project_cache: dict[str, Any] = {"layer": cached_project}
    project = LayeredProject(
        project_root=repo,
        cwd=repo,
        base_ref="main",
        state_dir=tmp_path / "state",
        store=LayerStore(tmp_path / "state" / "daemon.db"),
        embedder=object(),
        indexing_params={},
        query_params={},
        chunker_registry={},
        project_cache=project_cache,
        owns_project_cache=False,
    )

    project.close()

    assert cached_project.closed is False
    assert project_cache == {"layer": cached_project}


def test_project_registry_remove_project_closes_all_matching_project_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cocoindex_code.daemon import ProjectRegistry

    monkeypatch.setenv("COCOINDEX_CODE_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "repo"
    root.mkdir()

    class _CachedProject:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    first = _CachedProject()
    second = _CachedProject()
    registry = ProjectRegistry(embedder=object())
    registry._projects[f"{root.resolve()}\0{root.resolve()}\0"] = first  # noqa: SLF001
    registry._projects[f"{root.resolve()}\0{root.resolve() / 'subdir'}\0main"] = second  # noqa: SLF001

    assert registry.remove_project(str(root)) is True

    assert first.closed is True
    assert second.closed is True
    assert registry._projects == {}  # noqa: SLF001
