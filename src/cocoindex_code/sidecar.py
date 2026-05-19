from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from ._daemon_paths import daemon_state_dir
from .daemon import _resolve_chunker_registry
from .embedder_params import resolve_embedder_params
from .layer_store import LayerStore
from .layered_project import LayeredProject
from .protocol import IndexingProgress
from .settings import load_project_settings, load_user_settings
from .shared import create_embedder


def sidecar_enabled() -> bool:
    return os.environ.get("COCOINDEX_CODE_SIDECAR") == "1"


async def ensure_sidecar_layer_ids(
    *,
    project_root: Path,
    cwd: Path,
    base_ref: str | None,
    on_progress: Callable[[IndexingProgress], None] | None = None,
) -> list[str]:
    user_settings = load_user_settings()
    for key, value in user_settings.envs.items():
        os.environ[key] = value
    params = resolve_embedder_params(user_settings.embedding)
    project_settings = load_project_settings(project_root)
    state_dir = daemon_state_dir()
    project = LayeredProject(
        project_root=project_root,
        cwd=cwd,
        base_ref=base_ref,
        state_dir=state_dir,
        store=LayerStore(state_dir / "daemon.db"),
        embedder=create_embedder(user_settings.embedding, indexing_params=params.indexing),
        indexing_params=params.indexing,
        query_params=params.query,
        chunker_registry=_resolve_chunker_registry(project_settings.chunkers),
        project_cache={},
    )
    try:
        return await project.ensure_layer_ids(on_progress=on_progress)
    finally:
        project.close()


async def run_sidecar_index(
    *,
    project_root: Path,
    cwd: Path,
    base_ref: str | None,
    on_progress: Callable[[IndexingProgress], None] | None = None,
) -> None:
    await ensure_sidecar_layer_ids(
        project_root=project_root,
        cwd=cwd,
        base_ref=base_ref,
        on_progress=on_progress,
    )
