"""Unit tests for shared CLI helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from cocoindex_code import cli
from cocoindex_code.cli import (
    add_to_gitignore,
    remove_from_gitignore,
    require_project_root,
    resolve_default_path,
)
from cocoindex_code.protocol import IndexingProgress, SearchResponse
from cocoindex_code.sidecar import SidecarIndexReport, SidecarLayerSummary


def _sample_sidecar_report(project_root: Path) -> SidecarIndexReport:
    return SidecarIndexReport(
        project_root=project_root,
        cwd=project_root,
        repo_id="repo-123",
        branch="feature",
        base_ref="origin/main",
        base_commit="abcdef1234567890",
        head_commit="fedcba9876543210",
        effective_file_count=123,
        effective_chunk_count=620,
        layers=(
            SidecarLayerSummary(
                layer_id="branch-layer",
                kind="branch",
                ref_name="feature",
                commit="fedcba9876543210",
                previous_commit="abcdef1234567890",
                merge_base="abcdef1234567890",
                base_layer_id="base-layer",
                status="ready",
                built=True,
                affected_count=12,
                tombstoned_count=1,
                indexed_file_count=8,
                indexed_chunk_count=34,
                progress=IndexingProgress(
                    num_execution_starts=8,
                    num_unchanged=2,
                    num_adds=5,
                    num_deletes=1,
                    num_reprocesses=0,
                    num_errors=0,
                ),
            ),
            SidecarLayerSummary(
                layer_id="base-layer",
                kind="base",
                ref_name="origin/main",
                commit="abcdef1234567890",
                previous_commit=None,
                merge_base=None,
                base_layer_id=None,
                status="ready",
                built=False,
                affected_count=0,
                tombstoned_count=0,
                indexed_file_count=120,
                indexed_chunk_count=610,
            ),
        ),
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


def test_require_project_root_success_for_git_repo_without_local_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    subdir = project / "src"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
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


# ---------------------------------------------------------------------------
# COCOINDEX_CODE_HOST_CWD callback
# ---------------------------------------------------------------------------


def test_apply_host_cwd_chdirs_to_mapped_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When COCOINDEX_CODE_HOST_CWD is set and matches the mapping, chdir to container form."""
    from cocoindex_code.cli import _apply_host_cwd
    from cocoindex_code.settings import _reset_host_path_mapping_cache

    container = tmp_path / "workspace"
    host = tmp_path / "host-home"
    (container / "proj" / "src").mkdir(parents=True)
    host.mkdir()

    _reset_host_path_mapping_cache()
    monkeypatch.setenv("COCOINDEX_CODE_HOST_PATH_MAPPING", f"{container}={host}")
    monkeypatch.setenv("COCOINDEX_CODE_HOST_CWD", str(host / "proj" / "src"))

    _apply_host_cwd()

    # chdir resolves symlinks; compare resolved forms.
    assert Path.cwd().resolve() == (container / "proj" / "src").resolve()
    assert capsys.readouterr().err == ""

    _reset_host_path_mapping_cache()


def test_apply_host_cwd_warns_on_invalid_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An invalid COCOINDEX_CODE_HOST_CWD emits a warning but doesn't abort."""
    from cocoindex_code.cli import _apply_host_cwd

    original_cwd = Path.cwd()
    monkeypatch.setenv("COCOINDEX_CODE_HOST_CWD", "/nonexistent/path/xyz")
    monkeypatch.delenv("COCOINDEX_CODE_HOST_PATH_MAPPING", raising=False)

    _apply_host_cwd()

    captured = capsys.readouterr()
    assert "COCOINDEX_CODE_HOST_CWD" in captured.err
    assert "/nonexistent/path/xyz" in captured.err
    # cwd should be unchanged since chdir failed.
    assert Path.cwd() == original_cwd


