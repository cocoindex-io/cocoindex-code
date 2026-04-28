"""Knowledge gap detection: untested hubs, isolated nodes, thin communities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..declarations_db import db_connection


def get_knowledge_gaps(
    db_path: Path,
    repo_id: str | None = None,
    *,
    hub_min_in_degree: int = 3,
    thin_community_max_size: int = 2,
    limit: int = 30,
) -> dict[str, Any]:
    """Surface knowledge gaps across three dimensions.

    Returns:
      untested_hubs       – High in-degree declarations with no test edge.
      isolated_nodes      – Declarations with no callers and no callees.
      thin_communities    – Communities with very few members.
    """
    with db_connection(db_path) as conn:
        repo_clause = "AND d.repo_id = ?" if repo_id else ""
        repo_params: list[Any] = [repo_id] if repo_id else []

        # Untested hubs
        hub_params: list[Any] = [hub_min_in_degree] + repo_params + [limit]
        hub_rows = conn.execute(
            f"""
            SELECT d.id AS decl_id, d.name, d.kind, d.file_path, d.exported,
                   c.in_degree, c.betweenness
            FROM centrality c
            JOIN declarations d ON d.id = c.decl_id
            WHERE c.in_degree >= ?
              {repo_clause}
              AND NOT EXISTS (
                SELECT 1 FROM tests t WHERE t.tested_decl_id = d.id
              )
            ORDER BY c.in_degree DESC, c.betweenness DESC
            LIMIT ?
            """,
            hub_params,
        ).fetchall()
        untested_hubs = [{str(k): row[k] for k in row.keys()} for row in hub_rows]

        # Isolated nodes (no caller edges, no callee edges)
        iso_params: list[Any] = repo_params + [limit]
        iso_rows = conn.execute(
            f"""
            SELECT d.id AS decl_id, d.name, d.kind, d.file_path, d.exported
            FROM declarations d
            WHERE 1=1 {repo_clause}
              AND NOT EXISTS (SELECT 1 FROM calls c WHERE c.callee_decl_id = d.id)
              AND NOT EXISTS (SELECT 1 FROM calls c WHERE c.caller_decl_id = d.id)
              AND NOT EXISTS (SELECT 1 FROM inherits i
                               WHERE i.subclass_decl_id = d.id OR i.superclass_decl_id = d.id)
            ORDER BY d.file_path, d.start_line
            LIMIT ?
            """,
            iso_params,
        ).fetchall()
        isolated_nodes = [{str(k): row[k] for k in row.keys()} for row in iso_rows]

        # Thin communities
        thin_params: list[Any] = [repo_id or "", thin_community_max_size, limit]
        thin_rows = conn.execute(
            """
            SELECT community_id, COUNT(*) AS size
            FROM communities
            WHERE repo_id = ?
              AND level = 0
            GROUP BY community_id
            HAVING size <= ?
            ORDER BY size ASC
            LIMIT ?
            """,
            thin_params,
        ).fetchall()
        thin_communities = [{"community_id": int(r[0]), "size": int(r[1])} for r in thin_rows]

    return {
        "success": True,
        "untested_hubs": untested_hubs,
        "isolated_nodes": isolated_nodes,
        "thin_communities": thin_communities,
        "summary": {
            "untested_hub_count": len(untested_hubs),
            "isolated_node_count": len(isolated_nodes),
            "thin_community_count": len(thin_communities),
        },
    }
