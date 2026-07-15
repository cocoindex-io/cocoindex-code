"""Client for communicating with the daemon.

Per-request connection model: each function opens a fresh connection,
performs the version handshake, sends one request, reads the response(s),
and closes.  There is no persistent connection object.
"""

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
from typing import NamedTuple

from ._daemon_paths import (
    connection_family,
    daemon_log_path,
    daemon_pid_path,
    daemon_runtime_dir,
    daemon_socket_path,
    read_last_exit_marker,
)
from ._version import __version__
from .protocol import (
    DaemonEnvRequest,
    DaemonEnvResponse,
    DaemonStatusResponse,
    DoctorCheckResult,
    DoctorRequest,
    DoctorResponse,
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
from .settings import normalize_input_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-request connection helpers
# ---------------------------------------------------------------------------


_daemon_ensured = False

# PID of the daemon we last completed a handshake with. Matched against the
# graceful-exit marker when the daemon later vanishes, to tell an expected
# exit (e.g. `ccc daemon stop`) from a crash.
_ensured_daemon_pid: int | None = None

# Consecutive crash-restarts in this process. Reset whenever the daemon
# answers without needing a restart; at _MAX_CONSECUTIVE_CRASH_RESTARTS we
# stop relaunching a crash-looping daemon and surface the failure instead.
# In-process state: it matters for the long-lived MCP server, while one-shot
# CLI invocations get at most one crash-restart each — which is fine.
_consecutive_crash_restarts = 0
_MAX_CONSECUTIVE_CRASH_RESTARTS = 3

# Tracks which daemon-side handshake warnings have already been surfaced to
# the user in this process. We print each distinct warning at most once per
# `ccc` invocation — see `_print_handshake_warnings`.
_surfaced_warnings: set[str] = set()


def print_warning(message: str) -> None:
    """Render a user-facing warning to stderr with a uniform style.

    Prefixes with ``Warning:`` and renders in yellow when stderr is a TTY;
    falls through as plain text for pipes / files / CI logs.  Intended as
    the single entry point for warnings the user should notice — reuse it
    for any new warning rather than inventing a local style.
    """
    import click

    click.secho(f"Warning: {message}", fg="yellow", err=True)


def _print_handshake_warnings(resp: HandshakeResponse) -> None:
    """Print any new daemon-side warnings to stderr (once per process).

    The daemon populates ``HandshakeResponse.warnings`` on every handshake;
    the dedup set here ensures a warning is printed at most once within a
    single CLI invocation even though several connections are opened.
    """
    for w in resp.warnings:
        if w in _surfaced_warnings:
            continue
        _surfaced_warnings.add(w)
        print_warning(w)


def _is_daemon_supervised() -> bool:
    """True when an external supervisor (Docker entrypoint loop, systemd, …) owns
    daemon respawn. The client in that mode calls ``stop_daemon`` but never
    ``start_daemon`` — it just waits for the socket to reappear.
    """
    return os.environ.get("COCOINDEX_CODE_DAEMON_SUPERVISED") == "1"


def _connect_and_handshake() -> Connection:
    """Connect to the daemon and perform the version handshake.

    Returns the open connection for the caller to send exactly one request.

    Automatically starts or restarts the daemon when it is absent (first call,
    or it exited since the last request) or running with
    stale global settings (e.g. a ``ccc init`` retry rewrote
    ``global_settings.yml`` after the daemon loaded it). An ensured daemon
    that vanished *without* leaving a graceful-exit marker crashed — that is
    restarted loudly, and given up on after a few consecutive crashes (see
    ``_handle_vanished_daemon``). A genuine *version* mismatch after we have
    already reached a matching daemon means the binary was replaced under us
    mid-session — that fails fast instead of looping on restarts.
    """
    global _daemon_ensured, _ensured_daemon_pid, _consecutive_crash_restarts  # noqa: PLW0603

    try:
        conn, resp = _raw_connect_and_handshake()
        _daemon_ensured = True
        _ensured_daemon_pid = resp.pid
        # The daemon answered without needing a restart — any crash streak is over.
        _consecutive_crash_restarts = 0
        return conn
    except DaemonVersionError as e:
        # `resp.ok` is False only for a real version mismatch. Once we have
        # ensured a matching daemon, a fresh version mismatch means the binary
        # was swapped under us — fail fast. A settings-only restart request
        # (resp.ok True, but the loaded settings mtime moved) is expected;
        # restart the daemon below so it reloads them. (stop_daemon sends a
        # StopRequest, so this restart leaves a graceful-exit marker and the
        # next connect stays silent.)
        if _daemon_ensured and not e.resp.ok:
            raise
        stop_daemon()
    except (ConnectionRefusedError, OSError):
        # No daemon answered. Normal on the first call of this process (start
        # one below). If we had already ensured one, decide crash vs graceful
        # exit — a crash restarts loudly and is capped, a graceful exit (e.g.
        # a manual stop) restarts silently.
        if _daemon_ensured:
            _handle_vanished_daemon()

    if _is_daemon_supervised():
        # Supervisor is responsible for (re)starting the daemon — just wait
        # for the socket to reappear.
        _wait_for_daemon()
    else:
        proc = start_daemon()
        _wait_for_daemon(proc=proc)

    # Verify the fresh daemon is reachable
    for _attempt in range(10):
        try:
            conn, resp = _raw_connect_and_handshake()
            _daemon_ensured = True
            _ensured_daemon_pid = resp.pid
            return conn
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)

    raise RuntimeError("Failed to connect to daemon after starting it")


