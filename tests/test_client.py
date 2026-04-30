"""Tests for client connection handling."""

from __future__ import annotations

import signal
import tempfile
import time
from pathlib import Path

import pytest

from cocoindex_code import client
from cocoindex_code.protocol import SearchResponse, SearchWaitingNotice, encode_response


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


def test_stop_daemon_only_signals_pid_from_pidfile(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = Path(tempfile.mkdtemp(prefix="ccc_stop_"))
    pidfile = runtime_dir / "daemon.pid"
    pidfile.write_text("4242")

    kill_calls: list[tuple[int, int]] = []

    class _Conn:
        def send_bytes(self, _data: bytes) -> None:
            return None

        def recv_bytes(self) -> bytes:
            return b""

        def close(self) -> None:
            return None

    monkeypatch.setattr(client, "daemon_pid_path", lambda: pidfile)
    monkeypatch.setattr(client, "_raw_connect_and_handshake", lambda: _Conn())
    monkeypatch.setattr(client, "encode_request", lambda req: b"req")
    monkeypatch.setattr(client, "decode_response", lambda data: object())
    monkeypatch.setattr(client, "_wait_for_daemon_exit", lambda timeout: False)
    monkeypatch.setattr(client, "_pid_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(client.os, "kill", lambda pid, sig: kill_calls.append((pid, sig)))
    monkeypatch.setattr(client, "_cleanup_stale_files", lambda pid_path, pid: None)

    client.stop_daemon()

    assert kill_calls == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]


def test_search_times_out_when_daemon_never_responds(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Conn:
        def send_bytes(self, _data: bytes) -> None:
            return None

        def poll(self, _timeout: float) -> bool:
            return False

        def recv_bytes(self) -> bytes:
            raise AssertionError("recv_bytes should not be called when poll() is false")

        def close(self) -> None:
            return None

    monkeypatch.setattr(client, "_connect_and_handshake", lambda: _Conn())
    monkeypatch.setenv("COCOINDEX_CODE_SEARCH_TIMEOUT_SECONDS", "0.01")

    start = time.monotonic()
    with pytest.raises(TimeoutError, match="Search timed out"):
        client.search("/tmp/project", "hello")
    assert time.monotonic() >= start


def test_search_waiting_notice_calls_on_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    notices = [
        SearchWaitingNotice(phase="search", elapsed_seconds=1.0, message="running"),
    ]

    class _Conn:
        def __init__(self) -> None:
            self._recv_calls = 0

        def send_bytes(self, _data: bytes) -> None:
            return None

        def poll(self, _timeout: float) -> bool:
            return True

        def recv_bytes(self) -> bytes:
            self._recv_calls += 1
            if self._recv_calls == 1:
                return encode_response(notices[0])
            return encode_response(
                SearchResponse(success=True, results=[], total_returned=0, offset=0)
            )

        def close(self) -> None:
            return None

    waiting_calls = 0

    def on_waiting() -> None:
        nonlocal waiting_calls
        waiting_calls += 1

    monkeypatch.setattr(client, "_connect_and_handshake", lambda: _Conn())
    resp = client.search("/tmp/project", "hello", on_waiting=on_waiting)
    assert resp.success is True
    assert waiting_calls == 1
