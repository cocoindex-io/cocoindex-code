"""Tests for client connection handling."""

from __future__ import annotations

import tempfile
from multiprocessing.connection import Connection
from pathlib import Path
from typing import cast

import pytest

from cocoindex_code import client
from cocoindex_code._daemon_paths import LastExitMarker
from cocoindex_code.protocol import HandshakeResponse


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
        ok=True, daemon_version="x", pid=1, warnings=["first warning", "second warning"]
    )
    resp2 = HandshakeResponse(
        ok=True, daemon_version="x", pid=1, warnings=["first warning", "third warning"]
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
    client._print_handshake_warnings(HandshakeResponse(ok=True, daemon_version="x", pid=1))
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
    ok_resp = HandshakeResponse(ok=True, daemon_version="v1", pid=42)
    calls = {"raw": 0, "stop": 0, "start": 0}

    def fake_raw() -> client._HandshakeResult:
        calls["raw"] += 1
        if calls["raw"] == 1:
            raise client.DaemonVersionError(
                HandshakeResponse(ok=True, daemon_version="v1", pid=42, global_settings_mtime_us=1)
            )
        return client._HandshakeResult(conn=cast(Connection, sentinel_conn), resp=ok_resp)

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
        raise client.DaemonVersionError(
            HandshakeResponse(ok=False, daemon_version="other-version", pid=42)
        )

    monkeypatch.setattr(client, "_raw_connect_and_handshake", fake_raw)
    monkeypatch.setattr(client, "stop_daemon", lambda: None)
    monkeypatch.setattr(client, "start_daemon", lambda: started.update(start=1))
    monkeypatch.setattr(client, "_wait_for_daemon", lambda **_kw: None)
    monkeypatch.setattr(client, "_is_daemon_supervised", lambda: False)

    with pytest.raises(client.DaemonVersionError):
        client._connect_and_handshake()
    assert started["start"] == 0  # never tried to restart


# ---------------------------------------------------------------------------
# Vanished-daemon handling: graceful-exit marker vs crash
# ---------------------------------------------------------------------------


def _setup_vanished_daemon(
    monkeypatch: pytest.MonkeyPatch,
    *,
    marker: LastExitMarker | None,
    ensured_pid: int = 42,
) -> tuple[object, dict[str, int]]:
    """Scaffolding: an ensured daemon whose next connect is refused.

    ``marker`` is what ``read_last_exit_marker`` returns — a marker with
    ``pid == ensured_pid`` simulates a graceful exit, anything else a crash.
    """
    monkeypatch.setattr(client, "_daemon_ensured", True)
    monkeypatch.setattr(client, "_ensured_daemon_pid", ensured_pid)
    monkeypatch.setattr(client, "_consecutive_crash_restarts", 0)
    monkeypatch.setattr(client, "read_last_exit_marker", lambda: marker)

    sentinel_conn = object()
    ok_resp = HandshakeResponse(ok=True, daemon_version="v1", pid=43)
    calls = {"raw": 0, "start": 0}

    def fake_raw() -> client._HandshakeResult:
        calls["raw"] += 1
        if calls["raw"] == 1:
            raise ConnectionRefusedError("daemon socket not found")
        return client._HandshakeResult(conn=cast(Connection, sentinel_conn), resp=ok_resp)

    monkeypatch.setattr(client, "_raw_connect_and_handshake", fake_raw)
    monkeypatch.setattr(client, "start_daemon", lambda: calls.update(start=calls["start"] + 1))
    monkeypatch.setattr(client, "_wait_for_daemon", lambda **_kw: None)
    monkeypatch.setattr(client, "_is_daemon_supervised", lambda: False)
    return sentinel_conn, calls


def test_connect_restarts_silently_after_graceful_exit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A graceful exit (marker with the ensured daemon's pid — e.g. a
    manual stop) is transparently restarted with no warning.
    """
    marker = LastExitMarker(pid=42, reason="stop_request", timestamp=0.0)
    sentinel_conn, calls = _setup_vanished_daemon(monkeypatch, marker=marker, ensured_pid=42)

    conn = client._connect_and_handshake()

    assert conn is sentinel_conn
    assert calls["start"] == 1  # fresh daemon started
    assert capsys.readouterr().err == ""  # ...but silently
    assert client._consecutive_crash_restarts == 0


def test_connect_warns_and_restarts_after_crash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No marker means the daemon crashed: restart, but loudly."""
    sentinel_conn, calls = _setup_vanished_daemon(monkeypatch, marker=None)

    conn = client._connect_and_handshake()

    assert conn is sentinel_conn
    assert calls["start"] == 1
    err = capsys.readouterr().err
    assert "exited unexpectedly" in err
    assert client._consecutive_crash_restarts == 1


def test_connect_marker_pid_mismatch_counts_as_crash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A marker from a *different* process is not evidence our daemon exited
    gracefully — treat it as a crash.
    """
    marker = LastExitMarker(pid=999, reason="stop_request", timestamp=0.0)
    sentinel_conn, calls = _setup_vanished_daemon(monkeypatch, marker=marker, ensured_pid=42)

    conn = client._connect_and_handshake()

    assert conn is sentinel_conn
    assert calls["start"] == 1
    assert "exited unexpectedly" in capsys.readouterr().err
    assert client._consecutive_crash_restarts == 1


def test_connect_gives_up_after_consecutive_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third consecutive crash raises instead of relaunching a
    crash-looping daemon.
    """
    _sentinel_conn, calls = _setup_vanished_daemon(monkeypatch, marker=None)
    monkeypatch.setattr(client, "_consecutive_crash_restarts", 2)  # two prior crashes

    with pytest.raises(RuntimeError, match="crashed 3 times in a row"):
        client._connect_and_handshake()
    assert calls["start"] == 0  # never tried to restart


def test_crash_counter_resets_on_clean_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """A daemon that answers without needing a restart ends the crash streak,
    and its pid is remembered for future marker matching.
    """
    monkeypatch.setattr(client, "_daemon_ensured", True)
    monkeypatch.setattr(client, "_ensured_daemon_pid", 42)
    monkeypatch.setattr(client, "_consecutive_crash_restarts", 2)

    sentinel_conn = object()
    ok_resp = HandshakeResponse(ok=True, daemon_version="v1", pid=7)
    monkeypatch.setattr(
        client,
        "_raw_connect_and_handshake",
        lambda: client._HandshakeResult(conn=cast(Connection, sentinel_conn), resp=ok_resp),
    )

    conn = client._connect_and_handshake()

    assert conn is sentinel_conn
    assert client._consecutive_crash_restarts == 0
    assert client._ensured_daemon_pid == 7


def test_last_exit_marker_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cocoindex_code._daemon_paths import (
        clear_last_exit_marker,
        read_last_exit_marker,
        write_last_exit_marker,
    )

    monkeypatch.setenv("COCOINDEX_CODE_RUNTIME_DIR", str(tmp_path))
    assert read_last_exit_marker() is None

    write_last_exit_marker(pid=123, reason="stop_request")
    marker = read_last_exit_marker()
    assert marker is not None
    assert marker.pid == 123
    assert marker.reason == "stop_request"
    assert marker.timestamp > 0

    clear_last_exit_marker()
    assert read_last_exit_marker() is None


def test_daemon_version_error_message_reflects_cause() -> None:
    """The error text matches the real cause — not always "version mismatch"."""
    from cocoindex_code.protocol import HandshakeResponse

    version_err = client.DaemonVersionError(HandshakeResponse(ok=False, daemon_version="x", pid=1))
    assert "version mismatch" in str(version_err)

    settings_err = client.DaemonVersionError(
        HandshakeResponse(ok=True, daemon_version="x", pid=1, global_settings_mtime_us=1)
    )
    assert "stale global settings" in str(settings_err)
    assert "version mismatch" not in str(settings_err)
