"""OMP marketplace/plugin manifests stay valid and launch `ccc mcp`."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_omp_marketplace_catalog_points_at_repo_root() -> None:
    catalog = json.loads((REPO_ROOT / ".omp-plugin" / "marketplace.json").read_text())
    assert catalog["name"] == "cocoindex-code"
    assert catalog["owner"]["name"] == "CocoIndex"
    assert catalog["plugins"][0]["name"] == "cocoindex-code"
    assert catalog["plugins"][0]["source"] == "./"


def test_omp_plugin_manifest_replaces_mcp_with_ccc_mcp() -> None:
    manifest = json.loads((REPO_ROOT / ".omp-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "cocoindex-code"
    assert manifest["mcpServers"] == "./.mcp.json"
    mcp = json.loads((REPO_ROOT / ".mcp.json").read_text())
    assert mcp["mcpServers"]["cocoindex-code"] == {"command": "ccc", "args": ["mcp"]}
