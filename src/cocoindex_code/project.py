"""Project management: wraps a CocoIndex Environment + App."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import cocoindex as coco
from cocoindex.connectors import sqlite

from .indexer import indexer_main
from .protocol import IndexingProgress
from .settings import PROJECT_SETTINGS, ProjectSettings
from .shared import CODEBASE_DIR, EMBEDDER, SQLITE_DB, Embedder


class Project:
    _env: coco.Environment
    _app: coco.App[[], None]
    _index_lock: asyncio.Lock
    _initial_index_done: bool = False
    _indexing_stats: IndexingProgress | None = None

    async def update_index(
        self,
        *,
        on_progress: Callable[[IndexingProgress], None] | None = None,
    ) -> None:
        """Update the index, streaming progress via callback.

        The lock is NOT acquired here — callers (e.g. ProjectRegistry) are
        responsible for serialization so they can inspect lock state and
        yield one-shot snapshots before blocking.
        """
        try:
            handle = self._app.update()
            async for snapshot in handle.watch():
                file_stats = snapshot.stats.by_processor.get("process_file")
                if file_stats is not None:
                    progress = IndexingProgress(
                        num_execution_starts=file_stats.num_execution_starts,
                        num_unchanged=file_stats.num_unchanged,
                        num_adds=file_stats.num_adds,
                        num_deletes=file_stats.num_deletes,
                        num_reprocesses=file_stats.num_reprocesses,
                        num_errors=file_stats.num_errors,
                    )
                    self._indexing_stats = progress
                    if on_progress is not None:
                        on_progress(progress)
                    await asyncio.sleep(0.1)
        finally:
            self._indexing_stats = None
            self._initial_index_done = True

    @property
    def indexing_stats(self) -> IndexingProgress | None:
        return self._indexing_stats

    @property
    def env(self) -> coco.Environment:
        return self._env

    @property
    def is_initial_index_done(self) -> bool:
        return self._initial_index_done

    @staticmethod
    async def create(
        project_root: Path,
        project_settings: ProjectSettings,
        embedder: Embedder,
    ) -> Project:
        """Create a project with explicit settings and embedder."""
        index_dir = project_root / ".cocoindex_code"
        index_dir.mkdir(parents=True, exist_ok=True)

        cocoindex_db_path = index_dir / "cocoindex.db"
        target_sqlite_db_path = index_dir / "target_sqlite.db"

        settings = coco.Settings.from_env(cocoindex_db_path)

        context = coco.ContextProvider()
        context.provide(CODEBASE_DIR, project_root)
        context.provide(SQLITE_DB, sqlite.connect(str(target_sqlite_db_path), load_vec=True))
        context.provide(EMBEDDER, embedder)
        context.provide(PROJECT_SETTINGS, project_settings)

        env = coco.Environment(settings, context_provider=context)
        app = coco.App(
            coco.AppConfig(
                name="CocoIndexCode",
                environment=env,
            ),
            indexer_main,
        )

        result = Project.__new__(Project)
        result._env = env
        result._app = app
        result._index_lock = asyncio.Lock()
        return result
