from pathlib import Path

import pytest
from mcp import Client

from cocoindex_code import client as daemon_client
from cocoindex_code._version import __version__
from cocoindex_code.cli import _bg_index
from cocoindex_code.protocol import SearchResponse
from cocoindex_code.server import create_mcp_server


async def test_mcp_server_uses_v2_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        daemon_client,
        "search",
        lambda **kwargs: SearchResponse(success=True, offset=kwargs["offset"]),
    )

    server = create_mcp_server(".")
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
    }


async def test_member_mcp_default_search_never_refreshes_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COCOINDEX_CODE_INDEXING_ROLE", "member")
    index_calls = 0

    def _unexpected_index(_project_root: str) -> None:
        nonlocal index_calls
        index_calls += 1

    monkeypatch.setattr(daemon_client, "index", _unexpected_index)
    monkeypatch.setattr(
        daemon_client,
        "search",
        lambda **kwargs: SearchResponse(success=True, offset=kwargs["offset"]),
    )

    await _bg_index(".")
    server = create_mcp_server(".")
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("search", {"query": "authentication"})
        explicit_refresh = await client.call_tool(
            "search", {"query": "authorization", "refresh_index": True}
        )

    assert result.structured_content["success"] is True
    assert explicit_refresh.structured_content["success"] is True
    assert index_calls == 0


async def test_mcp_server_reports_own_version() -> None:
    """The handshake advertises our version, not the SDK's or an empty string."""
    server = create_mcp_server(".")
    async with Client(server, raise_exceptions=True) as client:
        assert client.server_info is not None
        assert client.server_info.name == "cocoindex-code"
        assert client.server_info.version == __version__


def _repo_below_stale_home_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """cwd in a repo with no settings, below a $HOME an older ccc made a project."""
    home = tmp_path / "home"
    (home / ".cocoindex_code").mkdir(parents=True)
    (home / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []\n")
    repo = home / "projects" / "app"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("COCOINDEX_CODE_DIR", raising=False)
    monkeypatch.chdir(repo)
    return home


async def test_search_from_repo_below_stale_home_project_carries_the_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP clients hide the server's stderr, so the note travels in the result."""
    home = _repo_below_stale_home_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        daemon_client,
        "search",
        lambda **kwargs: SearchResponse(success=True, message="daemon message"),
    )

    async with Client(create_mcp_server(str(home)), raise_exceptions=True) as client:
        result = await client.call_tool("search", {"query": "auth", "refresh_index": False})

    assert result.structured_content is not None
    message = result.structured_content["message"]
    assert message.startswith("daemon message\nNote: ")
    assert f"{home / 'projects' / 'app'} has no ccc project of its own" in message
    assert "ccc reset --all" in message


async def test_failed_search_below_stale_home_project_still_carries_the_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Indexing a whole home is what times out; the failure must still explain why."""
    home = _repo_below_stale_home_project(tmp_path, monkeypatch)

    def _timeout(_root: str) -> None:
        raise TimeoutError("indexing took too long")

    monkeypatch.setattr(daemon_client, "index", _timeout)

    async with Client(create_mcp_server(str(home)), raise_exceptions=True) as client:
        result = await client.call_tool("search", {"query": "auth"})

    assert result.structured_content is not None
    message = result.structured_content["message"]
    assert result.structured_content["success"] is False
    assert message.startswith("Query failed: indexing took too long\nNote: ")
    assert "ccc reset --all" in message
