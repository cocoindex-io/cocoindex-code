from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cocoindex_code import sidecar
from cocoindex_code.protocol import IndexingProgress
from cocoindex_code.settings import EmbeddingSettings, ProjectSettings, UserSettings


class _FakeLayeredProject:
    created: list[_FakeLayeredProject] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.closed = False
        _FakeLayeredProject.created.append(self)

    async def ensure_layer_ids(self, on_progress: Any = None) -> list[str]:
        if on_progress is not None:
            on_progress(
                IndexingProgress(
                    num_execution_starts=1,
                    num_unchanged=0,
                    num_adds=1,
                    num_deletes=0,
                    num_reprocesses=0,
                    num_errors=0,
                )
            )
        return ["base", "branch", "dirty"]

    def close(self) -> None:
        self.closed = True


class _FailingLayeredProject(_FakeLayeredProject):
    async def ensure_layer_ids(self, on_progress: Any = None) -> list[str]:
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_ensure_sidecar_layer_ids_sets_env_and_closes_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeLayeredProject.created.clear()
    monkeypatch.setenv("COCOINDEX_CODE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        sidecar,
        "load_user_settings",
        lambda: UserSettings(
            embedding=EmbeddingSettings(provider="litellm", model="test-model"),
            envs={"TEST_SIDECAR_ENV": "value"},
        ),
    )
    monkeypatch.setattr(
        sidecar,
        "resolve_embedder_params",
        lambda _embedding: SimpleNamespace(indexing={"input": "doc"}, query={"input": "query"}),
    )
    monkeypatch.setattr(sidecar, "load_project_settings", lambda _root: ProjectSettings())
    monkeypatch.setattr(sidecar, "create_embedder", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(sidecar, "_resolve_chunker_registry", lambda _chunkers: {".x": object()})
    monkeypatch.setattr(sidecar, "LayeredProject", _FakeLayeredProject)

    progress: list[IndexingProgress] = []
    layer_ids = await sidecar.ensure_sidecar_layer_ids(
        project_root=tmp_path / "repo",
        cwd=tmp_path / "repo" / "src",
        base_ref="main",
        on_progress=progress.append,
    )

    assert layer_ids == ["base", "branch", "dirty"]
    assert progress and progress[0].num_adds == 1
    assert sidecar.os.environ["TEST_SIDECAR_ENV"] == "value"
    [project] = _FakeLayeredProject.created
    assert project.closed is True
    assert project.kwargs["project_root"] == tmp_path / "repo"
    assert project.kwargs["cwd"] == tmp_path / "repo" / "src"
    assert project.kwargs["base_ref"] == "main"
    assert project.kwargs["indexing_params"] == {"input": "doc"}
    assert project.kwargs["query_params"] == {"input": "query"}
    assert ".x" in project.kwargs["chunker_registry"]
    assert project.kwargs["project_cache"] == {}


@pytest.mark.asyncio
async def test_ensure_sidecar_layer_ids_closes_project_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FailingLayeredProject.created.clear()
    monkeypatch.setenv("COCOINDEX_CODE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        sidecar,
        "load_user_settings",
        lambda: UserSettings(embedding=EmbeddingSettings(provider="litellm", model="test-model")),
    )
    monkeypatch.setattr(
        sidecar,
        "resolve_embedder_params",
        lambda _embedding: SimpleNamespace(indexing={}, query={}),
    )
    monkeypatch.setattr(sidecar, "load_project_settings", lambda _root: ProjectSettings())
    monkeypatch.setattr(sidecar, "create_embedder", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(sidecar, "_resolve_chunker_registry", lambda _chunkers: {})
    monkeypatch.setattr(sidecar, "LayeredProject", _FailingLayeredProject)

    with pytest.raises(RuntimeError, match="boom"):
        await sidecar.ensure_sidecar_layer_ids(
            project_root=tmp_path / "repo",
            cwd=tmp_path / "repo",
            base_ref=None,
        )

    [project] = _FailingLayeredProject.created
    assert project.closed is True


def test_sidecar_enabled_requires_exact_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COCOINDEX_CODE_SIDECAR", raising=False)
    assert sidecar.sidecar_enabled() is False

    monkeypatch.setenv("COCOINDEX_CODE_SIDECAR", "true")
    assert sidecar.sidecar_enabled() is False

    monkeypatch.setenv("COCOINDEX_CODE_SIDECAR", "1")
    assert sidecar.sidecar_enabled() is True
