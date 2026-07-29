import pytest
from mcp import Client

from cocoindex_code import client as daemon_client
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
