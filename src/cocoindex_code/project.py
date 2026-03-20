"""Project management: wraps a CocoIndex Environment + App."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import cocoindex as coco
from cocoindex.connectors import sqlite

from .indexer import indexer_main
from .protocol import IndexingProgress
from .shared import (
    CODEBASE_DIR,
    EMBEDDER,
    SQLITE_DB,
    Embedder,
)


class Project:
    _env: coco.Environment
    _app: coco.App[[], None]
    _index_lock: asyncio.Lock
    _initial_index_done: asyncio.Event
    _indexing_stats: IndexingProgress | None = None

    def close(self) -> None:
        """Close project resources to release file handles (LMDB, SQLite)."""
        try:
            db = self._env.get_context(SQLITE_DB)
            db.close()
        except Exception:
            pass

    async def run_index(
        self,
        on_progress: Callable[[IndexingProgress], None] | None = None,
        on_started: asyncio.Event | None = None,
    ) -> None:
        """Acquire the index lock, run indexing, and release.

        If *on_started* is provided, it is set once the lock is acquired
        (i.e. indexing has truly begun).  On completion (success or failure)
        ``_initial_index_done`` is set.
        """
        async with self._index_lock:
            self._indexing_stats = IndexingProgress(
                num_execution_starts=0,
                num_unchanged=0,
                num_adds=0,
                num_deletes=0,
                num_reprocesses=0,
                num_errors=0,
            )
            if on_started is not None:
                on_started.set()
            await self._update_index(on_progress=on_progress)

    async def _update_index(
        self,
        on_progress: Callable[[IndexingProgress], None] | None = None,
    ) -> None:
        """Run indexing (lock must already be held)."""
        try:
            handle = self._app.update()
            async for snapshot in handle.watch():
                file_stats = snapshot.stats.by_component.get("process_file")
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
            self._initial_index_done.set()
            self._indexing_stats = None

    @property
    def indexing_stats(self) -> IndexingProgress | None:
        return self._indexing_stats

    @property
    def env(self) -> coco.Environment:
        return self._env

    @staticmethod
    async def create(
        project_root: Path,
        embedder: Embedder,
    ) -> Project:
        """Create a project with explicit embedder.

        Project-level settings and .gitignore are NOT cached here — the
        indexer loads them fresh from disk on every run so that user edits
        take effect without restarting the daemon.
        """
        index_dir = project_root / ".cocoindex_code"
        index_dir.mkdir(parents=True, exist_ok=True)

        cocoindex_db_path = index_dir / "cocoindex.db"
        target_sqlite_db_path = index_dir / "target_sqlite.db"

        settings = coco.Settings.from_env(cocoindex_db_path)

        context = coco.ContextProvider()
        context.provide(CODEBASE_DIR, project_root)
        context.provide(SQLITE_DB, sqlite.connect(str(target_sqlite_db_path), load_vec=True))
        context.provide(EMBEDDER, embedder)

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
        result._initial_index_done = asyncio.Event()
        return result
