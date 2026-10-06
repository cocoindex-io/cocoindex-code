import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client, ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cocoindex_code import cli
from cocoindex_code import client as daemon_client
from cocoindex_code._version import __version__
from cocoindex_code.protocol import IndexResponse, SearchResponse
from cocoindex_code.server import create_mcp_server
from cocoindex_code.settings import _reset_host_path_mapping_cache, find_project_root


@pytest.fixture(autouse=True)
def _isolated_ccc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep every test away from the real user settings, daemon and path mapping."""
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(tmp_path / "ccc_user"))
    monkeypatch.setenv("COCOINDEX_CODE_RUNTIME_DIR", str(tmp_path / "ccc_runtime"))
    monkeypatch.delenv("COCOINDEX_CODE_HOST_PATH_MAPPING", raising=False)
    _reset_host_path_mapping_cache()
    yield
    _reset_host_path_mapping_cache()


async def test_mcp_server_uses_v2_protocol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        daemon_client,
        "search",
        lambda **kwargs: SearchResponse(success=True, offset=kwargs["offset"]),
    )
    project = _make_project(tmp_path / "project")

    server = create_mcp_server(str(project))
    async with Client(server, raise_exceptions=True) as client:
        tools = await client.list_tools()
        result = await client.call_tool(
            "search",
            {"query": "authentication", "refresh_index": False},
        )

    assert [tool.name for tool in tools.tools] == ["search"]
    assert result.structured_content == {
        "success": True,
        "results": [],
        "total_returned": 0,
        "offset": 0,
        "message": None,
        "project_root": str(project),
    }


async def test_mcp_server_reports_own_version() -> None:
    """The handshake advertises our version, not the SDK's or an empty string."""
    server = create_mcp_server(".")
    async with Client(server, raise_exceptions=True) as client:
        assert client.server_info is not None
        assert client.server_info.name == "cocoindex-code"
        assert client.server_info.version == __version__