def _handle_vanished_daemon() -> None:
    """Decide how to react when an ensured daemon no longer answers.

    A graceful exit (``ccc daemon stop``, SIGTERM) leaves a
    last-exit marker carrying the exiting daemon's pid; when it matches the
    pid we handshook with, the restart is expected and silent. No marker (or
    a marker from a different process) means the daemon *crashed*: restart
    it, but warn on stderr, and after ``_MAX_CONSECUTIVE_CRASH_RESTARTS``
    consecutive crashes raise instead of relaunching a crash-looping daemon.
    """
    global _consecutive_crash_restarts  # noqa: PLW0603

    marker = read_last_exit_marker()
    if marker is not None and marker.pid == _ensured_daemon_pid:
        return  # graceful exit — silent transparent restart

    _consecutive_crash_restarts += 1
    if _consecutive_crash_restarts >= _MAX_CONSECUTIVE_CRASH_RESTARTS:
        raise RuntimeError(
            f"cocoindex-code daemon crashed {_consecutive_crash_restarts} times in a row; "
            f"not restarting it again. Check the daemon log ({daemon_log_path()}) "
            "or run `ccc doctor`."
        )
    print_warning(
        "cocoindex-code daemon exited unexpectedly; restarting. "
        f"See {daemon_log_path()} for details."
    )


class _HandshakeResult(NamedTuple):
    conn: Connection
    resp: HandshakeResponse


def _raw_connect_and_handshake() -> _HandshakeResult:
    """Low-level connect + handshake without auto-start logic."""
    sock = daemon_socket_path()
    if sys.platform != "win32" and not os.path.exists(sock):
        raise ConnectionRefusedError(f"Daemon socket not found: {sock}")
    try:
        conn = Client(sock, family=connection_family())
    except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
        raise ConnectionRefusedError(f"Cannot connect to daemon: {e}") from e

    try:
        conn.send_bytes(encode_request(HandshakeRequest(version=__version__)))
        data = conn.recv_bytes()
    except (EOFError, OSError) as e:
        conn.close()
        raise ConnectionRefusedError(f"Handshake failed: {e}") from e

    resp = decode_response(data)
    if isinstance(resp, ErrorResponse):
        conn.close()
        raise RuntimeError(f"Daemon error: {resp.message}")
    if not isinstance(resp, HandshakeResponse):
        conn.close()
        raise RuntimeError(f"Unexpected handshake response: {type(resp).__name__}")
    if not resp.ok or _needs_restart(resp):
        conn.close()
        raise DaemonVersionError(resp)
    _print_handshake_warnings(resp)
    return _HandshakeResult(conn=conn, resp=resp)


