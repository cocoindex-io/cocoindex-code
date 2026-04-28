"""Betweenness centrality computation for the declaration call graph.

Persists results to the ``centrality`` table. networkx is required
(``uv add networkx`` or install cocoindex-code[analytics]).

Approximate betweenness (k-sampling) is used for large graphs to stay fast.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from ..declarations_db import db_connection

_K_APPROX_THRESHOLD = 500
_K_SAMPLE_DEFAULT = 100


def _build_digraph(conn: sqlite3.Connection, repo_id: str | None) -> Any:
    try:
        import networkx as nx
    except ImportError as exc:
        raise ImportError("networkx is required for centrality: uv add networkx") from exc

    graph: Any = nx.DiGraph()
    clause = "WHERE repo_id = ?" if repo_id else ""
    params = [repo_id] if repo_id else []

    rows = conn.execute(
        f"""
        SELECT c.caller_decl_id, c.callee_decl_id
        FROM calls c
        JOIN declarations caller ON caller.id = c.caller_decl_id
        JOIN declarations callee ON callee.id = c.callee_decl_id
        {clause.replace("repo_id", "c.repo_id")}
          {"AND" if clause else "WHERE"} c.caller_decl_id IS NOT NULL
          AND c.callee_decl_id IS NOT NULL
        """,
        params,
    ).fetchall()
    for r in rows:
        graph.add_edge(int(r[0]), int(r[1]))
    return graph


def _node_repo_ids(conn: sqlite3.Connection, node_ids: list[int]) -> dict[int, str]:
    if not node_ids:
        return {}
    placeholders = ",".join("?" * len(node_ids))
    rows = conn.execute(
        f"SELECT id, repo_id FROM declarations WHERE id IN ({placeholders})",
        node_ids,
    ).fetchall()
    return {int(row[0]): str(row[1]) for row in rows}


def compute_centrality(
    db_path: Path,
    repo_id: str | None = None,
    *,
    k: int | None = None,
) -> dict[str, Any]:
    """Compute and persist betweenness centrality.

    For graphs with more than ``_K_APPROX_THRESHOLD`` nodes the approximation
    mode (sampled BFS) is used automatically unless ``k`` is specified.
    """
    try:
        import networkx as nx
    except ImportError as exc:
        return {"success": False, "error": f"networkx required: {exc}"}

    with db_connection(db_path) as conn:
        graph = _build_digraph(conn, repo_id)
        n = graph.number_of_nodes()
        if n == 0:
            return {"success": True, "nodes": 0, "skipped": "empty graph"}

        effective_k = (
            k if k is not None else (_K_SAMPLE_DEFAULT if n > _K_APPROX_THRESHOLD else None)
        )
        if effective_k is not None:
            bw: dict[int, float] = nx.betweenness_centrality(graph, k=effective_k, normalized=True)
        else:
            bw = nx.betweenness_centrality(graph, normalized=True)

        in_deg: dict[int, int] = dict(graph.in_degree())
        out_deg: dict[int, int] = dict(graph.out_degree())
        repo_ids = _node_repo_ids(conn, [int(node_id) for node_id in bw])
        now = time.time_ns()

        batch = []
        for node_id, score in bw.items():
            rid = repo_id or repo_ids.get(int(node_id), "")
            batch.append(
                (
                    rid,
                    int(node_id),
                    float(score),
                    in_deg.get(node_id, 0),
                    out_deg.get(node_id, 0),
                    now,
                )
            )

        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM centrality WHERE decl_id NOT IN (SELECT id FROM declarations)")
        if repo_id is None:
            conn.execute("DELETE FROM centrality")
        else:
            conn.execute("DELETE FROM centrality WHERE repo_id = ?", (repo_id,))
        conn.executemany(
            """
            INSERT INTO centrality
                (repo_id, decl_id, betweenness, in_degree, out_degree, computed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        conn.execute("COMMIT")
        return {
            "success": True,
            "nodes": n,
            "edges": graph.number_of_edges(),
            "approximated": effective_k is not None,
            "k": effective_k,
        }


def query_hub_nodes(
    conn: sqlite3.Connection,
    repo_id: str | None = None,
    *,
    limit: int = 20,
    min_in_degree: int = 2,
) -> list[dict[str, Any]]:
    """Return declarations with highest betweenness (hub nodes)."""
    clause = "WHERE c.repo_id = ?" if repo_id else ""
    params: list[Any] = [repo_id] if repo_id else []
    params.extend([min_in_degree, limit])
    rows = conn.execute(
        f"""
        SELECT c.decl_id, c.betweenness, c.in_degree, c.out_degree,
               d.name, d.kind, d.file_path, d.exported
        FROM centrality c
        JOIN declarations d ON d.id = c.decl_id
        {clause}
          {"AND" if clause else "WHERE"} c.in_degree >= ?
        ORDER BY c.betweenness DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [{str(k): row[k] for k in row.keys()} for row in rows]


def query_bridge_nodes(
    conn: sqlite3.Connection,
    repo_id: str | None = None,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """High betweenness AND connects clusters: high-bet + moderate in+out degree."""
    clause = "WHERE c.repo_id = ?" if repo_id else ""
    params: list[Any] = [repo_id] if repo_id else []
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT c.decl_id, c.betweenness, c.in_degree, c.out_degree,
               d.name, d.kind, d.file_path, d.exported
        FROM centrality c
        JOIN declarations d ON d.id = c.decl_id
        {clause}
        ORDER BY c.betweenness DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [{str(k): row[k] for k in row.keys()} for row in rows]
