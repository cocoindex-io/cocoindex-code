"""Project management: wraps a CocoIndex Environment + App."""

from __future__ import annotations

import asyncio
from pathlib import Path

import cocoindex as coco
from cocoindex.connectors import sqlite

from .indexer import indexer_main
from .settings import PROJECT_SETTINGS, ProjectSettings
from .shared import CODEBASE_DIR, EMBEDDER, SQLITE_DB, Embedder


class Project:
    _env: coco.Environment
    _app: coco.App[[], None]
    _index_lock: asyncio.Lock
    _initial_index_done: bool = False

    async def update_index(self, *, report_to_stdout: bool = False) -> None:
        """Update the index, serializing concurrent calls via lock."""
        async with self._index_lock:
            try:
                await self._app.update(report_to_stdout=report_to_stdout)
            finally:
                self._initial_index_done = True

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
