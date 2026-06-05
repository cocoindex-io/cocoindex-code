from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from .layers import LayerBuildResult, LayerStack, LayerStore
from .project import Project
from .protocol import (
    IndexingProgress,
    IndexResponse,
    IndexStreamResponse,
    ProjectStatusResponse,
    SearchResult,
)
from .settings import load_project_settings
from .shared import Embedder
from .version_control import remote_tracking_ref_for_local_branch, resolve_worktree


def _sha_short(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def build_index_config_hash(
    project_root: Path,
    *,
    indexing_params: dict[str, Any],
    query_params: dict[str, Any],
) -> str:
    settings = load_project_settings(project_root)
    seed = repr(
        (
            settings.include_patterns,
            settings.exclude_patterns,
            [(lo.ext, lo.lang) for lo in settings.language_overrides],
            [(cm.ext, cm.module) for cm in settings.chunkers],
            sorted(indexing_params.items()),
            sorted(query_params.items()),
        )
    )
    return _sha_short(seed)


class LayeredProject:
    """A Project-compatible facade backed by base/branch/dirty Git layers."""

    def __init__(
        self,
        *,
        project_root: Path,
        cwd: Path,
        base_ref: str | None,
        state_dir: Path,
        store: LayerStore,
        embedder: Embedder,
        indexing_params: dict[str, Any],
        query_params: dict[str, Any],
        chunker_registry: dict[str, Any],
        project_cache: dict[str, Project],
        owns_project_cache: bool = True,
    ) -> None:
        self.project_root = project_root
        self.cwd = cwd
        self.base_ref = base_ref
        self.state_dir = state_dir
        self.store = store
        self.embedder = embedder
        self.indexing_params = indexing_params
        self.query_params = query_params
        self.chunker_registry = chunker_registry
        self.project_cache = project_cache
        self.owns_project_cache = owns_project_cache
        self._stack = LayerStack(
            project_root=project_root,
            state_dir=state_dir,
            store=store,
            embedder=embedder,
            indexing_params=indexing_params,
            query_params=query_params,
            chunker_registry=chunker_registry,
            project_cache=project_cache,
        )
        self._index_lock = asyncio.Lock()
        self._initial_index_done = asyncio.Event()
        self._indexing_stats: IndexingProgress | None = None
        self._last_layers: list[LayerBuildResult] = []

    @property
    def should_wait_for_indexing(self) -> bool:
        return not self._initial_index_done.is_set()

    @property
    def indexing_stats(self) -> IndexingProgress | None:
        return self._indexing_stats

    def close(self) -> None:
        if not self.owns_project_cache:
            return
        for project in self.project_cache.values():
            project.close()
        self.project_cache.clear()

    async def ensure_indexing_started(self) -> None:
        if self._initial_index_done.is_set() or self._index_lock.locked():
            return
        await self.run_index()

    async def wait_for_indexing_done(self) -> None:
        await self._initial_index_done.wait()
        if self._index_lock.locked():
            async with self._index_lock:
                pass

    async def stream_index(self) -> AsyncIterator[IndexStreamResponse]:
        if self._index_lock.locked():
            from .protocol import IndexWaitingNotice

            yield IndexWaitingNotice()
        try:
            await self.run_index()
            yield IndexResponse(success=True)
        except Exception as e:
            yield IndexResponse(success=False, message=str(e))

    async def run_index(
        self,
        on_progress: Callable[[IndexingProgress], None] | None = None,
        on_started: asyncio.Event | None = None,
    ) -> None:
        async with self._index_lock:
            self._indexing_stats = IndexingProgress(0, 0, 0, 0, 0, 0)
            if on_started is not None:
                on_started.set()
            try:
                self._last_layers = await self._ensure_layers(on_progress=on_progress)
            finally:
                self._initial_index_done.set()
                self._indexing_stats = None

    async def search(
        self,
        query: str,
        languages: list[str] | None = None,
        paths: list[str] | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> list[SearchResult]:
        layers = self._last_layers or await self._ensure_layers(on_progress=None)
        return await self._stack.search(
            layers=layers,
            query=query,
            languages=languages,
            paths=paths,
            limit=limit,
            offset=offset,
        )

    async def ensure_layer_ids(
        self,
        on_progress: Callable[[IndexingProgress], None] | None = None,
    ) -> list[str]:
        layers = await self.ensure_layer_results(on_progress=on_progress)
        return [layer.layer.id for layer in layers]

    async def ensure_layer_results(
        self,
        on_progress: Callable[[IndexingProgress], None] | None = None,
    ) -> list[LayerBuildResult]:
        layers = await self._ensure_layers(on_progress=on_progress)
        self._last_layers = layers
        return layers

    def get_status(self) -> ProjectStatusResponse:
        total_chunks = 0
        total_files_set: set[str] = set()
        languages: dict[str, int] = {}
        index_exists = bool(self._last_layers)
        for layer in self._last_layers:
            db_path = layer.layer.paths.target_sqlite
            if not db_path.exists():
                continue
            try:
                conn = sqlite3.connect(db_path)
                try:
                    total_chunks += conn.execute(
                        "SELECT COUNT(*) FROM code_chunks_vec"
                    ).fetchone()[0]
                    for (path,) in conn.execute("SELECT DISTINCT file_path FROM code_chunks_vec"):
                        total_files_set.add(path)
                    for lang, count in conn.execute(
                        "SELECT language, COUNT(*) FROM code_chunks_vec GROUP BY language"
                    ):
                        languages[lang] = languages.get(lang, 0) + count
                finally:
                    conn.close()
            except sqlite3.OperationalError:
                index_exists = False
        return ProjectStatusResponse(
            indexing=self._index_lock.locked(),
            total_chunks=total_chunks,
            total_files=len(total_files_set),
            languages=languages,
            progress=self._indexing_stats,
            index_exists=index_exists,
        )

    async def _ensure_layers(
        self,
        on_progress: Callable[[IndexingProgress], None] | None,
    ) -> list[LayerBuildResult]:
        config_hash = build_index_config_hash(
            self.project_root,
            indexing_params=self.indexing_params,
            query_params=self.query_params,
        )
        worktree = resolve_worktree(self.cwd, base_ref=self.base_ref, index_config_hash=config_hash)
        if self.base_ref is None:
            stored_base_ref = self.store.get_overlay_base_ref(worktree.repository.id)
            if stored_base_ref is not None:
                remote_base_ref = remote_tracking_ref_for_local_branch(self.cwd, stored_base_ref)
                if remote_base_ref is not None and remote_base_ref != stored_base_ref:
                    worktree = resolve_worktree(
                        self.cwd, base_ref=remote_base_ref, index_config_hash=config_hash
                    )
                    self.store.upsert_overlay_policy(
                        repo_id=worktree.repository.id, base_ref=remote_base_ref
                    )
                    stored_base_ref = remote_base_ref
                if stored_base_ref != worktree.branch.base_ref:
                    worktree = resolve_worktree(
                        self.cwd, base_ref=stored_base_ref, index_config_hash=config_hash
                    )
        return await self._stack.ensure(
            worktree=worktree,
            config_hash=config_hash,
            on_progress=on_progress,
        )
