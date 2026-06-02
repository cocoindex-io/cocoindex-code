"""Tests for client connection handling."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from cocoindex_code import client
from cocoindex_code._version import __version__
from cocoindex_code.protocol import (
    HandshakeResponse,
    SearchRequest,
    SearchResponse,
    decode_request,
    encode_response,
)


class _FakeConnection:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = responses
        self.sent: list[bytes] = []
        self.closed = False

    def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    def recv_bytes(self) -> bytes:
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


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


def test_find_ccc_executable_ignores_stale_script_shebang(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text("")
    stale_ccc = bin_dir / "ccc"
    stale_ccc.write_text("#!/missing/python\n")
    stale_ccc.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(python))

    assert client._find_ccc_executable() is None


def test_start_daemon_fallback_preserves_source_import_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakePopen:
        def __init__(self, cmd: list[str], **kwargs: object) -> None:
            captured["cmd"] = cmd
            captured["env"] = kwargs["env"]

    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(tmp_path))
    monkeypatch.setattr(client, "_find_ccc_executable", lambda: None)
    monkeypatch.setattr(client.subprocess, "Popen", _FakePopen)

    client.start_daemon()

    env = captured["env"]
    assert isinstance(env, dict)
    pythonpath = env.get("PYTHONPATH")
    assert isinstance(pythonpath, str)
    assert str(Path(client.__file__).resolve().parents[1]) in pythonpath.split(os.pathsep)


def test_print_handshake_warnings_no_warnings_prints_nothing(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from cocoindex_code.protocol import HandshakeResponse

    monkeypatch.setattr(client, "_surfaced_warnings", set())
    client._print_handshake_warnings(HandshakeResponse(ok=True, daemon_version="x"))
    assert capsys.readouterr().err == ""


def test_search_sends_layer_ids_and_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConnection([encode_response(SearchResponse(success=True))])
    monkeypatch.setattr(client, "_connect_and_handshake", lambda: conn)

    resp = client.search(
        project_root="/tmp/project",
        cwd="/tmp/project/src",
        base_ref="main",
        query="hello",
        layer_ids=["base", "branch", "dirty"],
        languages=["python"],
        paths=["src/*"],
        limit=7,
        offset=2,
    )

    assert resp.success is True
    assert conn.closed is True
    assert len(conn.sent) == 1
    req = decode_request(conn.sent[0])
    assert isinstance(req, SearchRequest)
    assert req.project_root == "/tmp/project"
    assert req.cwd == "/tmp/project/src"
    assert req.base_ref == "main"
    assert req.layer_ids == ["base", "branch", "dirty"]
    assert req.languages == ["python"]
    assert req.paths == ["src/*"]
    assert req.limit == 7
    assert req.offset == 2


def test_raw_connect_version_handshake_only_ignores_settings_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnection(
        [
            encode_response(
                HandshakeResponse(
                    ok=True,
                    daemon_version=__version__,
                    global_settings_mtime_us=-1,
                )
            )
        ]
    )

    monkeypatch.setattr(client, "Client", lambda *_args, **_kwargs: conn)
    monkeypatch.setattr(client, "connection_family", lambda: "AF_INET")
    monkeypatch.setattr(client, "daemon_socket_path", lambda: ("daemon", 8765))

    returned = client._raw_connect_version_handshake_only()

    assert returned is conn
    assert len(conn.sent) == 1


def test_raw_connect_version_handshake_only_rejects_protocol_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnection(
        [encode_response(HandshakeResponse(ok=False, daemon_version="old"))]
    )

    monkeypatch.setattr(client, "Client", lambda *_args, **_kwargs: conn)
    monkeypatch.setattr(client, "connection_family", lambda: "AF_INET")
    monkeypatch.setattr(client, "daemon_socket_path", lambda: ("daemon", 8765))

    with pytest.raises(client.DaemonVersionError):
        client._raw_connect_version_handshake_only()

    assert conn.closed is True


def test_raw_connect_and_handshake_rejects_restart_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnection(
        [encode_response(HandshakeResponse(ok=True, daemon_version=__version__))]
    )

    monkeypatch.setattr(client, "Client", lambda *_args, **_kwargs: conn)
    monkeypatch.setattr(client, "connection_family", lambda: "AF_INET")
    monkeypatch.setattr(client, "daemon_socket_path", lambda: ("daemon", 8765))
    monkeypatch.setattr(client, "_needs_restart", lambda _resp: True)

    with pytest.raises(client.DaemonVersionError):
        client._raw_connect_and_handshake()

    assert conn.closed is True


def test_raw_connect_version_handshake_only_closes_on_unexpected_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnection([encode_response(SearchResponse(success=True))])

    monkeypatch.setattr(client, "Client", lambda *_args, **_kwargs: conn)
    monkeypatch.setattr(client, "connection_family", lambda: "AF_INET")
    monkeypatch.setattr(client, "daemon_socket_path", lambda: ("daemon", 8765))

    with pytest.raises(RuntimeError, match="Unexpected handshake response"):
        client._raw_connect_version_handshake_only()

    assert conn.closed is True
