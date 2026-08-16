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
