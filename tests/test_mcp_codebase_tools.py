"""Tests for the native codebase MCP tool surface."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from conftest import make_test_user_settings
from typer.testing import CliRunner

from cocoindex_code.cli import app
from cocoindex_code.declarations_db import init_db
from cocoindex_code.mcp_handlers import (
    codebase_context_index_tool,
    codebase_context_list_tool,
    codebase_context_remove_tool,
    codebase_context_search_tool,
    codebase_graph_visualize_tool,
)
from cocoindex_code.server import create_mcp_server
from cocoindex_code.settings import (
    default_project_settings,
    save_project_settings,
    save_user_settings,
)
from cocoindex_code.workflows import codebase_workflow_tool

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
        "codebase_workflow",
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
    assert "auto-discovered" in result["message"]


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


def test_codebase_context_auto_discovers_common_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "intro.md").write_text("Architecture notes\n", encoding="utf-8")

    result = codebase_context_list_tool(tmp_path)

    assert result["success"] is True
    artifact_names = {artifact["name"] for artifact in result["artifacts"]}
    assert {"readme", "docs"} <= artifact_names


def test_codebase_graph_visualize_html_returns_document(tmp_path: Path) -> None:
    db_path = tmp_path / "declarations.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO declarations
            (id, repo_id, file_path, kind, name, signature, start_line, end_line, exported, source)
            VALUES
            (1, 'local', 'src/a.py', 'function', 'a', NULL, 1, 2, 1, 'native'),
            (2, 'local', 'src/b.py', 'function', 'b', NULL, 1, 2, 1, 'native')
            """
        )
        conn.execute(
            """
            INSERT INTO calls
            (repo_id, file_path, caller_decl_id, callee_decl_id, line, callee_name, source)
            VALUES ('local', 'src/a.py', 1, 2, 1, 'b', 'native')
            """
        )
        conn.commit()

    result = codebase_graph_visualize_tool(db_path, format="html")

    assert result["success"] is True
    assert result["mode"] == "html"
    assert "<!doctype html>" in result["html"]
    assert "src/a.py" in result["html"]


def test_install_command_prints_generic_snippet() -> None:
    result = runner.invoke(app, ["install", "--host", "generic"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["host"] == "generic"
    assert payload["snippet"]["mcpServers"]["cocoindex-code"]["command"] == "ccc"
    assert any("cgrep" in step for step in payload["next_steps"])


def test_install_command_rejects_unknown_host() -> None:
    result = runner.invoke(app, ["install", "--host", "cursor"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert "unsupported host" in payload["error"]
    assert "generic" in payload["supported_hosts"]


def test_graph_visualize_cli_writes_output_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    monkeypatch.setenv("COCOINDEX_CODE_DIR", str(tmp_path / "user"))
    save_user_settings(make_test_user_settings())
    save_project_settings(project, default_project_settings())
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    db_path = project / ".cocoindex_code" / "declarations.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO declarations
            (id, repo_id, file_path, kind, name, signature, start_line, end_line, exported, source)
            VALUES
            (1, 'local', 'src/a.py', 'function', 'a', NULL, 1, 2, 1, 'native'),
            (2, 'local', 'src/b.py', 'function', 'b', NULL, 1, 2, 1, 'native')
            """
        )
        conn.execute(
            """
            INSERT INTO calls
            (repo_id, file_path, caller_decl_id, callee_decl_id, line, callee_name, source)
            VALUES ('local', 'src/a.py', 1, 2, 1, 'b', 'native')
            """
        )
        conn.commit()

    output = project / "artifacts" / "graph.html"
    monkeypatch.chdir(project)
    result = runner.invoke(
        app,
        ["codebase", "graph", "visualize", "--format", "html", "--output", str(output)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert output.is_file()
    assert "<!doctype html>" in output.read_text(encoding="utf-8")


def test_context_search_ranks_best_match_first(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("incremental updates and architecture\n", encoding="utf-8")
    (docs / "b.md").write_text("incremental only\n", encoding="utf-8")
    (tmp_path / "coco-context.yml").write_text(
        "artifacts:\n  - name: docs\n    path: docs\n",
        encoding="utf-8",
    )

    result = codebase_context_search_tool(tmp_path, "incremental architecture", artifact="docs")

    assert result["success"] is True
    assert result["results"][0]["file_path"] == "docs/a.md"
    assert result["results"][0]["score"] > result["results"][1]["score"]


def test_workflow_debug_accepts_target_without_query(tmp_path: Path) -> None:
    db_path = tmp_path / "declarations.db"
    init_db(db_path)
    result = codebase_workflow_tool(
        tmp_path,
        db_path,
        workflow="debug",
        target="save_user",
        limit=5,
    )

    assert result["workflow"] == "debug"
    assert "search" in result


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
        "workflow",
        "graph",
        "context",
    ):
        assert command in result.output
