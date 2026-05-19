from __future__ import annotations

from pathlib import Path

import pytest

from cocoindex_code import _daemon_paths


def test_default_daemon_address_uses_unix_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COCOINDEX_CODE_DAEMON_TCP", raising=False)
    monkeypatch.setenv("COCOINDEX_CODE_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(_daemon_paths.sys, "platform", "linux")

    assert _daemon_paths.connection_family() == "AF_UNIX"
    assert _daemon_paths.daemon_socket_path() == str(tmp_path / "daemon.sock")


def test_daemon_tcp_env_switches_to_af_inet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COCOINDEX_CODE_DAEMON_TCP", "daemon:8765")

    assert _daemon_paths.connection_family() == "AF_INET"
    assert _daemon_paths.daemon_socket_path() == ("daemon", 8765)


def test_daemon_tcp_defaults_empty_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COCOINDEX_CODE_DAEMON_TCP", ":")

    assert _daemon_paths.daemon_socket_path() == ("127.0.0.1", 8765)