class DaemonVersionError(RuntimeError):
    """Raised when the daemon has a version or settings mismatch.

    The first ``_connect_and_handshake()`` call handles this by restarting
    the daemon.  If a mismatch occurs on a subsequent call, it means the
    daemon was replaced mid-session (e.g. after a tool upgrade).
    """

    def __init__(self, resp: HandshakeResponse) -> None:
        self.resp = resp
        if not resp.ok:
            message = (
                f"Daemon version mismatch (daemon={resp.daemon_version}, "
                f"client={__version__}). Please retry — the daemon may need a restart."
            )
        else:
            message = (
                "Daemon is running with stale global settings and needs a restart. Please retry."
            )
        super().__init__(message)


class DaemonStartError(RuntimeError):
    """Raised when the daemon process fails to start.

    Carries the daemon log content so callers can display it to the user.
    """

    def __init__(self, message: str, log: str | None = None) -> None:
        self.log = log
        super().__init__(message)


def _read_daemon_log() -> str | None:
    """Read the daemon log file, returning its content or None."""
    log_path = daemon_log_path()
    try:
        content = log_path.read_text().strip()
        return content if content else None
    except (FileNotFoundError, OSError):
        return None


def _send(req: Request) -> Response:
    """Open connection, handshake, send one request, read one response, close."""
    conn = _connect_and_handshake()
    try:
        conn.send_bytes(encode_request(req))
        data = conn.recv_bytes()
    except (EOFError, OSError) as e:
        raise RuntimeError(f"Connection to daemon lost: {e}") from e
    finally:
        conn.close()
    resp = decode_response(data)
    if isinstance(resp, ErrorResponse):
        raise RuntimeError(f"Daemon error: {resp.message}")
    return resp


# ---------------------------------------------------------------------------
# Public API — one function per request type
# ---------------------------------------------------------------------------


def index(
    project_root: str,
    on_progress: Callable[[IndexingProgress], None] | None = None,
    on_waiting: Callable[[], None] | None = None,
) -> IndexResponse:
    """Request indexing with streaming progress. Blocks until complete."""
    project_root = normalize_input_path(project_root)
    conn = _connect_and_handshake()
    try:
        conn.send_bytes(encode_request(IndexRequest(project_root=project_root)))
        while True:
            try:
                data = conn.recv_bytes()
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
    finally:
        conn.close()


def search(
    project_root: str,
    query: str,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    limit: int = 5,
    offset: int = 0,
    on_waiting: Callable[[], None] | None = None,
) -> SearchResponse:
    """Search the codebase.

    If the daemon sends ``IndexWaitingNotice`` (load-time indexing in
    progress), calls *on_waiting* (if provided) then continues reading
    until the final ``SearchResponse``.
    """
    project_root = normalize_input_path(project_root)
    conn = _connect_and_handshake()
    try:
        conn.send_bytes(
            encode_request(
                SearchRequest(
                    project_root=project_root,
                    query=query,
                    languages=languages,
                    paths=paths,
                    limit=limit,
                    offset=offset,
                )
            )
        )
        while True:
            try:
                data = conn.recv_bytes()
            except EOFError:
                raise RuntimeError("Connection to daemon lost during search")
            resp = decode_response(data)
            if isinstance(resp, ErrorResponse):
                raise RuntimeError(f"Daemon error: {resp.message}")
            if isinstance(resp, IndexWaitingNotice):
                if on_waiting is not None:
                    on_waiting()
                continue
            if isinstance(resp, SearchResponse):
                return resp
            raise RuntimeError(f"Unexpected response: {type(resp).__name__}")
    finally:
        conn.close()


def project_status(project_root: str) -> ProjectStatusResponse:
    return _send(ProjectStatusRequest(project_root=normalize_input_path(project_root)))  # type: ignore[return-value]


