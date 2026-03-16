"""Daemon process: listener loop, project registry, request dispatch."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import AsyncIterator
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
    StopRequest,
    StopResponse,
    decode_request,
    encode_response,
)
from .query import query_codebase
from .settings import (
    load_project_settings,
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
    _indexing: dict[str, bool]
    _embedder: Embedder

    def __init__(self, embedder: Embedder) -> None:
        self._projects = {}
        self._index_locks = {}
        self._indexing = {}
        self._embedder = embedder

    async def get_project(self, project_root: str, *, suppress_auto_index: bool = False) -> Project:
        """Get or create a Project for the given root. Lazy initialization.

        When a project is newly loaded and *suppress_auto_index* is False,
        a background indexing task is fired so the project is indexed
        immediately.  Callers that will index right away (e.g. IndexRequest,
        SearchRequest with refresh) should pass ``suppress_auto_index=True``.
        """
        if project_root not in self._projects:
            root = Path(project_root)
            project_settings = load_project_settings(root)
            project = await Project.create(root, project_settings, self._embedder)
            self._projects[project_root] = project
            self._index_locks[project_root] = asyncio.Lock()
            self._indexing[project_root] = False

            if not suppress_auto_index:
                asyncio.create_task(self._auto_index(project_root))
        return self._projects[project_root]

    async def _auto_index(self, project_root: str) -> None:
        """Background auto-index, consuming the update_index stream."""
        try:
            async for _ in self.update_index(project_root):
                pass
        except Exception:
            logger.exception("Auto-index failed for %s", project_root)

    async def update_index(
        self, project_root: str, *, suppress_auto_index: bool = True
    ) -> AsyncIterator[IndexStreamResponse]:
        """Update index, yielding progress updates and a final IndexResponse."""
        project = await self.get_project(project_root, suppress_auto_index=suppress_auto_index)
        lock = self._index_locks[project_root]

        # If lock is already held, notify the client and block until released
        if lock.locked():
            yield IndexWaitingNotice()

        async with lock:
            self._indexing[project_root] = True
            try:
                progress_queue: asyncio.Queue[IndexingProgress] = asyncio.Queue()

                def on_progress(progress: IndexingProgress) -> None:
                    progress_queue.put_nowait(progress)

                update_task = asyncio.create_task(project.update_index(on_progress=on_progress))

                # Drain the queue until the update completes
                while not update_task.done():
                    try:
                        progress = await asyncio.wait_for(progress_queue.get(), timeout=0.1)
                        yield IndexProgressUpdate(progress=progress)
                    except TimeoutError:
                        continue

                # Drain any remaining items
                while not progress_queue.empty():
                    yield IndexProgressUpdate(progress=progress_queue.get_nowait())

                # Propagate any exception from the update task
                update_task.result()

                yield IndexResponse(success=True)
            except Exception as e:
                yield IndexResponse(success=False, message=str(e))
            finally:
                self._indexing[project_root] = False

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
        with db.readonly() as conn:
            total_chunks = conn.execute("SELECT COUNT(*) FROM code_chunks_vec").fetchone()[0]
            total_files = conn.execute(
                "SELECT COUNT(DISTINCT file_path) FROM code_chunks_vec"
            ).fetchone()[0]
            lang_rows = conn.execute(
                "SELECT language, COUNT(*) as cnt FROM code_chunks_vec"
                " GROUP BY language ORDER BY cnt DESC"
            ).fetchall()

        is_indexing = self._indexing.get(project_root, False)
        progress = project.indexing_stats if is_indexing else None
        return ProjectStatusResponse(
            indexing=is_indexing,
            total_chunks=total_chunks,
            total_files=total_files,
            languages={lang: cnt for lang, cnt in lang_rows},
            progress=progress,
        )

    def remove_project(self, project_root: str) -> bool:
        """Remove a project from the registry. Returns True if it was loaded."""
        import gc

        was_loaded = project_root in self._projects
        project = self._projects.pop(project_root, None)
        self._index_locks.pop(project_root, None)
        self._indexing.pop(project_root, None)
        if project is not None:
            project.close()
            del project
            gc.collect()
        return was_loaded

    def list_projects(self) -> list[DaemonProjectInfo]:
        """List all loaded projects with their indexing state."""
        return [
            DaemonProjectInfo(
                project_root=root,
                indexing=self._indexing.get(root, False),
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
    shutdown_event: asyncio.Event,
) -> None:
    """Handle a single client connection."""
    loop = asyncio.get_event_loop()
    handshake_done = False

    def _recv() -> bytes:
        """Blocking recv that also checks for shutdown."""
        # Use poll with a timeout so we can check shutdown_event periodically
        while not shutdown_event.is_set():
            if conn.poll(0.5):
                return conn.recv_bytes()
        raise EOFError("shutdown")

    try:
        while not shutdown_event.is_set():
            try:
                data: bytes = await loop.run_in_executor(None, _recv)
            except (EOFError, OSError):
                break

            try:
                req = decode_request(data)
            except Exception as e:
                resp: Response = ErrorResponse(message=f"Invalid request: {e}")
                conn.send_bytes(encode_response(resp))
                continue

            if not handshake_done:
                if not isinstance(req, HandshakeRequest):
                    resp = ErrorResponse(message="First message must be a handshake")
                    conn.send_bytes(encode_response(resp))
                    break

                ok = req.version == __version__
                resp = HandshakeResponse(ok=ok, daemon_version=__version__)
                conn.send_bytes(encode_response(resp))
                if not ok:
                    break
                handshake_done = True
                continue

            result = await _dispatch(req, registry, start_time, shutdown_event)
            if isinstance(result, AsyncIterator):
                try:
                    async for resp in result:
                        conn.send_bytes(encode_response(resp))
                except Exception as exc:
                    logger.exception("Error during streaming response")
                    conn.send_bytes(encode_response(ErrorResponse(message=str(exc))))
            else:
                conn.send_bytes(encode_response(result))

            if isinstance(req, StopRequest):
                break
    except Exception:
        logger.exception("Error handling connection")
    finally:
        try:
            conn.close()
        except Exception:
            pass


async def _dispatch(
    req: Request,
    registry: ProjectRegistry,
    start_time: float,
    shutdown_event: asyncio.Event,
) -> Response | AsyncIterator[IndexStreamResponse]:
    """Dispatch a request to the appropriate handler.

    Returns a single Response for most requests, or an AsyncIterator for
    streaming requests (IndexRequest).
    """
    try:
        if isinstance(req, IndexRequest):
            return registry.update_index(req.project_root)

        if isinstance(req, SearchRequest):
            if req.refresh:
                # Consume the index stream silently for refresh
                async for _ in registry.update_index(req.project_root):
                    pass
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
            shutdown_event.set()
            return StopResponse(ok=True)

        return ErrorResponse(message=f"Unknown request type: {type(req).__name__}")
    except Exception as e:
        logger.exception("Error dispatching request")
        return ErrorResponse(message=str(e))


# ---------------------------------------------------------------------------
# Daemon main
# ---------------------------------------------------------------------------


def run_daemon() -> None:
    """Main entry point for the daemon process (blocking)."""
    daemon_dir().mkdir(parents=True, exist_ok=True)

    # Load user settings
    user_settings = load_user_settings()

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
        handlers=[logging.FileHandler(str(log_path)), logging.StreamHandler()],
        force=True,
    )

    logger.info("Daemon starting (PID %d, version %s)", os.getpid(), __version__)

    try:
        asyncio.run(_async_daemon_main(embedder))
    finally:
        # Clean up PID file and socket (named pipes on Windows clean up automatically)
        try:
            pid_path.unlink(missing_ok=True)
        except Exception:
            pass
        if sys.platform != "win32":
            sock = daemon_socket_path()
            try:
                Path(sock).unlink(missing_ok=True)
            except Exception:
                pass
        logger.info("Daemon stopped")


async def _async_daemon_main(embedder: Embedder) -> None:
    """Async main loop for the daemon."""
    start_time = time.monotonic()
    registry = ProjectRegistry(embedder)
    shutdown_event = asyncio.Event()

    sock_path = daemon_socket_path()
    # Remove stale socket (not applicable for Windows named pipes)
    if sys.platform != "win32":
        try:
            Path(sock_path).unlink(missing_ok=True)
        except Exception:
            pass

    listener = Listener(sock_path, family=_connection_family())
    logger.info("Listening on %s", sock_path)

    loop = asyncio.get_event_loop()

    # Handle signals for graceful shutdown (not supported on all platforms/contexts)
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown_event.set)
    except (RuntimeError, NotImplementedError):
        pass  # Not in main thread, or not supported on this platform (e.g. Windows)

    tasks: set[asyncio.Task[Any]] = set()

    async def _spawn_handler(
        conn: Connection,
        reg: ProjectRegistry,
        st: float,
        evt: asyncio.Event,
        task_set: set[asyncio.Task[Any]],
    ) -> None:
        task = asyncio.create_task(handle_connection(conn, reg, st, evt))
        task_set.add(task)
        task.add_done_callback(task_set.discard)

    # Run accept loop in a thread so we can shut down cleanly
    def _accept_loop() -> None:
        while not shutdown_event.is_set():
            try:
                try:
                    listener._listener._socket.settimeout(0.5)  # type: ignore[attr-defined]
                except AttributeError:
                    pass  # AF_PIPE (Windows) doesn't expose ._socket
                conn = listener.accept()
                # Schedule the handler on the event loop
                asyncio.run_coroutine_threadsafe(
                    _spawn_handler(conn, registry, start_time, shutdown_event, tasks),
                    loop,
                )
            except OSError:
                if shutdown_event.is_set():
                    break
                # Socket timeout — just retry
                continue

    accept_thread = threading.Thread(target=_accept_loop, daemon=True)
    accept_thread.start()

    try:
        await shutdown_event.wait()
    finally:
        listener.close()
        accept_thread.join(timeout=2)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
