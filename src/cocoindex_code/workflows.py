"""High-level workflow wrappers built from existing codebase primitives."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .analytics.centrality import compute_centrality
from .analytics.communities import compute_communities
from .mcp_handlers import (
    codebase_context_list_tool,
    codebase_flow_tool,
    codebase_graph_stats_tool,
    codebase_graph_visualize_tool,
    codebase_symbols_tool,
    detect_changes_tool,
    get_architecture_overview_tool,
    hybrid_search_tool,
)

SUPPORTED_WORKFLOWS = ("review", "debug", "onboard", "architecture")


def codebase_workflow_tool(
    project_root: Path,
    db_path: Path,
    *,
    workflow: str,
    query: str | None = None,
    target: str | None = None,
    ref_spec: str = "HEAD",
    path_prefix: str | None = None,
    top_n: int = 20,
    limit: int = 10,
    diagram_format: str = "mermaid",
) -> dict[str, Any]:
    """Execute a named workflow by composing lower-level codebase tools."""
    workflow_name = workflow.strip().lower()
    if workflow_name not in SUPPORTED_WORKFLOWS:
        return {
            "success": False,
            "workflow": workflow_name,
            "error": (
                "workflow must be one of: " + ", ".join(SUPPORTED_WORKFLOWS)
            ),
        }

    if workflow_name == "review":
        review = detect_changes_tool(
            db_path,
            project_root,
            "local",
            ref_spec,
            path_prefix=path_prefix,
            top_n=top_n,
        )
        return {
            "success": bool(review.get("success")),
            "workflow": "review",
            "summary": "Ranked review context for changed declarations.",
            "review": review,
            "next_steps": [
                "Start with the highest risk_score declarations.",
                (
                    "Inspect uncovered hubs first because they combine centrality "
                    "and lower test coverage."
                ),
                (
                    "Use `ccc codebase symbol <name>` or `ccc codebase impact "
                    "<symbol>` for deeper follow-up."
                ),
            ],
        }

    if workflow_name == "debug":
        effective_query = query or target
        if not effective_query:
            return {
                "success": False,
                "workflow": "debug",
                "error": "debug workflow requires --query or --target",
            }
        search = hybrid_search_tool(
            str(project_root), effective_query, limit=limit, refresh_index=False
        )
        symbols = codebase_symbols_tool(db_path, query=effective_query, limit=limit)
        flow = None
        if target:
            flow = codebase_flow_tool(db_path, entrypoint=target, depth=5, limit=limit)
        return {
            "success": bool(search.get("success")) and bool(symbols.get("success")),
            "workflow": "debug",
            "summary": "Hybrid search plus symbol hints for narrowing likely fault points.",
            "search": search,
            "symbols": symbols,
            "flow": flow,
            "next_steps": [
                "Validate whether the top hybrid hits line up with the reported symptom.",
                "If a likely entrypoint is known, rerun with `--target` to trace the call flow.",
                (
                    "Use `ccc codebase graph query <file>` on the strongest "
                    "candidate file to inspect imports and dependents."
                ),
            ],
        }

    if workflow_name == "onboard":
        graph = codebase_graph_stats_tool(db_path)
        entrypoints = codebase_flow_tool(db_path, entrypoint=None, depth=5, limit=limit)
        context = codebase_context_list_tool(project_root)
        return {
            "success": bool(graph.get("success")) and bool(entrypoints.get("success")),
            "workflow": "onboard",
            "summary": "Fast orientation pack for a new engineer or agent session.",
            "graph": graph,
            "entrypoints": entrypoints,
            "context": context,
            "next_steps": [
                "Read the configured or auto-discovered context artifacts first.",
                "Start with the highest out-degree entrypoints to understand request flow.",
                "Use `ccc codebase graph visualize --format html` for a browsable dependency view.",
            ],
        }

    if workflow_name == "architecture":
        architecture = get_architecture_overview_tool(db_path)
        centrality = compute_centrality(db_path)
        communities = compute_communities(db_path)
        visualize = codebase_graph_visualize_tool(
            db_path,
            limit=max(limit * 12, 60),
            format=diagram_format,
        )
        return {
            "success": (
                bool(architecture.get("success"))
                and bool(centrality.get("success"))
                and bool(visualize.get("success"))
            ),
            "workflow": "architecture",
            "summary": (
                "Architecture overview with hubs, bridges, communities, "
                "and a rendered graph."
            ),
            "architecture": architecture,
            "centrality": centrality,
            "communities": communities,
            "visualization": visualize,
            "next_steps": [
                "Audit bridge nodes first; they usually mark hidden coupling across subsystems.",
                (
                    "Review the largest communities for boundaries that should "
                    "become explicit modules."
                ),
                "Persist the HTML graph if you want a shareable architecture snapshot.",
            ],
        }

    raise AssertionError(f"unreachable workflow branch: {workflow_name}")
