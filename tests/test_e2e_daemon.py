"""End-to-end tests for the CLI → daemon subprocess flow.

These tests start a real daemon subprocess via ``start_daemon()`` and interact
with it through ``DaemonClient``, mirroring how ``ccc index`` / ``ccc search``
actually work.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from cocoindex_code._version import __version__
from cocoindex_code.client import DaemonClient, start_daemon, stop_daemon
from cocoindex_code.daemon import daemon_socket_path
from cocoindex_code.settings import (
    default_project_settings,
    default_user_settings,
    save_project_settings,
    save_user_settings,
)

SAMPLE_PY = '''\
"""Sample module."""

def calculate_fibonacci(n: int) -> int:
    """Calculate the nth Fibonacci number."""
    if n <= 1:
        return n
    return calculate_fibonacci(n - 1) + calculate_fibonacci(n - 2)
'''


@pytest.fixture(scope="module")
def e2e_daemon() -> Iterator[tuple[str, Path]]:
    """Start a real daemon subprocess and return (sock_path, project_dir).

    Uses COCOINDEX_CODE_DIR env var so the subprocess uses the temp directory.
    """
    # Use a short temp dir to stay within AF_UNIX path limit
    base_dir = Path(tempfile.mkdtemp(prefix="ccc_e2e_"))
    project_dir = base_dir / "proj"
    project_dir.mkdir()
    (project_dir / "main.py").write_text(SAMPLE_PY)

    # Set env var BEFORE calling any daemon/settings functions
    old_env = os.environ.get("COCOINDEX_CODE_DIR")
    os.environ["COCOINDEX_CODE_DIR"] = str(base_dir)

    try:
        save_user_settings(default_user_settings())
        save_project_settings(project_dir, default_project_settings())

        start_daemon()

        sock_path = daemon_socket_path()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if os.path.exists(sock_path):
                break
            time.sleep(0.2)
        else:
            log = base_dir / "daemon.log"
            log_content = log.read_text() if log.exists() else "(no log)"
            raise TimeoutError(f"Daemon did not start.\nLog:\n{log_content}")

        yield sock_path, project_dir
    finally:
        stop_daemon()
        if old_env is None:
            os.environ.pop("COCOINDEX_CODE_DIR", None)
        else:
            os.environ["COCOINDEX_CODE_DIR"] = old_env


def test_daemon_subprocess_starts(e2e_daemon: tuple[str, Path]) -> None:
    """The daemon should be reachable via DaemonClient after start_daemon()."""
    client = DaemonClient.connect()
    resp = client.handshake()
    assert resp.ok
    assert resp.daemon_version == __version__
    client.close()


def test_index_and_search_via_client(e2e_daemon: tuple[str, Path]) -> None:
    """Index a project and search via the client, same as ccc index / ccc search."""
    _, project_dir = e2e_daemon

    client = DaemonClient.connect()
    client.handshake()

    resp = client.index(str(project_dir))
    assert resp.success

    status = client.project_status(str(project_dir))
    assert status.total_chunks > 0
    assert status.total_files > 0

    search_resp = client.search(str(project_dir), query="fibonacci")
    assert search_resp.success
    assert len(search_resp.results) > 0
    assert "main.py" in search_resp.results[0].file_path

    client.close()


def test_daemon_survives_client_disconnect(e2e_daemon: tuple[str, Path]) -> None:
    """Daemon should keep running after a client disconnects."""
    _, project_dir = e2e_daemon

    c1 = DaemonClient.connect()
    c1.handshake()
    c1.search(str(project_dir), query="fibonacci")
    c1.close()

    c2 = DaemonClient.connect()
    resp = c2.handshake()
    assert resp.ok
    search_resp = c2.search(str(project_dir), query="fibonacci")
    assert search_resp.success
    assert len(search_resp.results) > 0
    c2.close()
