from pathlib import Path

import pytest
from mcp import Client

from cocoindex_code import client as daemon_client
from cocoindex_code._version import __version__
from cocoindex_code.protocol import ProjectStatusResponse, SearchResponse
from cocoindex_code.server import create_mcp_server
from cocoindex_code.settings import default_project_settings, save_project_settings


async def test_mcp_server_uses_v2_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        daemon_client,
        "search",
        lambda **kwargs: SearchResponse(success=True, offset=kwargs["offset"]),
    )
    monkeypatch.setattr(
        daemon_client,
        "project_status",
        lambda _root: ProjectStatusResponse(
            indexing=False,
            total_chunks=0,
            total_files=0,
            languages={},
            stale=False,
            indexed_at_us=1_700_000_000_000_000,
        ),
    )

    server = create_mcp_server(".")
    async with Client(server, raise_exceptions=True) as client:
        tools = await client.list_tools()
        result = await client.call_tool(
            "search",
            {"query": "authentication", "refresh_index": False},
        )

    assert [tool.name for tool in tools.tools] == [
        "search",
        "grep",
        "status",
        "codebase_overview",
    ]
    assert result.structured_content == {
        "success": True,
        "results": [],
        "total_returned": 0,
        "offset": 0,
        "freshness": {"stale": False, "indexed_at": "2023-11-14T22:13:20+00:00"},
        "message": None,
    }


async def test_mcp_grep_searches_project_files_without_daemon(tmp_path: Path) -> None:
    save_project_settings(tmp_path, default_project_settings())
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "example.py").write_text("def greet(name):\n    return name\n")

    async with Client(create_mcp_server(str(tmp_path)), raise_exceptions=True) as client:
        result = await client.call_tool("grep", {"pattern": r"def \NAME(\(ARGS*\)):"})

    assert result.structured_content == {
        "success": True,
        "matches": [
            {
                "file_path": "src/example.py",
                "language": "python",
                "content": "def greet(name):",
                "start_line": 1,
                "end_line": 1,
            }
        ],
        "total_matches": 1,
        "warnings": [],
        "truncated": False,
        "message": None,
    }


async def test_mcp_grep_rejects_a_path_outside_the_project(tmp_path: Path) -> None:
    save_project_settings(tmp_path, default_project_settings())

    async with Client(create_mcp_server(str(tmp_path)), raise_exceptions=True) as client:
        result = await client.call_tool("grep", {"pattern": "x", "path": ".."})

    assert result.structured_content is not None
    assert result.structured_content["success"] is False
    assert "within the project root" in result.structured_content["message"]


async def test_mcp_status_and_overview_report_freshness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_project_settings(tmp_path, default_project_settings())
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.py").write_text("def endpoint():\n    pass\n")
    (tmp_path / "README.md").write_text("# Example\n")
    monkeypatch.setattr(
        daemon_client,
        "project_status",
        lambda _root: ProjectStatusResponse(
            indexing=False,
            index_exists=True,
            total_chunks=12,
            total_files=2,
            languages={"python": 9, "markdown": 3},
            stale=True,
            indexed_at_us=1_700_000_000_000_000,
        ),
    )

    async with Client(create_mcp_server(str(tmp_path)), raise_exceptions=True) as client:
        status = await client.call_tool("status", {})
        overview = await client.call_tool("codebase_overview", {"max_depth": 2})

    assert status.structured_content == {
        "indexing": False,
        "index_exists": True,
        "total_chunks": 12,
        "total_files": 2,
        "languages": {"python": 9, "markdown": 3},
        "freshness": {"stale": True, "indexed_at": "2023-11-14T22:13:20+00:00"},
        "message": None,
    }
    assert overview.structured_content is not None
    assert overview.structured_content["success"] is True
    assert overview.structured_content["total_source_files"] == 2
    assert overview.structured_content["languages"] == {"markdown": 1, "python": 1}
    assert overview.structured_content["top_level_directories"] == [{"path": "src", "files": 1}]
    assert overview.structured_content["tree"] == ["README.md", "src/", "src/api.py"]
    assert overview.structured_content["freshness"]["stale"] is True


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
