"""Idle-timeout tests: IdleReaper predicate units + in-process daemon E2E.

The E2E tests start the daemon in-process (a background thread) with a
seconds-scale idle timeout injected via ``run_daemon(idle_timeout_s=...,
idle_check_interval_s=...)`` and verify it exits — or refuses to exit — at
the right times, and that the socket and PID file are cleaned up.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from multiprocessing.connection import Client, Connection
from pathlib import Path

import pytest
from conftest import make_test_user_settings

import cocoindex_code.daemon as dm
from cocoindex_code._daemon_paths import (
    connection_family,
    daemon_pid_path,
    read_last_exit_marker,
)
from cocoindex_code._version import __version__
from cocoindex_code.daemon import IdleReaper
from cocoindex_code.protocol import (
    HandshakeRequest,
    IndexProgressUpdate,
    IndexRequest,
    IndexResponse,
    IndexWaitingNotice,
    StopRequest,
    decode_response,
    encode_request,
)
from cocoindex_code.settings import (
    default_project_settings,
    save_project_settings,
    save_user_settings,
)

# ---------------------------------------------------------------------------
# IdleReaper predicate (pure, no event loop)
# ---------------------------------------------------------------------------


def test_reaper_exits_after_timeout_elapsed() -> None:
    reaper = IdleReaper(timeout_s=60.0, supervised=False)
    now = reaper.last_activity + 61.0
    assert reaper.should_exit(now=now, active_handlers=0, indexing=False) is True


def test_reaper_stays_before_timeout() -> None:
    reaper = IdleReaper(timeout_s=60.0, supervised=False)
    now = reaper.last_activity + 59.0
    assert reaper.should_exit(now=now, active_handlers=0, indexing=False) is False


def test_reaper_stays_with_live_handler() -> None:
    reaper = IdleReaper(timeout_s=60.0, supervised=False)
    now = reaper.last_activity + 61.0
    assert reaper.should_exit(now=now, active_handlers=1, indexing=False) is False


def test_reaper_stays_while_indexing() -> None:
    reaper = IdleReaper(timeout_s=60.0, supervised=False)
    now = reaper.last_activity + 61.0
    assert reaper.should_exit(now=now, active_handlers=0, indexing=True) is False


def test_reaper_timeout_zero_never_exits() -> None:
    reaper = IdleReaper(timeout_s=0.0, supervised=False)
    now = reaper.last_activity + 1_000_000.0
    assert reaper.should_exit(now=now, active_handlers=0, indexing=False) is False


def test_reaper_supervised_never_exits() -> None:
    reaper = IdleReaper(timeout_s=60.0, supervised=True)
    now = reaper.last_activity + 1_000_000.0
    assert reaper.should_exit(now=now, active_handlers=0, indexing=False) is False


def test_reaper_activity_resets_idle_clock() -> None:
    reaper = IdleReaper(timeout_s=60.0, supervised=False)
    reaper.last_activity -= 120.0  # long idle...
    reaper.record_activity()  # ...then a connection arrives
    now = reaper.last_activity + 1.0
    assert reaper.should_exit(now=now, active_handlers=0, indexing=False) is False
    assert reaper.idle_seconds(now) == pytest.approx(1.0)


def test_reaper_heartbeat_tracked_separately() -> None:
    reaper = IdleReaper(timeout_s=60.0, supervised=False)
    assert reaper.last_heartbeat is None
    reaper.record_heartbeat()
    assert reaper.last_heartbeat is not None


# ---------------------------------------------------------------------------
# In-process daemon E2E
# ---------------------------------------------------------------------------


@pytest.fixture()
def idle_env(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point COCOINDEX_CODE_DIR at a fresh short temp dir (AF_UNIX path limit)."""
    base_dir = Path(tempfile.mkdtemp(prefix="ccc_idle_"))
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(base_dir))
    return base_dir


def _start_daemon_thread(
    *, idle_timeout_s: float, idle_check_interval_s: float
) -> tuple[threading.Thread, str]:
    """Run the daemon in-process and wait for its socket to appear."""
    thread = threading.Thread(
        target=lambda: dm.run_daemon(
            idle_timeout_s=idle_timeout_s,
            idle_check_interval_s=idle_check_interval_s,
        ),
        daemon=True,
    )
    thread.start()

    sock_path = dm.daemon_socket_path()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if os.path.exists(sock_path):
            return thread, sock_path
        if not thread.is_alive():
            raise RuntimeError("Daemon thread exited before its socket appeared")
        time.sleep(0.05)
    raise TimeoutError("In-process daemon did not start")


