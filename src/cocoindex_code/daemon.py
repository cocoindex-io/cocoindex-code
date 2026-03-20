"""Daemon process: listener loop, project registry, request dispatch."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from multiprocessing.connection import Connection, Listener
from pathlib import Path
from typing import Any

from ._version import __version__
from .project import Project
from .protocol import (
    DaemonProjectInfo,
    DaemonStatusRequest,
    DaemonStatusResponse,
    ErrorResponse,
    HandshakeRequest,
    HandshakeResponse,
    IndexingProgress,
    IndexProgressUpdate,
    IndexRequest,
    IndexResponse,
    IndexStreamResponse,
    IndexWaitingNotice,
    ProjectStatusRequest,
    ProjectStatusResponse,
    RemoveProjectRequest,
    RemoveProjectResponse,
    Request,
    Response,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchStreamResponse,
    StopRequest,
    StopResponse,
    decode_request,
    encode_response,
)
from .query import query_codebase
from .settings import (
    global_settings_mtime_us,
    load_user_settings,
    user_settings_dir,
)
from .shared import SQLITE_DB, Embedder, create_embedder

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Daemon paths
# ---------------------------------------------------------------------------


def daemon_dir() -> Path:
    """Return the daemon directory (``~/.cocoindex_code/``)."""
    return user_settings_dir()


def _connection_family() -> str:
    """Return the multiprocessing connection family for this platform."""
    return "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"


def daemon_socket_path() -> str:
    """Return the daemon socket/pipe address."""
    if sys.platform == "win32":
        import hashlib

        # Hash the daemon dir so COCOINDEX_CODE_DIR overrides create unique pipe names,
        # preventing conflicts between different daemon instances (tests, users, etc.)
        dir_hash = hashlib.md5(str(daemon_dir()).encode()).hexdigest()[:12]
        return rf"\\.\pipe\cocoindex_code_{dir_hash}"
    return str(daemon_dir() / "daemon.sock")


def daemon_pid_path() -> Path:
    """Return the path for the daemon's PID file."""
    return daemon_dir() / "daemon.pid"


def daemon_log_path() -> Path:
    """Return the path for the daemon's log file."""
    return daemon_dir() / "daemon.log"


# ---------------------------------------------------------------------------
# Project Registry
# ---------------------------------------------------------------------------


