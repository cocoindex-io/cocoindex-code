from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ._daemon_paths import daemon_state_dir
from .daemon import _resolve_chunker_registry
from .embedder_params import resolve_embedder_params
from .layer_store import LayerStore
from .layered_project import LayeredProject
from .layers import LayerBuildResult
from .protocol import IndexingProgress
from .settings import load_project_settings, load_user_settings
from .shared import create_embedder


def sidecar_enabled() -> bool:
    return os.environ.get("COCOINDEX_CODE_SIDECAR") == "1"


@dataclass(frozen=True)
class SidecarLayerSummary:
    layer_id: str
    kind: str
    ref_name: str | None
    commit: str | None
    previous_commit: str | None
    merge_base: str | None
    base_layer_id: str | None
    status: str
    built: bool
    affected_count: int
    tombstoned_count: int
    indexed_file_count: int | None = None
    indexed_chunk_count: int | None = None
    progress: IndexingProgress | None = None


@dataclass(frozen=True)
class SidecarIndexReport:
    project_root: Path
    cwd: Path
    repo_id: str | None
    branch: str | None
    base_ref: str | None
    base_commit: str | None
    head_commit: str | None
    layers: tuple[SidecarLayerSummary, ...]
    effective_file_count: int | None = None
    effective_chunk_count: int | None = None


def _summarize_layers(
    *, project_root: Path, cwd: Path, layers: list[LayerBuildResult]
) -> SidecarIndexReport:
    summaries = tuple(_summarize_layer(layer) for layer in layers)
    effective_file_count, effective_chunk_count = _effective_index_counts(layers)
    base = next((layer for layer in summaries if layer.kind == "base"), None)
    top = summaries[0] if summaries else None
    branch = next((layer.ref_name for layer in summaries if layer.kind != "base"), None)
    return SidecarIndexReport(
        project_root=project_root,
        cwd=cwd,
        repo_id=layers[0].layer.repo_id if layers else None,
        branch=branch or (top.ref_name if top is not None else None),
        base_ref=base.ref_name if base is not None else None,
        base_commit=base.commit if base is not None else None,
        head_commit=top.commit if top is not None else None,
        layers=summaries,
        effective_file_count=effective_file_count,
        effective_chunk_count=effective_chunk_count,
    )


def _summarize_layer(layer: LayerBuildResult) -> SidecarLayerSummary:
    status = layer.runtime.project.get_status()
    return SidecarLayerSummary(
        layer_id=layer.layer.id,
        kind=layer.layer.kind.value,
        ref_name=layer.layer.ref_name,
        commit=layer.layer.commit_hash,
        previous_commit=layer.layer.base_commit_hash,
        merge_base=layer.layer.merge_base_hash,
        base_layer_id=layer.layer.base_layer_id,
        status=layer.layer.status,
        built=layer.built,
        affected_count=len(layer.manifest.affected_paths),
        tombstoned_count=len(layer.manifest.tombstoned_paths),
        indexed_file_count=status.total_files if status.index_exists else None,
        indexed_chunk_count=status.total_chunks if status.index_exists else None,
        progress=layer.progress,
    )


def _effective_index_counts(layers: list[LayerBuildResult]) -> tuple[int | None, int | None]:
    if not layers:
        return None, None

    lower_layer_shadowed_paths: set[str] = set()
    file_count = 0
    chunk_count = 0
    for layer in layers:
        file_chunks = layer.runtime.project.get_indexed_file_chunk_counts()
        for file_path, chunks in file_chunks.items():
            if file_path in lower_layer_shadowed_paths:
                continue
            file_count += 1
            chunk_count += chunks
        lower_layer_shadowed_paths.update(layer.manifest.affected_paths)
        lower_layer_shadowed_paths.update(layer.manifest.tombstoned_paths)
    return file_count, chunk_count


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
) -> SidecarIndexReport:
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
        layers = await project.ensure_layer_results(on_progress=on_progress)
        return _summarize_layers(project_root=project_root, cwd=cwd, layers=layers)
    finally:
        project.close()
