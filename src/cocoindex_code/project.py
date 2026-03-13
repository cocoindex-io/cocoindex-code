from __future__ import annotations

import asyncio

import cocoindex as coco
from cocoindex.connectors import sqlite
from cocoindex.connectors.localfs import register_base_dir

from .config import config
from .indexer import indexer_main
from .shared import CODEBASE_DIR, SQLITE_DB


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
    async def create() -> Project:
        # Ensure index directory exists
        config.index_dir.mkdir(parents=True, exist_ok=True)

        # Set CocoIndex state database path
        settings = coco.Settings.from_env(config.cocoindex_db_path)

        context = coco.ContextProvider()

        # Provide codebase root directory to environment
        context.provide(CODEBASE_DIR, register_base_dir("codebase", config.codebase_root_path))
        # Connect to SQLite with vector extension
        conn = sqlite.connect(str(config.target_sqlite_db_path), load_vec="auto")
        context.provide(SQLITE_DB, sqlite.register_db("index_db", conn))

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


_project: Project | None = None


async def default_project() -> Project:
    """Factory function to create the CocoIndexCode project."""
    global _project
    if _project is None:
        _project = await Project.create()
    return _project
