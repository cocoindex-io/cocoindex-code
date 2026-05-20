from __future__ import annotations

import hashlib
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cocoindex_code.project import Project
from cocoindex_code.protocol import IndexingProgress, SearchResult
from cocoindex_code.shared import Embedder
from cocoindex_code.version_control import Worktree
from cocoindex_code.version_control.git import (
    branch_changes,
    materialize_commit,
    materialize_paths_from_commit,
    materialize_paths_from_worktree,
)

from .layer import Layer
from .layer_kind import LayerKind
from .layer_manifest import LayerManifest
from .layer_paths import LayerPaths
from .layer_runtime import LayerRuntime
from .layer_store import LayerStore

_BRANCH_TTL_SECONDS = 14 * 24 * 60 * 60
_DIRTY_TTL_SECONDS = 24 * 60 * 60


def _sha_short(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class LayerBuildResult:
    layer: Layer
    manifest: LayerManifest
    runtime: LayerRuntime
    built: bool = False

    @property
    def record(self) -> Layer:
        return self.layer

    @property
    def project(self) -> Project:
        return self.runtime.project


class LayerStack:
    """Builds and queries ordered Git overlay layers."""

    def __init__(
        self,
        *,
        project_root: Path,
        state_dir: Path,
        store: LayerStore,
        embedder: Embedder,
        indexing_params: dict[str, Any],
        query_params: dict[str, Any],
        chunker_registry: dict[str, Any],
        project_cache: dict[str, Project],
    ) -> None:
        self.project_root = project_root
        self.state_dir = state_dir
        self.store = store
        self.embedder = embedder
        self.indexing_params = indexing_params
        self.query_params = query_params
        self.chunker_registry = chunker_registry
        self.project_cache = project_cache

    async def ensure(
        self,
        *,
        worktree: Worktree,
        config_hash: str,
        on_progress: Callable[[IndexingProgress], None] | None,
    ) -> list[LayerBuildResult]:
        self.store.upsert_repository(
            repo_id=worktree.repository.id,
            repo_name=worktree.repository.repo_name,
            remote_url=worktree.repository.remote_url,
            normalized_remote_url=worktree.repository.normalized_remote_url,
            repo_relative_root=worktree.repository.repo_relative_root,
            last_seen_root=worktree.repository.last_seen_root,
        )
        self.store.upsert_worktree(
            worktree_id=worktree.id,
            repo_id=worktree.repository.id,
            worktree_name=worktree.name,
            branch_name=worktree.branch.name,
            last_seen_path=worktree.path,
        )
        base = await self._ensure_base(worktree, config_hash, on_progress)
        layers: list[LayerBuildResult] = [base]
        branch = await self._ensure_branch(worktree, base.layer.id, config_hash, on_progress)
        if branch is not None:
            layers.insert(0, branch)
        dirty = await self._ensure_dirty(worktree, base.layer.id, config_hash, on_progress)
        if dirty is not None:
            layers.insert(0, dirty)
        for layer in layers:
            self.store.touch_layer(layer.layer.id)
        return layers

    async def _ensure_base(
        self,
        worktree: Worktree,
        config_hash: str,
        on_progress: Callable[[IndexingProgress], None] | None,
    ) -> LayerBuildResult:
        layer_id = _sha_short(
            "\0".join(
                [
                    "base",
                    worktree.repository.id,
                    worktree.branch.base_ref,
                    worktree.branch.base_commit,
                    config_hash,
                ]
            )
        )
        return await self._ensure_layer(
            worktree=worktree,
            layer_id=layer_id,
            kind=LayerKind.BASE,
            ref_name=worktree.branch.base_ref,
            commit=worktree.branch.base_commit,
            base_commit=None,
            merge_base=None,
            base_layer_id=None,
            worktree_id=None,
            config_hash=config_hash,
            expires_at=None,
            materialize=lambda source_dir: materialize_commit(
                worktree.repository.root, worktree.branch.base_commit, source_dir
            ),
            affected_paths=(),
            tombstoned_paths=(),
            on_progress=on_progress,
        )

    async def _ensure_branch(
        self,
        worktree: Worktree,
        base_layer_id: str,
        config_hash: str,
        on_progress: Callable[[IndexingProgress], None] | None,
    ) -> LayerBuildResult | None:
        changes = branch_changes(
            worktree.repository.root, worktree.branch.merge_base, worktree.branch.head_commit
        )
        if changes.is_empty:
            return None
        layer_id = _sha_short(
            "\0".join(
                [
                    "branch",
                    worktree.repository.id,
                    worktree.branch.name,
                    worktree.branch.head_commit,
                    worktree.branch.merge_base,
                    base_layer_id,
                    config_hash,
                ]
            )
        )
        return await self._ensure_layer(
            worktree=worktree,
            layer_id=layer_id,
            kind=LayerKind.BRANCH,
            ref_name=worktree.branch.name,
            commit=worktree.branch.head_commit,
            base_commit=worktree.branch.merge_base,
            merge_base=worktree.branch.merge_base,
            base_layer_id=base_layer_id,
            worktree_id=None,
            config_hash=config_hash,
            expires_at=time.time() + _BRANCH_TTL_SECONDS,
            materialize=lambda source_dir: materialize_paths_from_commit(
                worktree.repository.root,
                worktree.branch.head_commit,
                changes.affected_paths,
                source_dir,
            ),
            affected_paths=changes.affected_paths,
            tombstoned_paths=changes.tombstoned_paths,
            on_progress=on_progress,
        )

    async def _ensure_dirty(
        self,
        worktree: Worktree,
        base_layer_id: str,
        config_hash: str,
        on_progress: Callable[[IndexingProgress], None] | None,
    ) -> LayerBuildResult | None:
        if worktree.dirty.snapshot_hash is None:
            return None
        layer_id = _sha_short(
            "\0".join(
                [
                    "dirty",
                    worktree.repository.id,
                    worktree.id,
                    worktree.branch.name,
                    worktree.branch.head_commit,
                    worktree.dirty.snapshot_hash,
                    config_hash,
                ]
            )
        )
        return await self._ensure_layer(
            worktree=worktree,
            layer_id=layer_id,
            kind=LayerKind.DIRTY,
            ref_name=worktree.branch.name,
            commit=worktree.branch.head_commit,
            base_commit=worktree.branch.merge_base,
            merge_base=worktree.branch.merge_base,
            base_layer_id=base_layer_id,
            worktree_id=worktree.id,
            config_hash=config_hash,
            expires_at=time.time() + _DIRTY_TTL_SECONDS,
            materialize=lambda source_dir: materialize_paths_from_worktree(
                worktree.repository.root, worktree.dirty.affected_paths, source_dir
            ),
            affected_paths=worktree.dirty.affected_paths,
            tombstoned_paths=worktree.dirty.tombstoned_paths,
            on_progress=on_progress,
        )

    async def _ensure_layer(
        self,
        *,
        worktree: Worktree,
        layer_id: str,
        kind: LayerKind,
        ref_name: str | None,
        commit: str | None,
        base_commit: str | None,
        merge_base: str | None,
        base_layer_id: str | None,
        worktree_id: str | None,
        config_hash: str,
        expires_at: float | None,
        materialize: Callable[[Path], None],
        affected_paths: tuple[str, ...],
        tombstoned_paths: tuple[str, ...],
        on_progress: Callable[[IndexingProgress], None] | None,
    ) -> LayerBuildResult:
        paths = LayerPaths.for_layer(self.state_dir, worktree.repository.id, layer_id)
        existing = self.store.get_layer(layer_id)
        built = False
        if (
            existing is None
            or existing.status != "ready"
            or not paths.target_sqlite.exists()
        ):
            built = True
            shutil.rmtree(paths.root, ignore_errors=True)
            paths.source.mkdir(parents=True, exist_ok=True)
            paths.db_dir.mkdir(parents=True, exist_ok=True)
            self.store.upsert_layer(
                layer_id=layer_id,
                repo_id=worktree.repository.id,
                kind=kind,
                ref_name=ref_name,
                commit=commit,
                base_commit=base_commit,
                merge_base=merge_base,
                base_layer_id=base_layer_id,
                worktree_id=worktree_id,
                config_hash=config_hash,
                source_dir=paths.source,
                db_dir=paths.db_dir,
                status="building",
            )
            materialize(paths.source)
            layer = self._require_layer(layer_id)
            runtime = await self._runtime(layer)
            await runtime.run_index(on_progress=on_progress)
            self.store.replace_manifest(
                layer_id,
                affected_paths=affected_paths,
                tombstoned_paths=tombstoned_paths,
                expires_at=expires_at,
            )
            self.store.mark_layer_ready(layer_id)
        layer = self._require_layer(layer_id)
        manifest = self.store.get_manifest(layer_id)
        if manifest is None:
            raise RuntimeError(f"Layer manifest missing after build: {layer_id}")
        runtime = await self._runtime(layer)
        return LayerBuildResult(layer=layer, manifest=manifest, runtime=runtime, built=built)

    def _require_layer(self, layer_id: str) -> Layer:
        layer = self.store.get_layer(layer_id)
        if layer is None:
            raise RuntimeError(f"Layer metadata missing after build: {layer_id}")
        return layer

    async def _runtime(self, layer: Layer) -> LayerRuntime:
        return await LayerRuntime.create(
            layer=layer,
            project_root=self.project_root,
            embedder=self.embedder,
            indexing_params=self.indexing_params,
            query_params=self.query_params,
            chunker_registry=self.chunker_registry,
            project_cache=self.project_cache,
        )

    async def search(
        self,
        *,
        layers: list[LayerBuildResult],
        query: str,
        languages: list[str] | None,
        paths: list[str] | None,
        limit: int,
        offset: int,
    ) -> list[SearchResult]:
        query_embedding = await self.embedder.embed(query, **self.query_params)
        embedding_bytes = query_embedding.astype("float32").tobytes()
        higher_shadowed: set[str] = set()
        merged: list[SearchResult] = []
        for layer in layers:
            raw_results = layer.project.search_with_embedding(
                embedding_bytes=embedding_bytes,
                languages=languages,
                paths=paths,
                limit=limit + offset + 20,
                offset=0,
            )
            for result in raw_results:
                if result.file_path in higher_shadowed:
                    continue
                merged.append(
                    SearchResult(
                        file_path=result.file_path,
                        language=result.language,
                        content=result.content,
                        start_line=result.start_line,
                        end_line=result.end_line,
                        score=result.score,
                        repo_id=layer.layer.repo_id,
                        branch=layer.layer.ref_name,
                        commit=layer.layer.commit_hash,
                        layer_kind=layer.layer.kind.value,
                        layer_id=layer.layer.id,
                    )
                )
            higher_shadowed.update(layer.manifest.affected_paths)
            higher_shadowed.update(layer.manifest.tombstoned_paths)
        merged.sort(key=lambda r: r.score, reverse=True)
        return merged[offset : offset + limit]