def _stop_daemon_thread(thread: threading.Thread, sock_path: str) -> None:
    """Best-effort graceful stop for tests where the daemon is still running."""
    if not thread.is_alive():
        return
    try:
        conn = Client(sock_path, family=connection_family())
        conn.send_bytes(encode_request(HandshakeRequest(version=__version__)))
        conn.recv_bytes()
        conn.send_bytes(encode_request(StopRequest()))
        conn.recv_bytes()
        conn.close()
    except Exception:
        pass
    thread.join(timeout=10)


def _assert_cleaned_up(sock_path: str) -> None:
    assert not os.path.exists(sock_path), "socket file not cleaned up"
    assert not daemon_pid_path().exists(), "PID file not cleaned up"


def _recv_index_stream(conn: Connection) -> IndexResponse:
    while True:
        resp = decode_response(conn.recv_bytes())
        if isinstance(resp, IndexProgressUpdate | IndexWaitingNotice):
            continue
        if isinstance(resp, IndexResponse):
            return resp
        raise AssertionError(f"Unexpected response during indexing: {type(resp).__name__}")


def test_daemon_exits_when_idle(idle_env: Path) -> None:
    """With no client activity the daemon exits after the timeout, and the
    socket + PID file are cleaned up.  Runs in no-settings mode (no embedder)
    to prove an unconfigured daemon still idle-exits.
    """
    thread, sock_path = _start_daemon_thread(idle_timeout_s=1.0, idle_check_interval_s=0.2)
    try:
        thread.join(timeout=15)
        assert not thread.is_alive(), "daemon did not idle-exit"
        _assert_cleaned_up(sock_path)

        # The graceful exit left a marker recording the idle timeout as the
        # reason (the in-process daemon shares pytest's pid).
        marker = read_last_exit_marker()
        assert marker is not None
        assert marker.reason == "idle_timeout"
        assert marker.pid == os.getpid()
    finally:
        _stop_daemon_thread(thread, sock_path)


def test_daemon_does_not_exit_with_live_connection(idle_env: Path) -> None:
    """A live handler task (open connection awaiting its request) blocks the
    idle exit; once the connection closes the daemon exits on the next timeout.
    """
    thread, sock_path = _start_daemon_thread(idle_timeout_s=0.5, idle_check_interval_s=0.1)
    try:
        conn = Client(sock_path, family=connection_family())
        conn.send_bytes(encode_request(HandshakeRequest(version=__version__)))
        conn.recv_bytes()
        # Hold the connection open well past the timeout without sending a request.
        time.sleep(1.5)
        assert thread.is_alive(), "daemon idle-exited despite a live connection"

        conn.close()
        thread.join(timeout=15)
        assert not thread.is_alive(), "daemon did not exit after the connection closed"
        _assert_cleaned_up(sock_path)
    finally:
        _stop_daemon_thread(thread, sock_path)


def test_daemon_does_not_exit_during_index_run(idle_env: Path) -> None:
    """An in-flight index run that outlives the idle timeout is not killed:
    the stream completes successfully, then the daemon idle-exits afterwards.
    """
    save_user_settings(make_test_user_settings())
    project = idle_env / "proj"
    project.mkdir()
    save_project_settings(project, default_project_settings())
    for i in range(60):
        (project / f"module_{i}.py").write_text(
            f'"""Module {i}."""\n\ndef func_{i}(x: int) -> int:\n'
            f'    """Compute something for module {i}."""\n'
            f"    return x * {i} + {i}\n"
        )

    thread, sock_path = _start_daemon_thread(idle_timeout_s=1.0, idle_check_interval_s=0.2)
    try:
        conn = Client(sock_path, family=connection_family())
        conn.send_bytes(encode_request(HandshakeRequest(version=__version__)))
        conn.recv_bytes()
        conn.send_bytes(encode_request(IndexRequest(project_root=str(project))))
        # If the daemon idle-exited mid-run, the stream would break with
        # EOFError instead of delivering a successful IndexResponse.
        final = _recv_index_stream(conn)
        assert final.success is True
        conn.close()

        # After the run finishes and the connection closes, the idle clock
        # restarts and the daemon exits on the next timeout.
        thread.join(timeout=30)
        assert not thread.is_alive(), "daemon did not idle-exit after the index run"
        _assert_cleaned_up(sock_path)
    finally:
        _stop_daemon_thread(thread, sock_path)
