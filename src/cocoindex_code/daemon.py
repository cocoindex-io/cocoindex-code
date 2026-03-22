"""Daemon process: listener loop, project registry, request dispatch."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
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
    DaemonEnvRequest,
    DaemonEnvResponse,
    DaemonProjectInfo,
    DaemonStatusRequest,
    DaemonStatusResponse,
    DoctorCheckResult,
    DoctorRequest,
    DoctorResponse,
    DoctorStreamResponse,
    ErrorResponse,
    HandshakeRequest,
    HandshakeResponse,
    IndexRequest,
    IndexStreamResponse,
    IndexWaitingNotice,
    ProjectStatusRequest,
    RemoveProjectRequest,
    RemoveProjectResponse,
    Request,
    Response,
    SearchRequest,
    SearchResponse,
    SearchStreamResponse,
    StopRequest,
    StopResponse,
    decode_request,
    encode_response,
)
from .settings import (
    global_settings_mtime_us,
    load_user_settings,
    resolve_db_dir,
    user_settings_dir,
)
from .shared import Embedder, create_embedder

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
    """Cache of loaded projects, keyed by project root path."""

    _projects: dict[str, Project]
    _embedder: Embedder

    def __init__(self, embedder: Embedder) -> None:
        self._projects = {}
        self._embedder = embedder

    async def get_project(self, project_root: str) -> Project:
        """Get or create a Project for the given root. Lazy initialization."""
        if project_root not in self._projects:
            root = Path(project_root)
            project = await Project.create(root, self._embedder)
            self._projects[project_root] = project
        return self._projects[project_root]

    def remove_project(self, project_root: str) -> bool:
        """Remove a project from the registry. Returns True if it was loaded."""
        import gc

        project = self._projects.pop(project_root, None)
        if project is not None:
            project.close()
            del project
            gc.collect()
            return True
        return False

    def close_all(self) -> None:
        """Close all loaded projects and release resources."""
        import gc

        for project in self._projects.values():
            project.close()
        self._projects.clear()
        gc.collect()

    def list_projects(self) -> list[DaemonProjectInfo]:
        """List all loaded projects with their indexing state."""
        return [
            DaemonProjectInfo(
                project_root=root,
                indexing=project._index_lock.locked(),
            )
            for root, project in self._projects.items()
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
    settings_env_names: list[str],
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

        result = await _dispatch(req, registry, start_time, on_shutdown, settings_env_names)
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
    project: Project, req: SearchRequest
) -> AsyncIterator[SearchStreamResponse]:
    """Stream search response, waiting for ongoing indexing first."""
    yield IndexWaitingNotice()
    await project.wait_for_indexing_done()
    try:
        results = await project.search(
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


async def _handle_doctor(
    req: DoctorRequest,
    registry: ProjectRegistry,
) -> AsyncIterator[DoctorStreamResponse]:
    """Run doctor checks sequentially, yielding results as they complete.

    When ``project_root`` is None, only the model check runs (global scope).
    When ``project_root`` is set, only project-specific checks run (file walk + index status).
    The CLI calls this twice — once without project, once with — so that global checks
    appear before project settings in the output.
    """
    if req.project_root is None:
        # Global-scope checks
        yield DoctorResponse(result=await _check_model(registry._embedder))
    else:
        # Project-scope checks
        yield DoctorResponse(result=await _check_file_walk(req.project_root))
        yield DoctorResponse(result=await _check_index_status(req.project_root))

    # Final marker
    yield DoctorResponse(
        result=DoctorCheckResult(name="done", ok=True, details=[], errors=[]),
        final=True,
    )


async def _check_model(embedder: Embedder) -> DoctorCheckResult:
    """Test the embedding model by embedding a short string."""
    try:
        vec = await embedder.embed("hello world")
        dim = len(vec)
        return DoctorCheckResult(
            name="Model Check",
            ok=True,
            details=[f"Embedding dimension: {dim}"],
            errors=[],
        )
    except Exception as e:
        return DoctorCheckResult(
            name="Model Check",
            ok=False,
            details=[],
            errors=[str(e)],
        )


async def _check_file_walk(project_root_str: str) -> DoctorCheckResult:
    """Walk project files and report counts + gitignore paths."""
    from pathlib import PurePath

    from cocoindex.resources.file import PatternFilePathMatcher

    from .indexer import GitignoreAwareMatcher
    from .settings import load_gitignore_spec, load_project_settings

    project_root = Path(project_root_str)
    try:
        ps = load_project_settings(project_root)
    except FileNotFoundError as e:
        return DoctorCheckResult(name="File Walk", ok=False, details=[], errors=[str(e)])

    gitignore_spec = load_gitignore_spec(project_root)
    base_matcher = PatternFilePathMatcher(
        included_patterns=ps.include_patterns,
        excluded_patterns=ps.exclude_patterns,
    )
    matcher = GitignoreAwareMatcher(base_matcher, gitignore_spec, project_root)

    counts_by_ext: dict[str, int] = {}
    gitignore_dirs: list[str] = []
    total = 0

    def _walk() -> None:
        nonlocal total
        for dirpath_str, dirnames, filenames in os.walk(project_root):
            dirpath = Path(dirpath_str)
            rel_dir = PurePath(dirpath.relative_to(project_root))
            if rel_dir != PurePath(".") and not matcher.is_dir_included(rel_dir):
                dirnames.clear()
                continue

            if (dirpath / ".gitignore").is_file():
                gitignore_dirs.append(str(rel_dir))

            for fname in filenames:
                rel_path = rel_dir / fname if rel_dir != PurePath(".") else PurePath(fname)
                if matcher.is_file_included(rel_path):
                    total += 1
                    ext = PurePath(fname).suffix or "(no ext)"
                    counts_by_ext[ext] = counts_by_ext.get(ext, 0) + 1

    await asyncio.get_event_loop().run_in_executor(None, _walk)

    details = [f"Total matched files: {total}"]
    for ext, count in sorted(counts_by_ext.items(), key=lambda x: -x[1]):
        details.append(f"  {ext}: {count}")
    if gitignore_dirs:
        details.append(f"Loaded .gitignore from: {', '.join(gitignore_dirs)}")

    return DoctorCheckResult(name="File Walk", ok=True, details=details, errors=[])


async def _check_index_status(project_root_str: str) -> DoctorCheckResult:
    """Check index status by querying target_sqlite.db directly."""
    from cocoindex.connectors import sqlite as coco_sqlite

    project_root = Path(project_root_str)
    db_path = resolve_db_dir(project_root) / "target_sqlite.db"
    details = [f"Index: {db_path}"]

    if not db_path.exists():
        details.append("Index not created yet.")
        return DoctorCheckResult(name="Index Status", ok=True, details=details, errors=[])

    try:
        conn = coco_sqlite.connect(str(db_path), load_vec=True)
        try:
            with conn.readonly() as db:
                total_chunks = db.execute("SELECT COUNT(*) FROM code_chunks_vec").fetchone()[0]
                file_rows = db.execute("SELECT DISTINCT file_path FROM code_chunks_vec").fetchall()
                total_files = len(file_rows)
                lang_rows = db.execute(
                    "SELECT language, COUNT(*) FROM code_chunks_vec GROUP BY language"
                ).fetchall()
                languages = {row[0]: row[1] for row in lang_rows}
        finally:
            conn.close()

        details.append(f"Chunks: {total_chunks}")
        details.append(f"Files: {total_files}")
        if languages:
            details.append("Languages:")
            for lang, count in sorted(languages.items(), key=lambda x: -x[1]):
                details.append(f"  {lang}: {count} chunks")
        return DoctorCheckResult(name="Index Status", ok=True, details=details, errors=[])
    except Exception as e:
        return DoctorCheckResult(name="Index Status", ok=False, details=details, errors=[str(e)])


async def _dispatch(
    req: Request,
    registry: ProjectRegistry,
    start_time: float,
    on_shutdown: Callable[[], None],
    settings_env_names: list[str],
) -> (
    Response
    | AsyncIterator[IndexStreamResponse]
    | AsyncIterator[SearchStreamResponse]
    | AsyncIterator[DoctorStreamResponse]
):
    """Dispatch a request to the appropriate handler.

    Returns a single Response for most requests, or an AsyncIterator for
    streaming requests (IndexRequest, SearchRequest when waiting, DoctorRequest).
    """
    try:
        if isinstance(req, IndexRequest):
            project = await registry.get_project(req.project_root)
            return project.stream_index()

        if isinstance(req, SearchRequest):
            project = await registry.get_project(req.project_root)
            await project.ensure_indexing_started()

            if project.should_wait_for_indexing:
                return _search_with_wait(project, req)

            results = await project.search(
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
            project = await registry.get_project(req.project_root)
            await project.ensure_indexing_started()
            return project.get_status()

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

        if isinstance(req, DaemonEnvRequest):
            from .protocol import DbPathMappingEntry
            from .settings import get_db_path_mappings

            return DaemonEnvResponse(
                env_names=sorted(os.environ.keys()),
                settings_env_names=settings_env_names,
                db_path_mappings=[
                    DbPathMappingEntry(source=str(m.source), target=str(m.target))
                    for m in get_db_path_mappings()
                ],
            )

        if isinstance(req, DoctorRequest):
            return _handle_doctor(req, registry)

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
    settings_env_keys = list(user_settings.envs.keys())
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
                settings_env_keys,
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