def daemon_status() -> DaemonStatusResponse:
    from .protocol import DaemonStatusRequest

    return _send(DaemonStatusRequest())  # type: ignore[return-value]


def remove_project(project_root: str) -> RemoveProjectResponse:
    return _send(RemoveProjectRequest(project_root=normalize_input_path(project_root)))  # type: ignore[return-value]


def stop() -> StopResponse:
    return _send(StopRequest())  # type: ignore[return-value]


def daemon_env() -> DaemonEnvResponse:
    """Get environment variable names from the daemon."""
    return _send(DaemonEnvRequest())  # type: ignore[return-value]


def doctor(
    project_root: str | None = None,
    on_result: Callable[[DoctorCheckResult], None] | None = None,
) -> list[DoctorCheckResult]:
    """Run doctor checks via daemon, streaming results to on_result callback."""
    if project_root is not None:
        project_root = normalize_input_path(project_root)
    conn = _connect_and_handshake()
    try:
        conn.send_bytes(encode_request(DoctorRequest(project_root=project_root)))
        results: list[DoctorCheckResult] = []
        while True:
            try:
                data = conn.recv_bytes()
            except EOFError:
                raise RuntimeError("Connection to daemon lost during doctor checks")
            resp = decode_response(data)
            if isinstance(resp, ErrorResponse):
                detail = f"Daemon error: {resp.message}"
                if resp.traceback:
                    detail += f"\n{resp.traceback}"
                raise RuntimeError(detail)
            if isinstance(resp, DoctorResponse):
                results.append(resp.result)
                if on_result is not None:
                    on_result(resp.result)
                if resp.final:
                    break
            else:
                raise RuntimeError(f"Unexpected response: {type(resp).__name__}")
        return results
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Daemon lifecycle helpers
# ---------------------------------------------------------------------------


def is_daemon_running() -> bool:
    """Check if the daemon is running."""
    if sys.platform == "win32":
        try:
            conn = Client(daemon_socket_path(), family=connection_family())
            conn.close()
            return True
        except (ConnectionRefusedError, OSError):
            return False
    return os.path.exists(daemon_socket_path())


def start_daemon() -> subprocess.Popen[bytes]:
    """Start the daemon as a background process.

    Returns the ``Popen`` object so callers can detect early process death
    (via ``proc.poll()``) instead of waiting for a full timeout.
    """
    daemon_runtime_dir().mkdir(parents=True, exist_ok=True)
    log_path = daemon_log_path()

    ccc_path = _find_ccc_executable()
    if ccc_path:
        cmd = [ccc_path, "run-daemon"]
    else:
        cmd = [sys.executable, "-m", "cocoindex_code.cli", "run-daemon"]

    log_fd = open(log_path, "w")
    if sys.platform == "win32":
        _create_no_window = 0x08000000
        proc = subprocess.Popen(
            cmd,
            stdout=log_fd,
            stderr=log_fd,
            stdin=subprocess.DEVNULL,
            creationflags=_create_no_window,
        )
    else:
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=log_fd,
            stderr=log_fd,
            stdin=subprocess.DEVNULL,
        )
    log_fd.close()
    return proc


def _find_ccc_executable() -> str | None:
    """Find the ccc executable in PATH or the same directory as python."""
    python_dir = Path(sys.executable).parent
    names = ["ccc.exe", "ccc"] if sys.platform == "win32" else ["ccc"]
    for name in names:
        ccc = python_dir / name
        if ccc.exists():
            return str(ccc)
    return None


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* is still running."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = getattr(ctypes, "windll").kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _wait_for_daemon_exit(timeout: float) -> bool:
    """Wait up to *timeout* seconds for the daemon to finish cleanup.

    Returns True when the daemon's PID file is gone (meaning it completed its
    shutdown sequence).  This is more reliable than checking process liveness
    because the daemon process may linger as a zombie.
    """
    pid_path = daemon_pid_path()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_path.exists():
            return True
        time.sleep(0.1)
    return not pid_path.exists()


