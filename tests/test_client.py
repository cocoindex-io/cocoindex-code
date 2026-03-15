"""Tests for DaemonClient and ensure_daemon()."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from multiprocessing.connection import Client
from pathlib import Path

import pytest

from cocoindex_code._version import __version__
from cocoindex_code.client import DaemonClient
from cocoindex_code.daemon import _connection_family
from cocoindex_code.protocol import (
    HandshakeRequest,
    StopRequest,
    encode_request,
)
from cocoindex_code.settings import (
    default_user_settings,
    save_user_settings,
)


@pytest.fixture()
def daemon_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, Path]:
    """Set up daemon environment for client tests."""
    user_dir = tmp_path / "user_home" / ".cocoindex_code"
    user_dir.mkdir(parents=True)

    if sys.platform == "win32":
        sock_path = rf"\\.\pipe\ccc_client_{uuid.uuid4().hex[:12]}"
    else:
        sock_dir = Path(tempfile.mkdtemp(prefix="ccc_client_"))
        sock_path = str(sock_dir / "d.sock")
    pid_path = user_dir / "daemon.pid"

    monkeypatch.setattr("cocoindex_code.settings.user_settings_dir", lambda: user_dir)
    monkeypatch.setattr(
        "cocoindex_code.settings.user_settings_path",
        lambda: user_dir / "global_settings.yml",
    )
    save_user_settings(default_user_settings())

    # Override socket/pid paths for short AF_UNIX paths
    monkeypatch.setattr("cocoindex_code.daemon.daemon_socket_path", lambda: sock_path)
    monkeypatch.setattr("cocoindex_code.client.daemon_socket_path", lambda: sock_path)
    monkeypatch.setattr("cocoindex_code.client.daemon_pid_path", lambda: pid_path)

    return user_dir, sock_path, pid_path


@pytest.fixture()
def daemon_thread(daemon_env: tuple[Path, str, Path]) -> Iterator[str]:
    """Start daemon in thread, yield socket path."""
    user_dir, sock_path, pid_path = daemon_env

    from cocoindex_code.daemon import run_daemon

    thread = threading.Thread(target=run_daemon, daemon=True)
    thread.start()

    # Wait for socket/pipe
    import os

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if os.path.exists(sock_path):
            break
        time.sleep(0.2)

    yield sock_path

    try:
        conn = Client(sock_path, family=_connection_family())
        conn.send_bytes(encode_request(HandshakeRequest(version=__version__)))
        conn.recv_bytes()
        conn.send_bytes(encode_request(StopRequest()))
        conn.recv_bytes()
        conn.close()
    except Exception:
        pass
    thread.join(timeout=5)


def test_client_connect_to_running_daemon(daemon_thread: str) -> None:
    client = DaemonClient.connect()
    resp = client.handshake()
    assert resp.ok is True
    client.close()


def test_client_connect_refuses_when_no_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sock_dir = Path(tempfile.mkdtemp(prefix="ccc_noconn_"))
    sock_path = str(sock_dir / "d.sock")
    monkeypatch.setattr("cocoindex_code.client.daemon_socket_path", lambda: sock_path)

    with pytest.raises(ConnectionRefusedError):
        DaemonClient.connect()


def test_client_close_is_idempotent(daemon_thread: str) -> None:
    client = DaemonClient.connect()
    client.handshake()
    client.close()
    client.close()  # should not raise
