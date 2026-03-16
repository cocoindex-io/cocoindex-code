"""Client for communicating with the daemon."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from multiprocessing.connection import Client, Connection
from pathlib import Path

from ._version import __version__
from .daemon import _connection_family, daemon_pid_path, daemon_socket_path
from .protocol import (
    DaemonStatusResponse,
    ErrorResponse,
    HandshakeRequest,
    HandshakeResponse,
    IndexingProgress,
    IndexProgressUpdate,
    IndexRequest,
    IndexResponse,
    IndexWaitingNotice,
    ProjectStatusRequest,
    ProjectStatusResponse,
    RemoveProjectRequest,
    RemoveProjectResponse,
    Request,
    Response,
    SearchRequest,
    SearchResponse,
    StopRequest,
    StopResponse,
    decode_response,
    encode_request,
)

logger = logging.getLogger(__name__)


class DaemonClient:
    """Client for communicating with the daemon."""

    _conn: Connection

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    @classmethod
    def connect(cls) -> DaemonClient:
        """Connect to daemon. Raises ConnectionRefusedError if not running."""
        sock = daemon_socket_path()
        if not os.path.exists(sock):
            raise ConnectionRefusedError(f"Daemon socket not found: {sock}")
        try:
            conn = Client(sock, family=_connection_family())
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            raise ConnectionRefusedError(f"Cannot connect to daemon: {e}") from e
        return cls(conn)

    def handshake(self) -> HandshakeResponse:
        """Send version handshake."""
        return self._send(HandshakeRequest(version=__version__))  # type: ignore[return-value]

    def index(
        self,
        project_root: str,
        on_progress: Callable[[IndexingProgress], None] | None = None,
        on_waiting: Callable[[], None] | None = None,
    ) -> IndexResponse:
        """Request indexing with streaming progress. Blocks until complete."""
        self._conn.send_bytes(encode_request(IndexRequest(project_root=project_root)))
        while True:
            try:
                data = self._conn.recv_bytes()
            except EOFError:
                raise RuntimeError("Connection to daemon lost during indexing")
            resp = decode_response(data)
            if isinstance(resp, ErrorResponse):
                raise RuntimeError(f"Daemon error: {resp.message}")
            if isinstance(resp, IndexWaitingNotice):
                if on_waiting is not None:
                    on_waiting()
                continue
            if isinstance(resp, IndexProgressUpdate):
                if on_progress is not None:
                    on_progress(resp.progress)
                continue
            if isinstance(resp, IndexResponse):
                return resp
            raise RuntimeError(f"Unexpected response: {type(resp).__name__}")

    def search(
        self,
        project_root: str,
        query: str,
        languages: list[str] | None = None,
        paths: list[str] | None = None,
        limit: int = 5,
        offset: int = 0,
        refresh: bool = False,
    ) -> SearchResponse:
        """Search the codebase."""
        return self._send(  # type: ignore[return-value]
            SearchRequest(
                project_root=project_root,
                query=query,
                languages=languages,
                paths=paths,
                limit=limit,
                offset=offset,
                refresh=refresh,
            )
        )

    def project_status(self, project_root: str) -> ProjectStatusResponse:
        return self._send(  # type: ignore[return-value]
            ProjectStatusRequest(project_root=project_root)
        )

    def daemon_status(self) -> DaemonStatusResponse:
        from .protocol import DaemonStatusRequest

        return self._send(DaemonStatusRequest())  # type: ignore[return-value]

    def remove_project(self, project_root: str) -> RemoveProjectResponse:
        return self._send(  # type: ignore[return-value]
            RemoveProjectRequest(project_root=project_root)
        )

    def stop(self) -> StopResponse:
        return self._send(StopRequest())  # type: ignore[return-value]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def _send(self, req: Request) -> Response:
        self._conn.send_bytes(encode_request(req))
        data = self._conn.recv_bytes()
        resp = decode_response(data)
        if isinstance(resp, ErrorResponse):
            raise RuntimeError(f"Daemon error: {resp.message}")
        return resp


# ---------------------------------------------------------------------------
# Daemon lifecycle helpers
# ---------------------------------------------------------------------------


def is_daemon_running() -> bool:
    """Check if the daemon is running."""
    return os.path.exists(daemon_socket_path())


def start_daemon() -> None:
    """Start the daemon as a background process."""
    from .daemon import daemon_dir

    daemon_dir().mkdir(parents=True, exist_ok=True)
    log_path = daemon_dir() / "daemon.log"

    # Use the ccc entry point if available, otherwise fall back to python -m
    ccc_path = _find_ccc_executable()
    if ccc_path:
        cmd = [ccc_path, "run-daemon"]
    else:
        cmd = [sys.executable, "-m", "cocoindex_code.cli", "run-daemon"]

    log_fd = open(log_path, "a")
    subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=log_fd,
        stderr=log_fd,
        stdin=subprocess.DEVNULL,
    )
    log_fd.close()


def _find_ccc_executable() -> str | None:
    """Find the ccc executable in PATH or the same directory as python."""
    python_dir = Path(sys.executable).parent
    # On Windows the script is ccc.exe; on Unix it's just ccc
    names = ["ccc.exe", "ccc"] if sys.platform == "win32" else ["ccc"]
    for name in names:
        ccc = python_dir / name
        if ccc.exists():
            return str(ccc)
    return None


def stop_daemon() -> None:
    """Stop the daemon gracefully.

    Sends a StopRequest, waits for the process to exit, falls back to SIGTERM.
    """
    # Step 1: try sending StopRequest
    try:
        client = DaemonClient.connect()
        client.handshake()
        client.stop()
        client.close()
    except (ConnectionRefusedError, OSError, RuntimeError):
        pass

    # Step 2: wait for process to exit (up to 5s)
    pid_path = daemon_pid_path()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and pid_path.exists():
        time.sleep(0.1)

    if not pid_path.exists():
        return  # Clean exit

    # Step 3: if still running, try SIGTERM
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            if pid != os.getpid():
                os.kill(pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass

        # Wait a bit more
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and pid_path.exists():
            time.sleep(0.1)

    # Step 4: clean up stale files
    if sys.platform != "win32":
        sock = daemon_socket_path()
        try:
            Path(sock).unlink(missing_ok=True)
        except Exception:
            pass
    try:
        pid_path.unlink(missing_ok=True)
    except Exception:
        pass


def _wait_for_daemon(timeout: float = 10.0) -> None:
    """Wait for the daemon socket/pipe to become available."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(daemon_socket_path()):
            return
        time.sleep(0.1)
    raise TimeoutError("Daemon did not start in time")


def ensure_daemon() -> DaemonClient:
    """Connect to daemon, starting or restarting as needed.

    1. Try to connect to existing daemon.
    2. If connection refused: start daemon, retry connect with backoff.
    3. If connected but version mismatch: stop old daemon, start new one.
    """
    # Try connecting to existing daemon
    try:
        client = DaemonClient.connect()
        resp = client.handshake()
        if resp.ok:
            return client
        # Version mismatch — restart
        client.close()
        stop_daemon()
    except (ConnectionRefusedError, OSError):
        pass

    # Start daemon
    start_daemon()
    _wait_for_daemon()

    # Connect with retries
    for attempt in range(10):
        try:
            client = DaemonClient.connect()
            resp = client.handshake()
            if resp.ok:
                return client
            raise RuntimeError(
                f"Daemon version mismatch: expected {__version__}, got {resp.daemon_version}"
            )
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)

    raise RuntimeError("Failed to connect to daemon after starting it")