def stop_daemon() -> None:
    """Stop the daemon gracefully.

    Escalation: StopRequest → SIGTERM → SIGKILL.
    """
    global _daemon_ensured, _ensured_daemon_pid  # noqa: PLW0603
    _daemon_ensured = False
    _ensured_daemon_pid = None
    _surfaced_warnings.clear()
    pid_path = daemon_pid_path()

    pid: int | None = None
    try:
        pid = int(pid_path.read_text().strip())
        if pid == os.getpid():
            pid = None
    except (FileNotFoundError, ValueError):
        pass

    # 1) Graceful StopRequest via socket (bypass auto-start)
    try:
        conn, _resp = _raw_connect_and_handshake()
        try:
            conn.send_bytes(encode_request(StopRequest()))
            conn.recv_bytes()
        finally:
            conn.close()
    except (ConnectionRefusedError, OSError, RuntimeError, DaemonVersionError):
        pass

    if _wait_for_daemon_exit(timeout=3.0):
        return

    # 2) SIGTERM
    if pid is not None and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        if _wait_for_daemon_exit(timeout=2.0):
            return

    # 3) SIGKILL (Unix) — on Windows SIGTERM already calls TerminateProcess
    if sys.platform != "win32" and pid is not None and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    _cleanup_stale_files(pid_path, pid)


def _cleanup_stale_files(pid_path: Path, pid: int | None) -> None:
    """Remove socket and PID file after the daemon has exited."""
    if sys.platform != "win32":
        sock = daemon_socket_path()
        try:
            Path(sock).unlink(missing_ok=True)
        except Exception:
            pass
    if pid is not None:
        try:
            stored = pid_path.read_text().strip()
            if stored == str(pid):
                pid_path.unlink(missing_ok=True)
        except (FileNotFoundError, ValueError):
            pass
    else:
        try:
            pid_path.unlink(missing_ok=True)
        except Exception:
            pass


def _wait_for_daemon(
    timeout: float = 30.0,
    proc: subprocess.Popen[bytes] | None = None,
) -> None:
    """Wait for the daemon socket/pipe to become available.

    If *proc* is given, polls the process each iteration.  When the process
    exits before the socket appears, raises ``DaemonStartError`` immediately
    with the daemon log content — no need to wait for the full timeout.

    Socket existence is checked *before* ``proc.poll()`` so that races with a
    supervisor (e.g. the Docker entrypoint restart loop) don't spuriously raise
    ``DaemonStartError``: if the supervisor wins the bind and our subprocess
    exits because the socket is already in use, the socket is still ready — we
    should return success, not flag a failure.
    """
    deadline = time.monotonic() + timeout
    sock_path = daemon_socket_path()
    while time.monotonic() < deadline:
        if sys.platform == "win32":
            try:
                conn = Client(sock_path, family=connection_family())
                conn.close()
                return
            except (ConnectionRefusedError, OSError):
                pass
        else:
            if os.path.exists(sock_path):
                return

        # Daemon socket not yet up — if we spawned a subprocess that already
        # exited, bail out with its log.
        if proc is not None and proc.poll() is not None:
            log = _read_daemon_log()
            msg = "Daemon process exited before it became ready."
            if log:
                msg += f"\n\nDaemon log:\n{log}"
            raise DaemonStartError(msg, log=log)

        time.sleep(0.2)

    # Timeout — also include log for diagnostics.
    log = _read_daemon_log()
    msg = "Daemon did not start in time."
    if log:
        msg += f"\n\nDaemon log:\n{log}"
    raise DaemonStartError(msg, log=log)


def _needs_restart(resp: HandshakeResponse) -> bool:
    """Check if the daemon needs to be restarted."""
    if not resp.ok:
        return True
    from .settings import global_settings_mtime_us

    current_mtime = global_settings_mtime_us()
    if current_mtime != resp.global_settings_mtime_us:
        return True
    return False
