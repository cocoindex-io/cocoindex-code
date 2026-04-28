"""Tests for the native codebase MCP tool surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cocoindex_code.cli import app
from cocoindex_code.mcp_handlers import (
    codebase_context_index_tool,
    codebase_context_list_tool,
    codebase_context_remove_tool,
    codebase_context_search_tool,
)
from cocoindex_code.server import create_mcp_server

runner = CliRunner()


@pytest.mark.asyncio
async def test_mcp_exposes_local_codebase_tool_names(tmp_path: Path) -> None:
    mcp = create_mcp_server(str(tmp_path))

    tool_names = {tool.name for tool in await mcp.list_tools()}

    assert {
        "codebase_index",
        "codebase_update",
        "codebase_search",
        "codebase_status",
        "codebase_graph_query",
        "codebase_graph_stats",
        "codebase_impact",
        "codebase_flow",
        "codebase_symbol",
        "codebase_symbols",
        "codebase_context",
        "codebase_context_search",
        "codebase_health",
    } <= tool_names
    assert {
        "search",
        "hybrid_search",
        "ripgrep_bounded",
        "get_impact_radius",
        "detect_flows",
        "sync_configured_repos",
    }.isdisjoint(tool_names)


def test_codebase_context_uses_local_config_name(tmp_path: Path) -> None:
    result = codebase_context_list_tool(tmp_path)

    assert result["success"] is True
    assert result["config"] == str(tmp_path / "coco-context.yml")
    assert result["artifacts"] == []
    assert "coco-context.yml" in result["message"]


def test_codebase_context_search_reads_configured_artifact(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text(
        "The indexer maintains incremental updates for changed files.\n", encoding="utf-8"
    )
    (tmp_path / "coco-context.yml").write_text(
        "artifacts:\n  - name: architecture\n    path: docs\n",
        encoding="utf-8",
    )

    result = codebase_context_search_tool(tmp_path, "incremental updates", artifact="architecture")

    assert result["success"] is True
    assert result["results"] == [
        {
            "artifact": "architecture",
            "file_path": "docs/architecture.md",
            "line": 1,
            "content": "The indexer maintains incremental updates for changed files.",
            "score": 2.0,
        }
    ]


def test_codebase_context_accepts_mapping_artifacts(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text(
        "The app exposes helper and main as a tiny sample workflow.\n", encoding="utf-8"
    )
    (tmp_path / "coco-context.yml").write_text(
        "artifacts:\n"
        "  architecture:\n"
        "    path: docs/architecture.md\n"
        "    type: markdown\n",
        encoding="utf-8",
    )

    listed = codebase_context_list_tool(tmp_path)
    indexed = codebase_context_index_tool(tmp_path)
    result = codebase_context_search_tool(tmp_path, "helper", artifact="architecture")

    assert listed["success"] is True
    assert listed["artifacts"][0]["name"] == "architecture"
    assert indexed["success"] is True
    assert indexed["indexed"][0]["file_count"] == 1
    assert result["success"] is True
    assert result["results"][0]["artifact"] == "architecture"
    assert result["results"][0]["file_path"] == "docs/architecture.md"


def test_codebase_context_index_and_remove_use_local_manifest(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "schema.sql").write_text("create table users(id int);\n", encoding="utf-8")
    (tmp_path / "coco-context.yml").write_text(
        "artifacts:\n  - name: database\n    path: docs/schema.sql\n    description: Schema docs\n",
        encoding="utf-8",
    )

    indexed = codebase_context_index_tool(tmp_path)

    manifest = tmp_path / ".cocoindex_code" / "context_artifacts.json"
    assert indexed["success"] is True
    assert indexed["manifest"] == str(manifest)
    assert manifest.is_file()
    listed = codebase_context_list_tool(tmp_path)
    assert listed["artifacts"][0]["index"]["file_count"] == 1

    removed = codebase_context_remove_tool(tmp_path)

    assert removed["success"] is True
    assert removed["removed"] == 1
    assert not manifest.exists()


def test_cli_exposes_codebase_namespace() -> None:
    result = runner.invoke(app, ["codebase", "--help"])

    assert result.exit_code == 0
    assert "Codebase indexing and intelligence tools." in result.output
    for command in (
        "index",
        "update",
        "search",
        "status",
        "impact",
        "flow",
        "symbol",
        "symbols",
        "graph",
        "context",
    ):
        assert command in result.output
