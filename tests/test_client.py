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
        client._connect_and_handshake()
