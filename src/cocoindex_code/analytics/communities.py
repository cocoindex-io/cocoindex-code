"""Community detection on the declaration call graph.

Uses igraph + leidenalg when available; falls back to networkx greedy modularity.
Communities >25 nodes are recursively split.
Results are persisted to the ``communities`` table.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from ..declarations_db import db_connection

_SPLIT_THRESHOLD = 25


def _build_igraph(conn: sqlite3.Connection, repo_id: str | None) -> tuple[Any, list[int]]:
    """Return (igraph.Graph, node_ids) where position i → declaration id."""
    try:
        import igraph
    except ImportError as exc:
        raise ImportError("igraph required: uv add igraph") from exc

    clause = "WHERE repo_id = ?" if repo_id else ""
    params = [repo_id] if repo_id else []
    rows = conn.execute(
        f"""
        SELECT DISTINCT c.caller_decl_id, c.callee_decl_id
        FROM calls c
        JOIN declarations caller ON caller.id = c.caller_decl_id
        JOIN declarations callee ON callee.id = c.callee_decl_id
        {clause.replace("repo_id", "c.repo_id")}
          {"AND" if clause else "WHERE"} c.caller_decl_id IS NOT NULL
          AND c.callee_decl_id IS NOT NULL
        """,
        params,
    ).fetchall()

    all_ids: set[int] = set()
    edges_raw: list[tuple[int, int]] = []
    for r in rows:
        a, b = int(r[0]), int(r[1])
        all_ids.add(a)
        all_ids.add(b)
        edges_raw.append((a, b))

    node_list = sorted(all_ids)
    id_to_idx = {nid: i for i, nid in enumerate(node_list)}
    edge_list = [(id_to_idx[a], id_to_idx[b]) for a, b in edges_raw]

    g = igraph.Graph(n=len(node_list), edges=edge_list, directed=False)
    return g, node_list


def _detect_leiden(g: Any, node_list: list[int], level: int) -> list[list[int]]:
    try:
        import leidenalg
    except ImportError:
        return _detect_nx_fallback(g, node_list, level)

    partition = leidenalg.find_partition(g, leidenalg.ModularityVertexPartition)
    communities: list[list[int]] = []
    for part in partition:
        communities.append([node_list[i] for i in part])
    return communities


def _detect_nx_fallback(g: Any, node_list: list[int], _level: int) -> list[list[int]]:
    try:
        import networkx as nx
    except ImportError as exc:
        raise ImportError("networkx required for community fallback: uv add networkx") from exc

    nxg = nx.Graph()
    for i in range(g.vcount()):
        nxg.add_node(i)
    for edge in g.get_edgelist():
        nxg.add_edge(edge[0], edge[1])
    result = nx.algorithms.community.greedy_modularity_communities(nxg)
    return [[node_list[i] for i in part] for part in result]


def _decl_repo_ids(conn: sqlite3.Connection, decl_ids: list[int]) -> dict[int, str]:
    if not decl_ids:
        return {}
    placeholders = ",".join("?" * len(decl_ids))
    rows = conn.execute(
        f"SELECT id, repo_id FROM declarations WHERE id IN ({placeholders})",
        decl_ids,
    ).fetchall()
    return {int(row[0]): str(row[1]) for row in rows}


def _split_community(
    conn: sqlite3.Connection,
    repo_id: str | None,
    member_ids: list[int],
    level: int,
    community_id_counter: list[int],
) -> list[tuple[int, int, int]]:
    """Recursively split large communities into community rows."""
    if len(member_ids) <= _SPLIT_THRESHOLD or level >= 3:
        cid = community_id_counter[0]
        community_id_counter[0] += 1
        return [(cid, mid, level) for mid in member_ids]

    try:
        import igraph
    except ImportError:
        cid = community_id_counter[0]
        community_id_counter[0] += 1
        return [(cid, mid, level) for mid in member_ids]

    ph = ",".join("?" * len(member_ids))
    rows = conn.execute(
        f"""
        SELECT DISTINCT caller_decl_id, callee_decl_id
        FROM calls
        WHERE caller_decl_id IN ({ph})
          AND callee_decl_id IN ({ph})
          AND caller_decl_id IS NOT NULL
          AND callee_decl_id IS NOT NULL
        """,
        member_ids + member_ids,
    ).fetchall()

    id_to_idx = {nid: i for i, nid in enumerate(member_ids)}
    edge_list = [(id_to_idx[int(r[0])], id_to_idx[int(r[1])]) for r in rows]
    g = igraph.Graph(n=len(member_ids), edges=edge_list, directed=False)
    sub_communities = _detect_leiden(g, member_ids, level + 1)

    out: list[tuple[int, int, int]] = []
    for sub in sub_communities:
        out.extend(_split_community(conn, repo_id, sub, level + 1, community_id_counter))
    return out


def compute_communities(
    db_path: Path,
    repo_id: str | None = None,
) -> dict[str, Any]:
    """Detect communities and persist to ``communities`` table."""
    with db_connection(db_path) as conn:
        try:
            g, node_list = _build_igraph(conn, repo_id)
        except ImportError as exc:
            return {"success": False, "error": str(exc)}

        if not node_list:
            return {"success": True, "communities": 0, "skipped": "empty graph"}

        top_communities = _detect_leiden(g, node_list, 0)
        counter: list[int] = [0]
        now = time.time_ns()
        all_rows: list[tuple[int, int, int]] = []
        for members in top_communities:
            all_rows.extend(_split_community(conn, repo_id, members, 0, counter))

        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM communities WHERE decl_id NOT IN (SELECT id FROM declarations)")
        if repo_id is None:
            conn.execute("DELETE FROM communities")
        else:
            conn.execute("DELETE FROM communities WHERE repo_id = ?", (repo_id,))
        repo_ids = _decl_repo_ids(conn, [decl_id for _, decl_id, _ in all_rows])
        conn.executemany(
            """
            INSERT INTO communities (repo_id, community_id, decl_id, level, computed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (repo_id or repo_ids.get(decl_id, ""), cid, decl_id, level, now)
                for cid, decl_id, level in all_rows
            ],
        )
        conn.execute("COMMIT")

        return {
            "success": True,
            "nodes": len(node_list),
            "communities_top": len(top_communities),
            "communities_total": counter[0],
        }


def query_communities(
    conn: sqlite3.Connection,
    repo_id: str | None = None,
    *,
    level: int = 0,
    limit_per_community: int = 30,
) -> list[dict[str, Any]]:
    """Return community memberships grouped by community_id."""
    clause = "WHERE co.repo_id = ? AND co.level = ?" if repo_id else "WHERE co.level = ?"
    params: list[Any] = [repo_id, level] if repo_id else [level]
    rows = conn.execute(
        f"""
        SELECT co.community_id, co.decl_id, co.level,
               d.name, d.kind, d.file_path, d.exported
        FROM communities co
        JOIN declarations d ON d.id = co.decl_id
        {clause}
        ORDER BY co.community_id, d.name
        """,
        params,
    ).fetchall()

    by_community: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        cid = int(row["community_id"])
        if cid not in by_community:
            by_community[cid] = []
        if len(by_community[cid]) < limit_per_community:
            by_community[cid].append({str(k): row[k] for k in row.keys()})

    return [
        {"community_id": cid, "members": members, "size": len(members)}
        for cid, members in sorted(by_community.items())
    ]
