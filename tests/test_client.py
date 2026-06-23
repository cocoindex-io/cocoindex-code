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


def test_connect_restarts_ensured_daemon_on_stale_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-ensured daemon reporting stale global settings (resp.ok True,
    moved mtime) is restarted, not surfaced as an error. This is the `ccc init`
    retry path, where rewriting global_settings.yml changes its mtime.
    """
    from cocoindex_code.protocol import HandshakeResponse

    monkeypatch.setattr(client, "_daemon_ensured", True)

    sentinel_conn = object()
    calls = {"raw": 0, "stop": 0, "start": 0}

    def fake_raw() -> object:
        calls["raw"] += 1
        if calls["raw"] == 1:
            raise client.DaemonVersionError(
                HandshakeResponse(ok=True, daemon_version="v1", global_settings_mtime_us=1)
            )
        return sentinel_conn

    monkeypatch.setattr(client, "_raw_connect_and_handshake", fake_raw)
    monkeypatch.setattr(client, "stop_daemon", lambda: calls.update(stop=calls["stop"] + 1))
    monkeypatch.setattr(client, "start_daemon", lambda: calls.update(start=calls["start"] + 1))
    monkeypatch.setattr(client, "_wait_for_daemon", lambda **_kw: None)
    monkeypatch.setattr(client, "_is_daemon_supervised", lambda: False)

    conn = client._connect_and_handshake()

    assert conn is sentinel_conn
    assert calls["stop"] == 1  # old daemon stopped
    assert calls["start"] == 1  # fresh daemon started to reload settings
    assert calls["raw"] == 2  # reconnected after restart


def test_connect_fails_fast_on_version_mismatch_after_ensured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine version mismatch (resp.ok False) after the daemon was already
    ensured means the binary was swapped under us — fail fast, don't restart.
    """
    from cocoindex_code.protocol import HandshakeResponse

    monkeypatch.setattr(client, "_daemon_ensured", True)
    started = {"start": 0}

    def fake_raw() -> object:
        raise client.DaemonVersionError(HandshakeResponse(ok=False, daemon_version="other-version"))

    monkeypatch.setattr(client, "_raw_connect_and_handshake", fake_raw)
    monkeypatch.setattr(client, "stop_daemon", lambda: None)
    monkeypatch.setattr(client, "start_daemon", lambda: started.update(start=1))
    monkeypatch.setattr(client, "_wait_for_daemon", lambda **_kw: None)
    monkeypatch.setattr(client, "_is_daemon_supervised", lambda: False)

    with pytest.raises(client.DaemonVersionError):
        client._connect_and_handshake()
    assert started["start"] == 0  # never tried to restart


def test_daemon_version_error_message_reflects_cause() -> None:
    """The error text matches the real cause — not always "version mismatch"."""
    from cocoindex_code.protocol import HandshakeResponse

    version_err = client.DaemonVersionError(HandshakeResponse(ok=False, daemon_version="x"))
    assert "version mismatch" in str(version_err)

    settings_err = client.DaemonVersionError(
        HandshakeResponse(ok=True, daemon_version="x", global_settings_mtime_us=1)
    )
    assert "stale global settings" in str(settings_err)
    assert "version mismatch" not in str(settings_err)
