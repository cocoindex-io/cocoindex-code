"""Integration tests for the daemon process.

Runs the daemon in a background thread with a shared embedder.
Uses a session-scoped fixture to avoid re-creating the daemon for each test.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from collections.abc import Iterator
from multiprocessing.connection import Client, Connection
from pathlib import Path

import pytest

from cocoindex_code._version import __version__
from cocoindex_code.daemon import _connection_family
from cocoindex_code.protocol import (
    DaemonStatusRequest,
    HandshakeRequest,
    IndexProgressUpdate,
    IndexRequest,
    IndexResponse,
    IndexWaitingNotice,
    ProjectStatusRequest,
    Response,
    SearchRequest,
    decode_response,
    encode_request,
)
from cocoindex_code.settings import (
    default_project_settings,
    default_user_settings,
    save_project_settings,
    save_user_settings,
)

SAMPLE_MAIN_PY = '''\
"""Main module."""

def calculate_fibonacci(n: int) -> int:
    """Calculate the nth Fibonacci number."""
    if n <= 1:
        return n
    return calculate_fibonacci(n - 1) + calculate_fibonacci(n - 2)
'''


@pytest.fixture(scope="session")
def daemon_sock() -> Iterator[str]:
    """Start a daemon once per session and return the socket path."""
    import cocoindex_code.daemon as dm
    from cocoindex_code.settings import EmbeddingSettings
    from cocoindex_code.shared import create_embedder
    from cocoindex_code.shared import embedder as shared_emb

    emb = shared_emb if shared_emb is not None else create_embedder(EmbeddingSettings())

    # Use a short path to stay within AF_UNIX limit
    user_dir = Path(tempfile.mkdtemp(prefix="ccc_d_"))
    user_dir.mkdir(parents=True, exist_ok=True)

    # Use COCOINDEX_CODE_DIR env var for isolation instead of direct module patching.
    # Direct patching of dm.user_settings_dir leaks across test modules and causes
    # stop_daemon() in other fixtures to read the wrong PID file (pytest's own PID).
    old_env = os.environ.get("COCOINDEX_CODE_DIR")
    os.environ["COCOINDEX_CODE_DIR"] = str(user_dir)

    # Patch create_embedder to reuse the already-loaded embedder (performance)
    _orig_create_embedder = dm.create_embedder  # type: ignore[attr-defined]
    dm.create_embedder = lambda settings: emb  # type: ignore[attr-defined]

    save_user_settings(default_user_settings())

    thread = threading.Thread(target=dm.run_daemon, daemon=True)
    thread.start()

    sock_path = dm.daemon_socket_path()

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if os.path.exists(sock_path):
            break
        time.sleep(0.1)
    else:
        raise TimeoutError("Daemon did not start")

    yield sock_path

    # Restore patches and env var
    dm.create_embedder = _orig_create_embedder  # type: ignore[attr-defined]
    if old_env is None:
        os.environ.pop("COCOINDEX_CODE_DIR", None)
    else:
        os.environ["COCOINDEX_CODE_DIR"] = old_env


def _recv_index_response(conn: Connection) -> tuple[list[IndexProgressUpdate], IndexResponse]:
    """Read streaming index responses until the final IndexResponse arrives."""
    progress_updates: list[IndexProgressUpdate] = []
    while True:
        resp = decode_response(conn.recv_bytes())
        if isinstance(resp, IndexProgressUpdate):
            progress_updates.append(resp)
            continue
        if isinstance(resp, IndexWaitingNotice):
            continue
        if isinstance(resp, IndexResponse):
            return progress_updates, resp
        raise AssertionError(f"Unexpected response during indexing: {type(resp).__name__}")


@pytest.fixture(scope="session")
def daemon_project(daemon_sock: str) -> str:
    """Create and index a project once for the session. Returns project_root str."""
    project = Path(tempfile.mkdtemp(prefix="ccc_proj_"))
    save_project_settings(project, default_project_settings())
    (project / "main.py").write_text(SAMPLE_MAIN_PY)

    conn = Client(daemon_sock, family=_connection_family())
    conn.send_bytes(encode_request(HandshakeRequest(version=__version__)))
    decode_response(conn.recv_bytes())
    conn.send_bytes(encode_request(IndexRequest(project_root=str(project))))
    _updates, final = _recv_index_response(conn)
    assert final.success is True
    conn.close()

    return str(project)


def _connect_and_handshake(sock_path: str) -> tuple[Connection, Response]:
    conn = Client(sock_path, family=_connection_family())
    conn.send_bytes(encode_request(HandshakeRequest(version=__version__)))
    resp = decode_response(conn.recv_bytes())
    return conn, resp


def test_daemon_starts_and_accepts_handshake(daemon_sock: str) -> None:
    conn, resp = _connect_and_handshake(daemon_sock)
    assert resp.ok is True  # type: ignore[union-attr]
    assert resp.daemon_version == __version__  # type: ignore[union-attr]
    conn.close()


def test_daemon_rejects_version_mismatch(daemon_sock: str) -> None:
    conn = Client(daemon_sock, family=_connection_family())
    conn.send_bytes(encode_request(HandshakeRequest(version="0.0.0-fake")))
    resp = decode_response(conn.recv_bytes())
    assert resp.ok is False  # type: ignore[union-attr]
    conn.close()


def test_daemon_status(daemon_sock: str) -> None:
    conn, _ = _connect_and_handshake(daemon_sock)
    conn.send_bytes(encode_request(DaemonStatusRequest()))
    resp = decode_response(conn.recv_bytes())
    assert resp.version == __version__  # type: ignore[union-attr]
    assert resp.uptime_seconds > 0  # type: ignore[union-attr]
    conn.close()


def test_daemon_project_status_after_index(daemon_sock: str, daemon_project: str) -> None:
    conn, _ = _connect_and_handshake(daemon_sock)
    conn.send_bytes(encode_request(ProjectStatusRequest(project_root=daemon_project)))
    resp = decode_response(conn.recv_bytes())
    assert resp.total_chunks > 0  # type: ignore[union-attr]
    assert resp.total_files > 0  # type: ignore[union-attr]
    conn.close()


def test_daemon_search_after_index(daemon_sock: str, daemon_project: str) -> None:
    conn, _ = _connect_and_handshake(daemon_sock)
    conn.send_bytes(encode_request(SearchRequest(project_root=daemon_project, query="fibonacci")))
    resp = decode_response(conn.recv_bytes())
    assert resp.success is True  # type: ignore[union-attr]
    assert len(resp.results) > 0  # type: ignore[union-attr]
    assert "main.py" in resp.results[0].file_path  # type: ignore[union-attr]
    conn.close()


def test_index_streams_progress(daemon_sock: str) -> None:
    """Indexing a new project should stream IndexProgressUpdate before IndexResponse."""
    project = Path(tempfile.mkdtemp(prefix="ccc_strm_"))
    save_project_settings(project, default_project_settings())
    (project / "main.py").write_text(SAMPLE_MAIN_PY)

    conn, _ = _connect_and_handshake(daemon_sock)
    conn.send_bytes(encode_request(IndexRequest(project_root=str(project))))
    updates, final = _recv_index_response(conn)
    conn.close()

    assert final.success is True
    assert len(updates) > 0, "Expected at least one IndexProgressUpdate"
    for u in updates:
        assert u.progress.num_execution_starts >= 0