def test_apply_host_cwd_noop_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With COCOINDEX_CODE_HOST_CWD unset, the callback is a silent no-op."""
    from cocoindex_code.cli import _apply_host_cwd

    original_cwd = Path.cwd()
    monkeypatch.delenv("COCOINDEX_CODE_HOST_CWD", raising=False)

    _apply_host_cwd()

    assert Path.cwd() == original_cwd
    assert capsys.readouterr().err == ""


def test_search_with_wait_spinner_resolves_sidecar_layers_before_daemon_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cocoindex_code.client as client
    import cocoindex_code.sidecar as sidecar

    async def fake_ensure_sidecar_layer_ids(**kwargs: object) -> list[str]:
        captured["ensure_kwargs"] = kwargs
        return ["base", "dirty"]

    def fake_search(**kwargs: object) -> SearchResponse:
        captured["search_kwargs"] = kwargs
        return SearchResponse(success=True)

    captured: dict[str, object] = {}
    monkeypatch.setattr(sidecar, "sidecar_enabled", lambda: True)
    monkeypatch.setattr(sidecar, "ensure_sidecar_layer_ids", fake_ensure_sidecar_layer_ids)
    monkeypatch.setattr(client, "search", fake_search)

    resp = cli._search_with_wait_spinner(
        project_root=str(tmp_path / "repo"),
        cwd=str(tmp_path / "repo" / "src"),
        base_ref="main",
        query="hello",
        languages=["python"],
        paths=["src/*"],
        limit=3,
        offset=1,
    )

    assert resp.success is True
    ensure_kwargs = captured["ensure_kwargs"]
    assert isinstance(ensure_kwargs, dict)
    assert ensure_kwargs["project_root"] == tmp_path / "repo"
    assert ensure_kwargs["cwd"] == tmp_path / "repo" / "src"
    assert ensure_kwargs["base_ref"] == "main"

    search_kwargs = captured["search_kwargs"]
    assert isinstance(search_kwargs, dict)
    assert search_kwargs["project_root"] == str(tmp_path / "repo")
    assert search_kwargs["cwd"] == str(tmp_path / "repo" / "src")
    assert search_kwargs["base_ref"] == "main"
    assert search_kwargs["layer_ids"] == ["base", "dirty"]
    assert search_kwargs["languages"] == ["python"]
    assert search_kwargs["paths"] == ["src/*"]
    assert search_kwargs["limit"] == 3
    assert search_kwargs["offset"] == 1


def test_search_with_wait_spinner_omits_layer_ids_outside_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cocoindex_code.client as client
    import cocoindex_code.sidecar as sidecar

    def fail_ensure(**_kwargs: object) -> list[str]:
        raise AssertionError("sidecar layer resolution should not run")

    def fake_search(**kwargs: object) -> SearchResponse:
        captured["search_kwargs"] = kwargs
        return SearchResponse(success=True)

    captured: dict[str, object] = {}
    monkeypatch.setattr(sidecar, "sidecar_enabled", lambda: False)
    monkeypatch.setattr(sidecar, "ensure_sidecar_layer_ids", fail_ensure)
    monkeypatch.setattr(client, "search", fake_search)

    resp = cli._search_with_wait_spinner(
        project_root=str(tmp_path / "repo"),
        query="hello",
    )

    assert resp.success is True
    search_kwargs = captured["search_kwargs"]
    assert isinstance(search_kwargs, dict)
    assert search_kwargs["layer_ids"] is None


def test_run_index_with_progress_uses_sidecar_indexer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import cocoindex_code.client as client
    import cocoindex_code.sidecar as sidecar

    async def fake_run_sidecar_index(**kwargs: object) -> SidecarIndexReport:
        captured["index_kwargs"] = kwargs
        return _sample_sidecar_report(tmp_path / "repo")

    def fail_client_index(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("daemon index should not run in sidecar mode")

    captured: dict[str, object] = {}
    monkeypatch.setattr(sidecar, "sidecar_enabled", lambda: True)
    monkeypatch.setattr(sidecar, "run_sidecar_index", fake_run_sidecar_index)
    monkeypatch.setattr(client, "index", fail_client_index)

    report = cli._run_index_with_progress(
        str(tmp_path / "repo"),
        cwd=str(tmp_path / "repo" / "src"),
        base_ref="main",
    )

    assert report is not None
    assert report.repo_id == "repo-123"
    kwargs = captured["index_kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["project_root"] == tmp_path / "repo"
    assert kwargs["cwd"] == tmp_path / "repo" / "src"
    assert kwargs["base_ref"] == "main"
    assert callable(kwargs["on_progress"])
    assert "Indexing failed" not in capsys.readouterr().err


def test_index_command_skips_daemon_project_status_in_sidecar_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import cocoindex_code.client as client
    import cocoindex_code.sidecar as sidecar

    project_root = tmp_path / "repo"

    def fail_project_status(_project_root: str) -> object:
        raise AssertionError("sidecar index must not ask daemon for non-mounted project status")

    monkeypatch.setattr(cli, "require_project_root_from", lambda _cwd: project_root)
    monkeypatch.setattr(
        cli,
        "_run_index_with_progress",
        lambda *_args, **_kwargs: _sample_sidecar_report(project_root),
    )
    monkeypatch.setattr(sidecar, "sidecar_enabled", lambda: True)
    monkeypatch.setattr(client, "project_status", fail_project_status)

    cli.index(cwd=None, base_ref=None)

    out = capsys.readouterr().out
    assert f"Project: {project_root}" in out
    assert "Layered index updated:" in out
    assert "Mode: Git layered index" in out
    assert "Repo ID: repo-123" in out
    assert "Source:" in out
    assert "Total searchable content: 123 files, 620 chunks" in out
    assert "Search layers, top to bottom:" in out
    assert "branch built now" in out
    assert "Covers: feature changes from abcdef123456 to fedcba987654" in out
    assert "Source changes: 12 changed paths, 1 deleted" in out
    assert "Searchable in this layer: 8 files, 34 chunks" in out
    assert "8 files listed; 5 added, 2 unchanged, 0 reprocessed, 1 deleted, 0 errors" in out
    assert "base   reused" in out
    assert "Covers: full snapshot of origin/main at abcdef123456" in out
    assert "Searchable in this layer: 120 files, 610 chunks" in out
    assert "Build work: skipped, reused existing ready layer" in out
    assert "Index stats:" not in out


# ---------------------------------------------------------------------------
# ccc init — auto-populate indexing_params / query_params from curated table
# ---------------------------------------------------------------------------


def test_init_auto_populates_known_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """For a known model, `ccc init` writes real indexing/query params into the
    file and prints an 'Applied recommended defaults' message.
    """
    from cocoindex_code.settings import EmbeddingSettings, load_user_settings

    user_dir = tmp_path / ".cocoindex_code"
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(user_dir))

    monkeypatch.setattr(
        cli,
        "_resolve_embedding_choice",
        lambda **_kw: EmbeddingSettings(provider="litellm", model="cohere/embed-english-v3.0"),
    )
    monkeypatch.setattr(cli, "_run_init_model_check", lambda path: None)

    cli._setup_user_settings_interactive(litellm_model_flag=None)

    loaded = load_user_settings()
    assert loaded.embedding.provider == "litellm"
    assert loaded.embedding.model == "cohere/embed-english-v3.0"
    assert loaded.embedding.indexing_params == {"input_type": "search_document"}
    assert loaded.embedding.query_params == {"input_type": "search_query"}

    out = capsys.readouterr().out
    assert "Applied recommended defaults" in out


def test_init_writes_comment_template_for_unknown_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For a model outside the curated table, `ccc init` writes a commented-out
    template block under ``embedding:`` instead of real keys.
    """
    from cocoindex_code.settings import (
        EmbeddingSettings,
        load_user_settings,
        user_settings_path,
    )

    user_dir = tmp_path / ".cocoindex_code"
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(user_dir))

    monkeypatch.setattr(
        cli,
        "_resolve_embedding_choice",
        lambda **_kw: EmbeddingSettings(provider="litellm", model="someprovider/unknown-model"),
    )
    monkeypatch.setattr(cli, "_run_init_model_check", lambda path: None)

    cli._setup_user_settings_interactive(litellm_model_flag=None)

    content = user_settings_path().read_text()
    # Commented template present, no populated keys
    assert "# indexing_params: {}" in content
    assert "# query_params: {}" in content
    loaded = load_user_settings()
    assert loaded.embedding.indexing_params is None
    assert loaded.embedding.query_params is None