class ProjectRegistry:
    """Manages loaded projects and their indexes."""

    _projects: dict[str, Project]
    _index_locks: dict[str, asyncio.Lock]
    _embedder: Embedder

    def __init__(self, embedder: Embedder) -> None:
        self._projects = {}
        self._index_locks = {}
        self._load_time_done: dict[str, asyncio.Event] = {}
        self._embedder = embedder

    async def get_project(self, project_root: str, *, suppress_auto_index: bool = False) -> Project:
        """Get or create a Project for the given root. Lazy initialization.

        When a project is newly loaded and *suppress_auto_index* is False,
        a background indexing task (load-time indexing) is fired so the project
        is indexed immediately.  Callers that will index right away (e.g.
        IndexRequest, SearchRequest with refresh) should pass
        ``suppress_auto_index=True``.
        """
        if project_root not in self._projects:
            root = Path(project_root)
            project = await Project.create(root, self._embedder)
            self._projects[project_root] = project
            self._index_locks[project_root] = asyncio.Lock()
            self._load_time_done[project_root] = asyncio.Event()
            if not suppress_auto_index:
                asyncio.create_task(self._run_index(project_root))
        return self._projects[project_root]

    def should_wait_for_indexing(self, project_root: str) -> bool:
        """Check if search should wait before querying.

        Returns True if the index lock is held (indexing actively running)
        or the initial indexing hasn't completed yet (covers the window
        between task creation and lock acquisition).
        """
        lock = self._index_locks.get(project_root)
        if lock is not None and lock.locked():
            return True
        event = self._load_time_done.get(project_root)
        return event is not None and not event.is_set()

    async def wait_for_indexing_done(self, project_root: str) -> None:
        """Wait until no indexing is in progress and initial indexing is complete."""
        # Wait for the initial indexing to complete (if pending)
        event = self._load_time_done.get(project_root)
        if event is not None:
            await event.wait()
        # Wait for any ongoing indexing to finish (lock released)
        lock = self._index_locks.get(project_root)
        if lock is not None and lock.locked():
            await lock.acquire()
            lock.release()

    async def _run_index(
        self,
        project_root: str,
        on_progress: Callable[[IndexingProgress], None] | None = None,
    ) -> None:
        """Run indexing for a project, acquiring and releasing the per-project lock.

        This is the single place where indexing actually happens.  It is used
        both as a fire-and-forget background task (load-time indexing) and as a
        spawned task inside ``update_index`` (client-driven indexing).

        On completion (success or failure) it marks load-time as done
        (idempotent) and releases the lock.
        """
        project = self._projects[project_root]
        lock = self._index_locks[project_root]

        await lock.acquire()
        try:
            await project.update_index(
                on_progress=on_progress,
            )
        except Exception:
            logger.exception("Indexing failed for %s", project_root)
        finally:
            event = self._load_time_done.get(project_root)
            if event is not None:
                event.set()
            lock.release()

    async def update_index(
        self, project_root: str, *, suppress_auto_index: bool = True
    ) -> AsyncIterator[IndexStreamResponse]:
        """Update index, yielding progress updates and a final IndexResponse.

        Streams ``IndexProgressUpdate`` messages while indexing is in progress,
        ending with a terminal ``IndexResponse``.  If the lock is already held,
        yields ``IndexWaitingNotice`` first.

        The actual indexing runs in a separate task (``_run_index``) so that
        client disconnects (``GeneratorExit``) do not abort the indexing.
        """
        await self.get_project(project_root, suppress_auto_index=suppress_auto_index)
        lock = self._index_locks[project_root]

        # If lock is already held, notify the client before blocking
        if lock.locked():
            yield IndexWaitingNotice()

        progress_queue: asyncio.Queue[IndexingProgress] = asyncio.Queue()
        index_task = asyncio.create_task(
            self._run_index(
                project_root,
                on_progress=lambda p: progress_queue.put_nowait(p),
            )
        )

        try:
            # Drain the queue until the task completes
            while not index_task.done():
                try:
                    progress = await asyncio.wait_for(progress_queue.get(), timeout=0.1)
                    yield IndexProgressUpdate(progress=progress)
                except TimeoutError:
                    continue

            # Drain any remaining items
            while not progress_queue.empty():
                yield IndexProgressUpdate(progress=progress_queue.get_nowait())

            # Propagate any exception from the index task
            index_task.result()

            yield IndexResponse(success=True)
        except GeneratorExit:
            # Client disconnected — _run_index continues in background and
            # handles cleanup (release lock, clear _indexing) when done.
            return
        except Exception as e:
            yield IndexResponse(success=False, message=str(e))

    async def search(
        self,
        project_root: str,
        query: str,
        languages: list[str] | None = None,
        paths: list[str] | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> list[SearchResult]:
        """Search within a project."""
        project = await self.get_project(project_root)
        root = Path(project_root)
        target_db = root / ".cocoindex_code" / "target_sqlite.db"
        results = await query_codebase(
            query=query,
            target_sqlite_db_path=target_db,
            env=project.env,
            limit=limit,
            offset=offset,
            languages=languages,
            paths=paths,
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

    def get_status(self, project_root: str) -> ProjectStatusResponse:
        """Get index stats for a project."""
        project = self._projects.get(project_root)
        if project is None:
            return ProjectStatusResponse(
                indexing=False, total_chunks=0, total_files=0, languages={}
            )

        db = project.env.get_context(SQLITE_DB)
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

        lock = self._index_locks.get(project_root)
        is_indexing = lock is not None and lock.locked()
        progress = project.indexing_stats if is_indexing else None
        return ProjectStatusResponse(
            indexing=is_indexing,
            total_chunks=total_chunks,
            total_files=total_files,
            languages={lang: cnt for lang, cnt in lang_rows},
            progress=progress,
            index_exists=index_exists,
        )

    def remove_project(self, project_root: str) -> bool:
        """Remove a project from the registry. Returns True if it was loaded."""
        import gc

        was_loaded = project_root in self._projects
        project = self._projects.pop(project_root, None)
        self._index_locks.pop(project_root, None)
        self._load_time_done.pop(project_root, None)
        if project is not None:
            project.close()
            del project
            gc.collect()
        return was_loaded

    def close_all(self) -> None:
        """Close all loaded projects and release resources."""
        import gc

        for project in self._projects.values():
            project.close()
        self._projects.clear()
        self._index_locks.clear()
        self._load_time_done.clear()
        gc.collect()

    def list_projects(self) -> list[DaemonProjectInfo]:
        """List all loaded projects with their indexing state."""
        return [
            DaemonProjectInfo(
                project_root=root,
                indexing=self._index_locks[root].locked(),
            )
            for root in self._projects
        ]


# ---------------------------------------------------------------------------
# Connection handler
# ---------------------------------------------------------------------------


async def handle_connection(
    conn: Connection,
    registry: ProjectRegistry,
    start_time: float,
    on_shutdown: Callable[[], None],
    settings_mtime_us: int | None,
) -> None:
    """Handle a single client connection (per-request model).

    Reads exactly two messages: a ``HandshakeRequest`` followed by one
    ``Request``.  Sends the response(s) and closes the connection.
    """
    loop = asyncio.get_event_loop()
    try:
        # 1. Handshake
        data: bytes = await loop.run_in_executor(None, conn.recv_bytes)
        req = decode_request(data)

        if not isinstance(req, HandshakeRequest):
            conn.send_bytes(
                encode_response(ErrorResponse(message="First message must be a handshake"))
            )
            return

        ok = req.version == __version__
        conn.send_bytes(
            encode_response(
                HandshakeResponse(
                    ok=ok,
                    daemon_version=__version__,
                    global_settings_mtime_us=settings_mtime_us,
                )
            )
        )
        if not ok:
            return

        # 2. Single request
        data = await loop.run_in_executor(None, conn.recv_bytes)
        req = decode_request(data)

        result = await _dispatch(req, registry, start_time, on_shutdown)
        if isinstance(result, AsyncIterator):
            try:
                async for resp in result:
                    conn.send_bytes(encode_response(resp))
            except Exception as exc:
                logger.exception("Error during streaming response")
                conn.send_bytes(encode_response(ErrorResponse(message=str(exc))))
        else:
            conn.send_bytes(encode_response(result))
    except (EOFError, OSError, asyncio.CancelledError):
        pass
    except Exception:
        logger.exception("Error handling connection")
    finally:
        try:
            conn.close()
        except Exception:
            pass


async def _search_with_wait(
    registry: ProjectRegistry, req: SearchRequest
) -> AsyncIterator[SearchStreamResponse]:
    """Stream search response, waiting for ongoing indexing first."""
    yield IndexWaitingNotice()
    await registry.wait_for_indexing_done(req.project_root)
    try:
        results = await registry.search(
            project_root=req.project_root,
            query=req.query,
            languages=req.languages,
            paths=req.paths,
            limit=req.limit,
            offset=req.offset,
        )
        yield SearchResponse(
            success=True,
            results=results,
            total_returned=len(results),
            offset=req.offset,
        )
    except Exception as e:
        yield ErrorResponse(message=str(e))


async def _dispatch(
    req: Request,
    registry: ProjectRegistry,
    start_time: float,
    on_shutdown: Callable[[], None],
) -> Response | AsyncIterator[IndexStreamResponse] | AsyncIterator[SearchStreamResponse]:
    """Dispatch a request to the appropriate handler.

    Returns a single Response for most requests, or an AsyncIterator for
    streaming requests (IndexRequest, or SearchRequest when waiting for
    load-time indexing).
    """
    try:
        if isinstance(req, IndexRequest):
            return registry.update_index(req.project_root)

        if isinstance(req, SearchRequest):
            # Ensure the project is loaded (may trigger load-time indexing)
            await registry.get_project(req.project_root)

            # If load-time indexing is in progress, return a streaming response
            if registry.should_wait_for_indexing(req.project_root):
                return _search_with_wait(registry, req)

            results = await registry.search(
                project_root=req.project_root,
                query=req.query,
                languages=req.languages,
                paths=req.paths,
                limit=req.limit,
                offset=req.offset,
            )
            return SearchResponse(
                success=True,
                results=results,
                total_returned=len(results),
                offset=req.offset,
            )

        if isinstance(req, ProjectStatusRequest):
            return registry.get_status(req.project_root)

        if isinstance(req, DaemonStatusRequest):
            return DaemonStatusResponse(
                version=__version__,
                uptime_seconds=time.monotonic() - start_time,
                projects=registry.list_projects(),
            )

        if isinstance(req, RemoveProjectRequest):
            registry.remove_project(req.project_root)
            return RemoveProjectResponse(ok=True)

        if isinstance(req, StopRequest):
            on_shutdown()
            return StopResponse(ok=True)

        return ErrorResponse(message=f"Unknown request type: {type(req).__name__}")
    except Exception as e:
        logger.exception("Error dispatching request")
        return ErrorResponse(message=str(e))


# ---------------------------------------------------------------------------
# Daemon main
# ---------------------------------------------------------------------------


def run_daemon() -> None:
    """Main entry point for the daemon process (blocking).

    Sets up the listener, runs the asyncio event loop (``loop.run_forever``)
    to serve connections, and performs cleanup when shutdown is requested via
    ``StopRequest`` or a signal (SIGTERM / SIGINT).
    """
    daemon_dir().mkdir(parents=True, exist_ok=True)

    # Load user settings and record mtime for staleness detection
    user_settings = load_user_settings()
    settings_mtime_us = global_settings_mtime_us()

    # Set environment variables from settings
    for key, value in user_settings.envs.items():
        os.environ[key] = value

    # Create embedder
    embedder = create_embedder(user_settings.embedding)

    # Write PID file
    pid_path = daemon_pid_path()
    pid_path.write_text(str(os.getpid()))

    # Set up logging to file
    log_path = daemon_log_path()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(str(log_path), mode="w"), logging.StreamHandler()],
        force=True,
    )

    logger.info("Daemon starting (PID %d, version %s)", os.getpid(), __version__)

    start_time = time.monotonic()
    registry = ProjectRegistry(embedder)

    sock_path = daemon_socket_path()
    if sys.platform != "win32":
        try:
            Path(sock_path).unlink(missing_ok=True)
        except Exception:
            pass

    listener = Listener(sock_path, family=_connection_family())
    logger.info("Listening on %s", sock_path)

    loop = asyncio.new_event_loop()
    tasks: set[asyncio.Task[Any]] = set()

    def _request_shutdown() -> None:
        """Trigger daemon shutdown — called by StopRequest or signal handler."""
        loop.stop()

    def _spawn_handler(conn: Connection) -> None:
        task = loop.create_task(
            handle_connection(
                conn,
                registry,
                start_time,
                _request_shutdown,
                settings_mtime_us,
            )
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    # Handle signals for graceful shutdown
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _request_shutdown)
    except (RuntimeError, NotImplementedError):
        pass  # Not in main thread, or not supported on this platform (e.g. Windows)

    # Accept loop runs in a background thread; new connections are dispatched
    # to the event loop via call_soon_threadsafe.  The loop exits when
    # listener.close() (called during shutdown) causes accept() to raise.
    def _accept_loop() -> None:
        while True:
            try:
                conn = listener.accept()
                loop.call_soon_threadsafe(_spawn_handler, conn)
            except OSError:
                break

    accept_thread = threading.Thread(target=_accept_loop, daemon=True)
    accept_thread.start()

    # --- Serve until shutdown ---
    try:
        loop.run_forever()
    finally:
        # 1. Stop accepting new connections.
        listener.close()

        # 2. Cancel handler tasks (they may be blocked in run_in_executor).
        for task in tasks:
            task.cancel()
        if tasks:
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

        # 3. Release project resources.
        registry.close_all()
        loop.close()

        # 4. Remove socket and PID file.
        if sys.platform != "win32":
            try:
                Path(sock_path).unlink(missing_ok=True)
            except Exception:
                pass
        try:
            stored = pid_path.read_text().strip()
            if stored == str(os.getpid()):
                pid_path.unlink(missing_ok=True)
        except Exception:
            pass

        logger.info("Daemon stopped")

        # 5. Hard-exit to avoid slow Python teardown (torch, threadpool, etc.).
        #    All resources are already cleaned up above.  Only do this when
        #    running as the main entry point (not when the daemon is started
        #    in-process for testing).
        if threading.current_thread() is threading.main_thread():
            os._exit(0)
