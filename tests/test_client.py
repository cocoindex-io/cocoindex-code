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


def test_print_handshake_warnings_dedupes_within_process(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each distinct handshake warning is surfaced at most once per process."""
    from cocoindex_code.protocol import HandshakeResponse

    monkeypatch.setattr(client, "_surfaced_warnings", set())

    resp1 = HandshakeResponse(
        ok=True, daemon_version="x", warnings=["first warning", "second warning"]
    )
    resp2 = HandshakeResponse(
        ok=True, daemon_version="x", warnings=["first warning", "third warning"]
    )

    client._print_handshake_warnings(resp1)
    client._print_handshake_warnings(resp2)

    err = capsys.readouterr().err
    assert err.count("first warning") == 1
    assert err.count("second warning") == 1
    assert err.count("third warning") == 1
    # Every line is rendered through the shared util and gets the "Warning:" prefix.
    assert err.count("Warning:") == 3


def test_print_warning_prefixes_message(capsys: pytest.CaptureFixture[str]) -> None:
    client.print_warning("something happened")
    err = capsys.readouterr().err
    assert err.startswith("Warning: something happened")


def test_print_handshake_warnings_no_warnings_prints_nothing(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from cocoindex_code.protocol import HandshakeResponse

    monkeypatch.setattr(client, "_surfaced_warnings", set())
    client._print_handshake_warnings(HandshakeResponse(ok=True, daemon_version="x"))
    assert capsys.readouterr().err == ""
