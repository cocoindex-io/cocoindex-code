"""Tests for client connection handling."""

from __future__ import annotations

import multiprocessing
import os
import subprocess
import tempfile
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, cast

import pytest

from cocoindex_code import client
from cocoindex_code._daemon_paths import LastExitMarker
from cocoindex_code.protocol import HandshakeResponse


def _concurrent_connect_worker(
    runtime_dir: str,
    barrier: Any,
    starts_path: str,
    ready_path: str,
    result_queue: Any,
) -> None:
    """Run one isolated client process in the daemon-start stampede harness."""
    os.environ["COCOINDEX_CODE_RUNTIME_DIR"] = runtime_dir

    from cocoindex_code import client as worker_client

    raw_calls = 0
    sentinel_conn = cast(Connection, object())
    ok_resp = HandshakeResponse(ok=True, daemon_version="test", pid=42)

    def fake_raw() -> worker_client._HandshakeResult:
        nonlocal raw_calls
        raw_calls += 1
        if raw_calls == 1:
            barrier.wait(timeout=10)
            raise ConnectionRefusedError("simultaneous initial miss")
        if not Path(ready_path).exists():
            raise ConnectionRefusedError("daemon is not ready")
        return worker_client._HandshakeResult(conn=sentinel_conn, resp=ok_resp)

    def fake_start() -> subprocess.Popen[bytes]:
        with Path(starts_path).open("a") as starts_file:
            starts_file.write(f"{os.getpid()}\n")
        Path(ready_path).touch()
        return cast(subprocess.Popen[bytes], object())

    worker_client._raw_connect_and_handshake = fake_raw
    worker_client.start_daemon = fake_start
    worker_client._wait_for_daemon = lambda **_kwargs: None
    worker_client._is_daemon_supervised = lambda: False
    worker_client._daemon_ensured = False

    try:
        worker_client._connect_and_handshake()
    except Exception as exc:
        result_queue.put(f"{type(exc).__name__}: {exc}")
    else:
        result_queue.put(None)


def test_concurrent_clients_start_only_one_daemon(tmp_path: Path) -> None:
    """Independent MCP processes must serialize and deduplicate daemon startup."""
    process_count = 6
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(process_count)
    result_queue = ctx.Queue()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    starts_path = tmp_path / "starts"
    ready_path = tmp_path / "ready"

    processes = [
        ctx.Process(
            target=_concurrent_connect_worker,
            args=(
                str(runtime_dir),
                barrier,
                str(starts_path),
                str(ready_path),
                result_queue,
            ),
        )
        for _ in range(process_count)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)

    assert all(process.exitcode == 0 for process in processes)
    assert [result_queue.get(timeout=1) for _ in processes] == [None] * process_count
    assert len(starts_path.read_text().splitlines()) == 1


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
        if calls["raw"] <= 2:
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
    assert calls["raw"] == 3  # lock recheck, then reconnect after restart


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


def test_connect_restarts_daemon_on_undecodable_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handshake reply that does not even decode — a daemon whose wire
    protocol drifted beyond the version-mismatch path, e.g. a stale
    pre-upgrade daemon (issue #237) — is restarted, not surfaced as a raw
    decode error.
    """
    monkeypatch.setattr(client, "_daemon_ensured", False)

    sentinel_conn = object()
    ok_resp = HandshakeResponse(ok=True, daemon_version="v1", pid=42)
    calls = {"raw": 0, "stop": 0, "start": 0}

    def fake_raw() -> client._HandshakeResult:
        calls["raw"] += 1
        if calls["raw"] <= 2:
            raise client.DaemonProtocolError("Undecodable handshake reply from daemon")
        return client._HandshakeResult(conn=cast(Connection, sentinel_conn), resp=ok_resp)

    monkeypatch.setattr(client, "_raw_connect_and_handshake", fake_raw)
    monkeypatch.setattr(client, "stop_daemon", lambda: calls.update(stop=calls["stop"] + 1))
    monkeypatch.setattr(client, "start_daemon", lambda: calls.update(start=calls["start"] + 1))
    monkeypatch.setattr(client, "_wait_for_daemon", lambda **_kw: None)
    monkeypatch.setattr(client, "_is_daemon_supervised", lambda: False)

    conn = client._connect_and_handshake()

    assert conn is sentinel_conn
    assert calls["stop"] == 1  # incompatible daemon stopped
    assert calls["start"] == 1  # fresh daemon started
    assert calls["raw"] == 3  # lock recheck, then reconnect after restart


def test_connect_fails_fast_on_undecodable_handshake_after_ensured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once a matching daemon was ensured, an undecodable handshake reply
    cannot legitimately happen — fail fast, don't loop on restarts.
    """
    monkeypatch.setattr(client, "_daemon_ensured", True)
    started = {"start": 0}

    def fake_raw() -> client._HandshakeResult:
        raise client.DaemonProtocolError("Undecodable handshake reply from daemon")

    monkeypatch.setattr(client, "_raw_connect_and_handshake", fake_raw)
    monkeypatch.setattr(client, "stop_daemon", lambda: None)
    monkeypatch.setattr(client, "start_daemon", lambda: started.update(start=1))
    monkeypatch.setattr(client, "_wait_for_daemon", lambda **_kw: None)
    monkeypatch.setattr(client, "_is_daemon_supervised", lambda: False)

    with pytest.raises(client.DaemonProtocolError):
        client._connect_and_handshake()
    assert started["start"] == 0  # never tried to restart


def test_stop_daemon_escalates_past_undecodable_handshake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ccc daemon stop`` must still work against a protocol-incompatible
    daemon (issue #237): the graceful StopRequest is impossible, so it falls
    through to the kill escalation instead of crashing.
    """
    monkeypatch.setattr(client, "daemon_pid_path", lambda: tmp_path / "daemon.pid")

    def fake_raw() -> client._HandshakeResult:
        raise client.DaemonProtocolError("Undecodable handshake reply from daemon")

    monkeypatch.setattr(client, "_raw_connect_and_handshake", fake_raw)
    waited = {"n": 0}

    def fake_wait(timeout: float) -> bool:
        waited["n"] += 1
        return True

    monkeypatch.setattr(client, "_wait_for_daemon_exit", fake_wait)

    client.stop_daemon()  # must not raise

    assert waited["n"] == 1  # reached the escalation ladder


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
        if calls["raw"] <= 2:
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


def test_daemon_error_carries_daemon_side_traceback() -> None:
    """Daemon-side tracebacks reach the caller — the client frames alone say nothing.

    Regression guard for issue #270, where a daemon search crash surfaced as a
    bare `Daemon error: <message>` and the reporter had no frame pointing at the
    code that actually failed.
    """
    from cocoindex_code.protocol import ErrorResponse

    err = client._daemon_error(ErrorResponse(message="boom", traceback="Traceback: frame\nfoo"))
    assert "Daemon error: boom" in str(err)
    assert "Traceback: frame\nfoo" in str(err)

    # No traceback recorded (e.g. a deliberate ErrorResponse, not an exception):
    # the message stands alone, with no trailing noise.
    plain = client._daemon_error(ErrorResponse(message="run `ccc init` first"))
    assert str(plain) == "Daemon error: run `ccc init` first"