def _make_project(root: Path) -> Path:
    (root / ".cocoindex_code").mkdir(parents=True)
    (root / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []\n")
    return root


class _DaemonCalls:
    """Records the project root of every daemon call the MCP server makes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.index_roots: list[str] = []
        self.search_roots: list[str] = []

        def _index(project_root: str, **_: Any) -> IndexResponse:
            self.index_roots.append(project_root)
            return IndexResponse(success=True)

        def _search(**kwargs: Any) -> SearchResponse:
            self.search_roots.append(kwargs["project_root"])
            return SearchResponse(success=True)

        monkeypatch.setattr(daemon_client, "index", _index)
        monkeypatch.setattr(daemon_client, "search", _search)


async def test_search_project_path_selects_project_per_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subdirectory of another project routes both indexing and search there."""
    project_a = _make_project(tmp_path / "a")
    project_b = _make_project(tmp_path / "b")
    sub = project_b / "src" / "pkg"
    sub.mkdir(parents=True)
    calls = _DaemonCalls(monkeypatch)

    server = create_mcp_server(str(project_a))
    async with Client(server, raise_exceptions=True) as client:
        other = await client.call_tool("search", {"query": "q", "project_path": str(sub)})
        default = await client.call_tool("search", {"query": "q"})

    assert other.structured_content is not None and other.structured_content["success"] is True
    assert default.structured_content is not None and default.structured_content["success"] is True
    assert other.structured_content["project_root"] == str(project_b)
    assert default.structured_content["project_root"] == str(project_a)
    assert calls.index_roots == [str(project_b), str(project_a)]
    assert calls.search_roots == [str(project_b), str(project_a)]


async def test_search_project_path_without_refresh_only_searches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_a = _make_project(tmp_path / "a")
    project_b = _make_project(tmp_path / "b")
    calls = _DaemonCalls(monkeypatch)

    server = create_mcp_server(str(project_a))
    async with Client(server, raise_exceptions=True) as client:
        await client.call_tool(
            "search", {"query": "q", "project_path": str(project_b), "refresh_index": False}
        )

    assert calls.index_roots == []
    assert calls.search_roots == [str(project_b)]


@pytest.mark.parametrize("project_path", ["", ".", "src", "~/b", "file:///b"])
async def test_search_project_path_must_be_absolute(
    project_path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative path would resolve against the server's cwd, i.e. silently against
    the startup checkout: reject it instead."""
    project_a = _make_project(tmp_path / "a")
    (project_a / "src").mkdir()
    monkeypatch.chdir(project_a)
    calls = _DaemonCalls(monkeypatch)

    server = create_mcp_server(str(project_a))
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("search", {"query": "q", "project_path": project_path})

    assert result.structured_content is not None
    assert result.structured_content["success"] is False
    assert "absolute path" in result.structured_content["message"]
    assert calls.index_roots == [] and calls.search_roots == []


async def test_search_unresolvable_project_path_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_a = _make_project(tmp_path / "a")
    calls = _DaemonCalls(monkeypatch)

    server = create_mcp_server(str(project_a))
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "search", {"query": "q", "project_path": str(tmp_path / "a\x00b")}
        )

    assert result.structured_content is not None
    assert result.structured_content["success"] is False
    assert "Could not resolve project_path" in result.structured_content["message"]
    assert calls.index_roots == [] and calls.search_roots == []


async def test_search_project_path_uses_host_path_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In Docker the agent passes a host path: it is mapped to the container path for
    the daemon, and the reported project_root is mapped back to the host form."""
    container_ws = tmp_path / "container_ws"
    project = _make_project(container_ws / "proj")
    (project / "src").mkdir()
    host_ws = tmp_path / "host_ws"
    monkeypatch.setenv("COCOINDEX_CODE_HOST_PATH_MAPPING", f"{container_ws}={host_ws}")
    _reset_host_path_mapping_cache()
    calls = _DaemonCalls(monkeypatch)

    server = create_mcp_server()
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "search", {"query": "q", "project_path": str(host_ws / "proj" / "src")}
        )

    assert result.structured_content is not None
    assert result.structured_content["success"] is True
    assert result.structured_content["project_root"] == str(host_ws / "proj")
    assert calls.index_roots == [str(project)]
    assert calls.search_roots == [str(project)]


async def test_search_failure_still_reports_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_a = _make_project(tmp_path / "a")
    project_b = _make_project(tmp_path / "b")

    def _broken_daemon(*_args: Any, **_kwargs: Any) -> IndexResponse:
        raise RuntimeError("daemon unavailable")

    monkeypatch.setattr(daemon_client, "index", _broken_daemon)

    server = create_mcp_server(str(project_a))
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("search", {"query": "q", "project_path": str(project_b)})

    assert result.structured_content is not None
    assert result.structured_content["success"] is False
    assert "daemon unavailable" in result.structured_content["message"]
    assert result.structured_content["project_root"] == str(project_b)


async def test_search_project_path_without_project_fails_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No project at project_path is an error, never the startup project's results."""
    project_a = _make_project(tmp_path / "a")
    bare = tmp_path / "bare"
    bare.mkdir()
    calls = _DaemonCalls(monkeypatch)

    server = create_mcp_server(str(project_a))
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("search", {"query": "q", "project_path": str(bare)})

    assert result.structured_content is not None
    assert result.structured_content["success"] is False
    message = result.structured_content["message"]
    assert str(bare) in message and "ccc init" in message
    assert calls.index_roots == [] and calls.search_roots == []


async def test_search_without_any_project_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server started outside a project needs project_path on every call."""
    calls = _DaemonCalls(monkeypatch)

    server = create_mcp_server()
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("search", {"query": "q"})

    assert result.structured_content is not None
    assert result.structured_content["success"] is False
    assert "project_path" in result.structured_content["message"]
    assert calls.index_roots == [] and calls.search_roots == []


@pytest.mark.parametrize("in_project", [True, False], ids=["in_project", "outside"])
def test_ccc_mcp_startup_project(
    in_project: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ccc mcp` hands the project around its cwd (or none) to the server, and only
    indexes in the background when there is one."""
    ccc_dir = tmp_path / "ccc_user"
    ccc_dir.mkdir()
    (ccc_dir / "global_settings.yml").write_text("embedding:\n  model: test\n  provider: litellm\n")
    project = _make_project(tmp_path / "project") if in_project else tmp_path / "bare"
    (project / "src").mkdir(parents=True)
    monkeypatch.chdir(project / "src")
    started_with: list[str | None] = []
    indexed: list[str] = []

    class _Server:
        async def run_stdio_async(self) -> None:
            await asyncio.sleep(0)  # let the background tasks start, as real I/O would

    def _create(default_project_root: str | None = None) -> _Server:
        started_with.append(default_project_root)
        return _Server()

    async def _no_heartbeat() -> None:
        return None

    async def _record_index(project_root: str) -> None:
        indexed.append(project_root)

    monkeypatch.setattr("cocoindex_code.server.create_mcp_server", _create)
    monkeypatch.setattr("cocoindex_code.server.run_heartbeat_loop", _no_heartbeat)
    monkeypatch.setattr(cli, "_bg_index", _record_index)

    cli.mcp()

    expected = str(project.resolve()) if in_project else None
    assert started_with == [expected]
    assert indexed == ([expected] if in_project else [])


async def test_ccc_mcp_starts_outside_a_project(tmp_path: Path) -> None:
    """`ccc mcp` launched in a non-project directory serves MCP instead of exiting."""
    ccc_dir = tmp_path / "ccc"
    ccc_dir.mkdir()
    (ccc_dir / "global_settings.yml").write_text("embedding:\n  model: test\n  provider: litellm\n")
    bare = tmp_path / "bare"
    bare.mkdir()
    assert find_project_root(bare) is None
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from cocoindex_code.cli import app; app()", "mcp"],
        cwd=bare,
        env={
            "COCOINDEX_CODE_DIR": str(ccc_dir),
            "COCOINDEX_CODE_RUNTIME_DIR": str(tmp_path / "runtime"),
            "HOME": str(tmp_path),
            "PATH": "",
        },
    )

    with anyio.fail_after(60):
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("search", {"query": "q"})

    assert [tool.name for tool in tools.tools] == ["search"]
    assert result.structured_content is not None
    assert result.structured_content["success"] is False
    assert "project_path" in result.structured_content["message"]
