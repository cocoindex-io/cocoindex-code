"""Tests for client connection handling."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cocoindex_code import client


def test_client_connect_refuses_when_no_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sock_dir = Path(tempfile.mkdtemp(prefix="ccc_noconn_"))
    sock_path = str(sock_dir / "d.sock")
    monkeypatch.setattr("cocoindex_code.client.daemon_socket_path", lambda: sock_path)

    with pytest.raises(ConnectionRefusedError):
        client._raw_connect_and_handshake()


def test_is_daemon_supervised_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """The supervised branch is controlled by COCOINDEX_CODE_DAEMON_SUPERVISED=1."""
    monkeypatch.delenv("COCOINDEX_CODE_DAEMON_SUPERVISED", raising=False)
    assert client._is_daemon_supervised() is False

    monkeypatch.setenv("COCOINDEX_CODE_DAEMON_SUPERVISED", "1")
    assert client._is_daemon_supervised() is True

    # Anything other than exact "1" is not supervised (avoid accidental truthy values).
    monkeypatch.setenv("COCOINDEX_CODE_DAEMON_SUPERVISED", "true")
    assert client._is_daemon_supervised() is False

    monkeypatch.setenv("COCOINDEX_CODE_DAEMON_SUPERVISED", "0")
    assert client._is_daemon_supervised() is False
