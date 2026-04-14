"""End-to-end tests for the CLI → daemon subprocess flow.

These tests start a real daemon subprocess via ``start_daemon()`` and interact
with it through the per-request client functions, mirroring how ``ccc index`` /
``ccc search`` actually work.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import make_test_user_settings

from cocoindex_code import client
from cocoindex_code._version import __version__
from cocoindex_code.client import start_daemon, stop_daemon
from cocoindex_code.daemon import daemon_socket_path
from cocoindex_code.settings import (
    default_project_settings,
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
        save_user_settings(make_test_user_settings())
        save_project_settings(project_dir, default_project_settings())

        proc = start_daemon()

        sock_path = daemon_socket_path()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                log = base_dir / "daemon.log"
                log_content = log.read_text() if log.exists() else "(no log)"
                raise RuntimeError(f"Daemon process exited early.\nLog:\n{log_content}")
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
    """The daemon should be reachable via a fresh connection after start_daemon()."""
    resp = client.daemon_status()
    assert resp.version == __version__


def test_index_and_search_via_client(e2e_daemon: tuple[str, Path]) -> None:
    """Index a project and search via the client, same as ccc index / ccc search."""
    _, project_dir = e2e_daemon

    resp = client.index(str(project_dir))
    assert resp.success

    status = client.project_status(str(project_dir))
    assert status.total_chunks > 0
    assert status.total_files > 0

    search_resp = client.search(str(project_dir), query="fibonacci")
    assert search_resp.success
    assert len(search_resp.results) > 0
    assert "main.py" in search_resp.results[0].file_path


# ---------------------------------------------------------------------------
# No-settings mode + host_path_mappings wiring
# ---------------------------------------------------------------------------


def test_daemon_starts_in_no_settings_mode_without_global_settings() -> None:
    """Daemon started against an empty COCOINDEX_CODE_DIR should come up without
    creating ``global_settings.yml``. The file stays absent; the handshake reports
    ``mtime=None``. Project requests are rejected with a clear "run `ccc init`" error.
    """
    from cocoindex_code.client import stop_daemon as _stop
    from cocoindex_code.protocol import ProjectStatusRequest, encode_request
    from cocoindex_code.settings import user_settings_path

    base_dir = Path(tempfile.mkdtemp(prefix="ccc_nosettings_"))
    old_env = os.environ.get("COCOINDEX_CODE_DIR")
    os.environ["COCOINDEX_CODE_DIR"] = str(base_dir)

    try:
        assert not user_settings_path().is_file()

        proc = start_daemon()
        sock = daemon_socket_path()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                log = base_dir / "daemon.log"
                raise RuntimeError(
                    f"Daemon exited early. Log:\n{log.read_text() if log.exists() else '(none)'}"
                )
            if os.path.exists(sock):
                break
            time.sleep(0.2)
        else:
            raise TimeoutError("Daemon did not start in time")

        # Daemon is up, but global_settings.yml stays absent — no auto-create.
        assert not user_settings_path().is_file()

        # Handshake works and reports mtime=None (no settings yet).
        # Use the lower-level raw handshake so we can inspect the response
        # directly; the high-level client would loop on mtime mismatch.
        from cocoindex_code.client import _raw_connect_and_handshake
        from cocoindex_code.protocol import decode_response

        # _raw_connect_and_handshake does its own handshake read — but it also
        # raises DaemonVersionError when the client-side mtime disagrees. With
        # the file absent on both sides, mtime=None matches, so handshake OK.
        conn = _raw_connect_and_handshake()
        try:
            # Send a project request — should get an ErrorResponse pointing at
            # `ccc init`, not a crash.
            conn.send_bytes(encode_request(ProjectStatusRequest(project_root=str(base_dir))))
            resp = decode_response(conn.recv_bytes())
        finally:
            conn.close()

        from cocoindex_code.protocol import ErrorResponse

        assert isinstance(resp, ErrorResponse)
        assert "ccc init" in resp.message
    finally:
        _stop()
        if old_env is None:
            os.environ.pop("COCOINDEX_CODE_DIR", None)
        else:
            os.environ["COCOINDEX_CODE_DIR"] = old_env


def test_daemon_env_response_includes_host_path_mappings(
    e2e_daemon: tuple[str, Path],
) -> None:
    """``client.daemon_env`` surfaces the parsed COCOINDEX_CODE_HOST_PATH_MAPPING."""
    _, _project_dir = e2e_daemon

    # The session daemon was started without COCOINDEX_CODE_HOST_PATH_MAPPING,
    # so this just verifies the field is exposed on the wire and defaults to empty.
    resp = client.daemon_env()
    assert hasattr(resp, "host_path_mappings")
    assert resp.host_path_mappings == []
