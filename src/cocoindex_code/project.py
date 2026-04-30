"""Project management: wraps a CocoIndex Environment + App."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import cocoindex as coco
from cocoindex.connectors import sqlite as coco_sqlite

from .chunking import CHUNKER_REGISTRY, ChunkerFn
from .indexer import indexer_main
from .protocol import (
    IndexingProgress,
    IndexProgressUpdate,
    IndexResponse,
    IndexStreamResponse,
    IndexWaitingNotice,
    ProjectStatusResponse,
    SearchResult,
)
from .query import query_codebase
from .settings import (
    cocoindex_db_path as _cocoindex_db_path,
)
from .settings import (
    resolve_db_dir,
)
from .settings import (
    target_sqlite_db_path as _target_sqlite_db_path,
)
from .shared import (
    CODEBASE_DIR,
    EMBEDDER,
    INDEXING_EMBED_PARAMS,
    QUERY_EMBED_PARAMS,
    SQLITE_DB,
    Embedder,
)


def _ensure_cocoindex_db_dir(cocoindex_db: Path) -> None:
    """Repair broken LMDB dir symlinks before creating the environment.

    Some repo-local wrappers keep ``.cocoindex_code/cocoindex.db`` symlinked
    into a shared cache. If that shared target is pruned, Rust-side
    ``core.Environment`` creation can fail with ``EEXIST`` while trying to
    create the LMDB directory through the stale symlink. Normalize that state
    here so project creation is tolerant of stale local cache wiring.
    """
    if cocoindex_db.is_symlink() and not cocoindex_db.exists():
        cocoindex_db.unlink()
    cocoindex_db.mkdir(parents=True, exist_ok=True)


class Project:
    _env: coco.Environment
    _app: coco.App[[], None]
    _project_root: Path
    _index_lock: asyncio.Lock
    _initial_index_done: asyncio.Event
    _indexing_stats: IndexingProgress | None = None
    _chunker_registry_ref: dict[str, ChunkerFn]
    _chunkers_ready: bool
    _normalized_query_params: tuple[tuple[str, str], ...]
    _query_embedding_cache: OrderedDict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, Any]]
    _query_embedding_tasks: dict[
        tuple[str, tuple[tuple[str, str], ...]],
        asyncio.Task[Any],
    ]

    _QUERY_CACHE_MAX = 64
    _QUERY_CACHE_TTL_S = 60.0

    def close(self) -> None:
        """Close project resources to release file handles (LMDB, SQLite)."""
        try:
            db = self._env.get_context(SQLITE_DB)
            db.close()
        except Exception:
            pass

    def has_queryable_index(self) -> bool:
        """True when a non-empty target sqlite db already exists on disk."""
        target_db = _target_sqlite_db_path(self._project_root)
        try:
            return target_db.exists() and target_db.stat().st_size > 0
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

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
            await self._run_index_inner(on_progress=on_progress)

    async def _run_index_inner(
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

    async def ensure_indexing_started(self) -> None:
        """Kick off background indexing and wait until it has actually started.

        Returns once the indexing task holds the lock.  Safe to call multiple
        times — only the first call spawns a task; subsequent calls return
        immediately.
        """
        if self._initial_index_done.is_set() or self._index_lock.locked():
            return
        if self.has_queryable_index():
            self._initial_index_done.set()
            return
        started = asyncio.Event()
        asyncio.create_task(self.run_index(on_started=started))
        await started.wait()

    def set_chunker_registry(self, chunker_registry: dict[str, ChunkerFn]) -> None:
        self._chunker_registry_ref.clear()
        self._chunker_registry_ref.update(chunker_registry)
        self._chunkers_ready = True

    @property
    def chunkers_ready(self) -> bool:
        return self._chunkers_ready

    async def stream_index(self) -> AsyncIterator[IndexStreamResponse]:
        """Run indexing, streaming progress updates and a final IndexResponse.

        If the lock is already held, yields ``IndexWaitingNotice`` first.
        The actual indexing runs in a separate task so that client disconnects
        (``GeneratorExit``) do not abort the indexing.
        """
        if self._index_lock.locked():
            yield IndexWaitingNotice()

        progress_queue: asyncio.Queue[IndexingProgress] = asyncio.Queue()
        index_task = asyncio.create_task(
            self.run_index(on_progress=lambda p: progress_queue.put_nowait(p))
        )

        try:
            while not index_task.done():
                try:
                    progress = await asyncio.wait_for(progress_queue.get(), timeout=0.1)
                    yield IndexProgressUpdate(progress=progress)
                except TimeoutError:
                    continue

            while not progress_queue.empty():
                yield IndexProgressUpdate(progress=progress_queue.get_nowait())

            index_task.result()
            yield IndexResponse(success=True)
        except GeneratorExit:
            return
        except Exception as e:
            yield IndexResponse(success=False, message=str(e))

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @property
    def should_wait_for_indexing(self) -> bool:
        """True if indexing has been started but not yet completed."""
        return not self._initial_index_done.is_set()

    async def wait_for_indexing_done(self) -> None:
        """Wait until initial indexing is complete and no indexing is running."""
        await self._initial_index_done.wait()
        if self._index_lock.locked():
            async with self._index_lock:
                pass

    def _query_cache_key(self, query: str) -> tuple[str, tuple[tuple[str, str], ...]]:
        return (query, self._normalized_query_params)

    async def _get_query_embedding(self, query: str) -> Any:
        cache_key = self._query_cache_key(query)
        now = time.monotonic()
        cached = self._query_embedding_cache.get(cache_key)
        if cached is not None:
            cached_at, embedding = cached
            if now - cached_at <= self._QUERY_CACHE_TTL_S:
                self._query_embedding_cache.move_to_end(cache_key)
                return embedding
            self._query_embedding_cache.pop(cache_key, None)

        in_flight = self._query_embedding_tasks.get(cache_key)
        if in_flight is not None:
            return await in_flight

        async def _embed() -> Any:
            embedder = self._env.get_context(EMBEDDER)
            query_params = self._env.get_context(QUERY_EMBED_PARAMS)
            return await embedder.embed(query, **query_params)

        task = asyncio.create_task(_embed())
        self._query_embedding_tasks[cache_key] = task
        try:
            embedding = await task
        finally:
            self._query_embedding_tasks.pop(cache_key, None)
        self._query_embedding_cache[cache_key] = (now, embedding)
        self._query_embedding_cache.move_to_end(cache_key)
        while len(self._query_embedding_cache) > self._QUERY_CACHE_MAX:
            self._query_embedding_cache.popitem(last=False)
        return embedding

    async def search(
        self,
        query: str,
        languages: list[str] | None = None,
        paths: list[str] | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> list[SearchResult]:
        """Search within this project."""
        target_db = _target_sqlite_db_path(self._project_root)
        query_embedding = await self._get_query_embedding(query)
        results = await query_codebase(
            query=query,
            target_sqlite_db_path=target_db,
            env=self._env,
            limit=limit,
            offset=offset,
            languages=languages,
            paths=paths,
            query_embedding=query_embedding,
        )
        return [
            SearchResult(
                file_path=r.file_path,
                language=r.language,
                content=r.content,
                start_line=r.start_line,
                end_line=r.end_line,
                score=r.score,
            )
            for r in results
        ]

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> ProjectStatusResponse:
        """Get index stats by querying the SQLite database."""
        db = self._env.get_context(SQLITE_DB)
        index_exists = True
        try:
            with db.readonly() as conn:
                total_chunks = conn.execute("SELECT COUNT(*) FROM code_chunks_vec").fetchone()[0]
                total_files = conn.execute(
                    "SELECT COUNT(DISTINCT file_path) FROM code_chunks_vec"
                ).fetchone()[0]
                lang_rows = conn.execute(
                    "SELECT language, COUNT(*) as cnt FROM code_chunks_vec"
                    " GROUP BY language ORDER BY cnt DESC"
                ).fetchall()
        except sqlite3.OperationalError:
            index_exists = False
            total_chunks = 0
            total_files = 0
            lang_rows = []

        is_indexing = self._index_lock.locked()
        progress = self._indexing_stats if is_indexing else None
        return ProjectStatusResponse(
            indexing=is_indexing,
            total_chunks=total_chunks,
            total_files=total_files,
            languages={lang: cnt for lang, cnt in lang_rows},
            progress=progress,
            index_exists=index_exists,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def indexing_stats(self) -> IndexingProgress | None:
        return self._indexing_stats

    @property
    def env(self) -> coco.Environment:
        return self._env

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @staticmethod
    async def create(
        project_root: Path,
        embedder: Embedder,
        indexing_params: dict[str, Any],
        query_params: dict[str, Any],
        chunker_registry: dict[str, ChunkerFn] | None = None,
    ) -> Project:
        """Create a project with explicit embedder and per-call params.

        Project-level settings and .gitignore are NOT cached here — the
        indexer loads them fresh from disk on every run so that user edits
        take effect without restarting the daemon.

        Args:
            project_root: Root directory of the codebase to index.
            embedder: Embedding model instance.
            indexing_params: Extra kwargs spread into ``embedder.embed()`` during
                indexing (e.g. ``{"prompt_name": "passage"}``).  Pass ``{}`` for
                no extras.
            query_params: Extra kwargs spread into ``embedder.embed()`` for the
                query side.
            chunker_registry: Optional mapping of file suffix (e.g. ``".toml"``)
                to a ``ChunkerFn``. When a suffix matches, the registered
                chunker is called instead of the built-in splitter.
        """
        settings_dir = project_root / ".cocoindex_code"
        settings_dir.mkdir(parents=True, exist_ok=True)

        db_dir = resolve_db_dir(project_root)
        db_dir.mkdir(parents=True, exist_ok=True)

        cocoindex_db = _cocoindex_db_path(project_root)
        target_sqlite_db = _target_sqlite_db_path(project_root)
        _ensure_cocoindex_db_dir(cocoindex_db)

        settings = coco.Settings.from_env(cocoindex_db)

        context = coco.ContextProvider()
        context.provide(CODEBASE_DIR, project_root)
        context.provide(SQLITE_DB, coco_sqlite.connect(str(target_sqlite_db), load_vec=True))
        context.provide(EMBEDDER, embedder)
        context.provide(INDEXING_EMBED_PARAMS, dict(indexing_params))
        context.provide(QUERY_EMBED_PARAMS, dict(query_params))
        chunker_registry_ref = dict(chunker_registry) if chunker_registry else {}
        context.provide(CHUNKER_REGISTRY, chunker_registry_ref)

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
        result._project_root = project_root
        result._index_lock = asyncio.Lock()
        result._initial_index_done = asyncio.Event()
        result._chunker_registry_ref = chunker_registry_ref
        result._chunkers_ready = chunker_registry is not None
        result._normalized_query_params = tuple(
            sorted((str(k), repr(v)) for k, v in query_params.items())
        )
        result._query_embedding_cache = OrderedDict()
        result._query_embedding_tasks = {}
        if target_sqlite_db.exists():
            try:
                if target_sqlite_db.stat().st_size > 0:
                    result._initial_index_done.set()
            except OSError:
                pass
        return result
