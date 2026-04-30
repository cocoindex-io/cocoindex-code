from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from cocoindex_code.daemon import ProjectRegistry
from cocoindex_code.shared import Embedder


class _DummyEmbedder:
    pass


def test_project_registry_caches_chunker_dir_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_candidate_chunker_roots(project_root: Path) -> list[Path]:
        calls.append(str(project_root))
        return [project_root / "scripts" / "cocoindex"]

    registry = ProjectRegistry(cast(Embedder, _DummyEmbedder()))
    monkeypatch.setattr(
        "cocoindex_code.daemon._candidate_shared_chunker_roots",
        fake_candidate_chunker_roots,
    )

    root = "/tmp/workspace"
    first = registry._shared_chunker_roots_for_project(root)
    second = registry._shared_chunker_roots_for_project(root)

    assert first == second
    assert calls == [root]


def test_project_registry_remove_project_clears_chunker_dir_cache() -> None:
    registry = ProjectRegistry(cast(Embedder, _DummyEmbedder()))
    registry._chunker_roots_by_project["/tmp/workspace"] = (
        Path("/tmp/workspace/scripts/cocoindex"),
    )

    removed = registry.remove_project("/tmp/workspace")

    assert removed is False
    assert "/tmp/workspace" not in registry._chunker_roots_by_project


@pytest.mark.asyncio
async def test_project_registry_can_load_query_only_without_chunkers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeProject:
        def __init__(self) -> None:
            self.chunkers_ready = False

        def set_chunker_registry(self, chunker_registry: dict[str, Any]) -> None:
            self.chunkers_ready = True

    fake_project = _FakeProject()
    registry = ProjectRegistry(cast(Embedder, _DummyEmbedder()))

    async def fake_create(*args: Any, **kwargs: Any) -> _FakeProject:
        return fake_project

    def fail_resolve(_project_root: str) -> dict[str, Any]:
        raise AssertionError("chunkers should not be resolved for query-only project loads")

    monkeypatch.setattr("cocoindex_code.daemon.Project.create", fake_create)
    monkeypatch.setattr(registry, "_resolve_project_chunkers", fail_resolve)

    loaded = await registry.get_project("/tmp/workspace", require_chunkers=False)
    assert loaded is fake_project
    assert fake_project.chunkers_ready is False


@pytest.mark.asyncio
async def test_project_registry_upgrades_project_when_chunkers_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeProject:
        def __init__(self) -> None:
            self.chunkers_ready = False
            self.loaded_registry: dict[str, Any] | None = None

        def set_chunker_registry(self, chunker_registry: dict[str, Any]) -> None:
            self.loaded_registry = dict(chunker_registry)
            self.chunkers_ready = True

    fake_project = _FakeProject()
    registry = ProjectRegistry(cast(Embedder, _DummyEmbedder()))

    async def fake_create(*args: Any, **kwargs: Any) -> _FakeProject:
        return fake_project

    monkeypatch.setattr("cocoindex_code.daemon.Project.create", fake_create)
    monkeypatch.setattr(registry, "_resolve_project_chunkers", lambda _root: {".py": object()})

    loaded = await registry.get_project("/tmp/workspace", require_chunkers=True)
    assert loaded is fake_project
    assert fake_project.chunkers_ready is True
    assert fake_project.loaded_registry is not None
    assert set(fake_project.loaded_registry) == {".py"}
