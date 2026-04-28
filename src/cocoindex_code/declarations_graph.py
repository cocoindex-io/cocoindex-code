"""Materialized call/inherit edges and lightweight job status for declarations DB."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from typing import Any

from .declarations_db import InheritEdge, _escape_like, _normalize_path


def _innermost_decl_id_at_line(decl_rows: list[sqlite3.Row], line: int) -> int | None:
    best_id: int | None = None
    best_start = -1
    for d in decl_rows:
        if int(d["start_line"]) <= line <= int(d["end_line"]):
            sl = int(d["start_line"])
            if sl > best_start:
                best_start = sl
                best_id = int(d["id"])
    return best_id


def resolve_callee_decl_id(
    conn: sqlite3.Connection, repo_id: str, caller_file: str, callee_name: str
) -> int | None:
    """Resolve a call-site name to a declaration id.

    Resolution order:
      1. Same-repo, same-file (most specific).
      2. Same-repo, any-file (handles intra-repo cross-module calls).
      3. Cross-repo, ``exported = 1`` only — restricted to public surfaces so
         we do not invent edges to private internals of other repos. Polyrepo
         shared type packages need this hop or ``get_impact_radius``
         cannot traverse repo boundaries.

    The cross-repo fallback prefers shorter file paths to bias toward
    package re-export points (e.g. ``shared/packages/types/src/index.ts``)
    over deeply nested implementation files.
    """
    if not callee_name:
        return None
    tail = callee_name.split(".")[-1]
    path = _normalize_path(caller_file)
    row = conn.execute(
        "SELECT id FROM declarations WHERE repo_id = ? AND file_path = ? AND name = ? LIMIT 1",
        (repo_id, path, tail),
    ).fetchone()
    if row:
        return int(row[0])
    row = conn.execute(
        """
        SELECT id FROM declarations
        WHERE repo_id = ? AND name = ?
        ORDER BY LENGTH(file_path) ASC, file_path ASC
        LIMIT 1
        """,
        (repo_id, tail),
    ).fetchone()
    if row:
        return int(row[0])
    # Cross-repo fallback: only consider exported declarations to avoid
    # spurious edges into private implementations of sibling repos.
    # Ambiguity guard: pull top-2 candidates; if the two shortest paths
    # have the same length the symbol name is too generic (e.g. ``init``,
    # ``run``, ``process``) and we'd just be guessing — return None.
    rows = conn.execute(
        """
        SELECT id, file_path FROM declarations
        WHERE repo_id != ? AND name = ? AND exported = 1
        ORDER BY LENGTH(file_path) ASC, file_path ASC
        LIMIT 2
        """,
        (repo_id, tail),
    ).fetchall()
    if not rows:
        return None
    if len(rows) == 2 and len(rows[0][1]) == len(rows[1][1]):
        return None
    return int(rows[0][0])


def resolve_named_decl_in_file(
    conn: sqlite3.Connection,
    repo_id: str,
    file_path: str,
    name: str,
    kinds: tuple[str, ...],
) -> int | None:
    path = _normalize_path(file_path)
    placeholders = ",".join("?" * len(kinds))
    row = conn.execute(
        f"""
        SELECT id FROM declarations
        WHERE repo_id = ? AND file_path = ? AND name = ?
          AND kind IN ({placeholders})
        LIMIT 1
        """,
        (repo_id, path, name, *kinds),
    ).fetchone()
    return int(row[0]) if row else None


def resolve_superclass_decl_id(
    conn: sqlite3.Connection, repo_id: str, superclass_name: str
) -> int | None:
    if not superclass_name:
        return None
    base = superclass_name.split(".")[-1]
    row = conn.execute(
        """
        SELECT id FROM declarations
        WHERE repo_id = ? AND name = ? AND kind IN ('class', 'interface', 'type')
        ORDER BY
          CASE kind WHEN 'class' THEN 0 WHEN 'interface' THEN 1 ELSE 2 END,
          LENGTH(file_path) ASC
        LIMIT 1
        """,
        (repo_id, base),
    ).fetchone()
    return int(row[0]) if row else None


def rebuild_calls_for_file(conn: sqlite3.Connection, repo_id: str, file_path: str) -> int:
    path = _normalize_path(file_path)
    conn.execute("DELETE FROM calls WHERE repo_id = ? AND file_path = ?", (repo_id, path))
    decl_rows = conn.execute(
        """
        SELECT id, name, kind, start_line, end_line, parent_name
        FROM declarations
        WHERE repo_id = ? AND file_path = ?
        ORDER BY start_line ASC
        """,
        (repo_id, path),
    ).fetchall()
    ref_rows = conn.execute(
        """
        SELECT start_line, callee_name,
               COALESCE(source, 'treesitter') AS ref_source
        FROM "references"
        WHERE repo_id = ? AND file_path = ?
        """,
        (repo_id, path),
    ).fetchall()
    batch: list[tuple[str, str, int | None, int | None, int, str, str, float | None]] = []
    for ref in ref_rows:
        line = int(ref["start_line"])
        callee_name = str(ref["callee_name"])
        ref_src = str(ref["ref_source"]) if ref["ref_source"] is not None else "treesitter"
        caller_id = _innermost_decl_id_at_line(decl_rows, line)
        callee_id = resolve_callee_decl_id(conn, repo_id, path, callee_name)
        batch.append((repo_id, path, caller_id, callee_id, line, callee_name, ref_src, None))
    if batch:
        conn.executemany(
            """
            INSERT INTO calls
                (
                    repo_id, file_path, caller_decl_id, callee_decl_id,
                    line, callee_name, source, confidence
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
    return len(batch)


def rebuild_inherits_for_file(
    conn: sqlite3.Connection, repo_id: str, file_path: str, edges: Iterable[InheritEdge]
) -> int:
    path = _normalize_path(file_path)
    conn.execute("DELETE FROM inherits WHERE repo_id = ? AND file_path = ?", (repo_id, path))
    rows: list[tuple[str, str, int, int, int]] = []
    for e in edges:
        sub_id = resolve_named_decl_in_file(conn, repo_id, path, e.subclass_name, ("class",))
        if sub_id is None:
            continue
        sup_id = resolve_superclass_decl_id(conn, repo_id, e.superclass_name)
        if sup_id is None:
            continue
        rows.append((repo_id, path, sub_id, sup_id, e.line))
    if rows:
        conn.executemany(
            """
            INSERT INTO inherits (repo_id, file_path, subclass_decl_id, superclass_decl_id, line)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def job_start(conn: sqlite3.Connection, job_id: str, total: int) -> None:
    now = time.time_ns()
    conn.execute(
        """
        INSERT INTO jobs (id, started_at, updated_at, progress, total, status, last_error)
        VALUES (?, ?, ?, 0, ?, 'running', NULL)
        ON CONFLICT(id) DO UPDATE SET
          updated_at = excluded.updated_at,
          progress = 0,
          total = excluded.total,
          status = 'running',
          last_error = NULL
        """,
        (job_id, now, now, total),
    )


def job_touch(
    conn: sqlite3.Connection, job_id: str, progress: int, total: int | None = None
) -> None:
    now = time.time_ns()
    if total is not None:
        conn.execute(
            "UPDATE jobs SET updated_at = ?, progress = ?, total = ? WHERE id = ?",
            (now, progress, total, job_id),
        )
    else:
        conn.execute(
            "UPDATE jobs SET updated_at = ?, progress = ? WHERE id = ?",
            (now, progress, job_id),
        )


def job_finish(
    conn: sqlite3.Connection, job_id: str, status: str, last_error: str | None = None
) -> None:
    now = time.time_ns()
    conn.execute(
        "UPDATE jobs SET updated_at = ?, status = ?, last_error = ? WHERE id = ?",
        (now, status, last_error, job_id),
    )


def query_latest_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs ORDER BY updated_at DESC LIMIT 1").fetchone()


def query_job_by_id(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def query_find_callers_transitive(
    conn: sqlite3.Connection,
    callee_decl_id: int,
    *,
    depth: int = 3,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Transitive callers up to ``depth`` hops (hop 1 = direct ``calls`` rows)."""
    if depth < 1 or limit < 1:
        return []
    out: list[dict[str, Any]] = []
    seen_call_ids: set[int] = set()
    frontier: set[int] = {callee_decl_id}
    hop = 0
    while hop < depth and frontier and len(out) < limit:
        hop += 1
        ph = ",".join("?" * len(frontier))
        sql = f"""
        SELECT
          c.id AS call_id,
          c.repo_id,
          c.file_path,
          c.line,
          c.callee_name,
          c.caller_decl_id,
          d.name AS caller_name,
          d.kind AS caller_kind,
          d.file_path AS caller_file_path,
          d.start_line AS caller_decl_line
        FROM calls c
        LEFT JOIN declarations d ON d.id = c.caller_decl_id
        WHERE c.callee_decl_id IN ({ph})
        ORDER BY c.file_path, c.line
        LIMIT ?
        """
        cap = max(0, limit - len(out))
        rows = conn.execute(sql, (*frontier, cap)).fetchall()
        if not rows:
            break
        next_frontier: set[int] = set()
        for r in rows:
            cid = int(r["call_id"])
            if cid in seen_call_ids:
                continue
            seen_call_ids.add(cid)
            rowd = {str(k): r[k] for k in r.keys()}
            rowd["transitive_hop"] = hop
            out.append(rowd)
            cd = r["caller_decl_id"]
            if cd is not None:
                next_frontier.add(int(cd))
        frontier = next_frontier
    return out[:limit]


def query_find_callers(
    conn: sqlite3.Connection, callee_decl_id: int, limit: int = 50
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
          c.id AS call_id,
          c.repo_id,
          c.file_path,
          c.line,
          c.callee_name,
          c.caller_decl_id,
          d.name AS caller_name,
          d.kind AS caller_kind,
          d.file_path AS caller_file_path,
          d.start_line AS caller_decl_line
        FROM calls c
        LEFT JOIN declarations d ON d.id = c.caller_decl_id
        WHERE c.callee_decl_id = ?
        ORDER BY c.file_path, c.line
        LIMIT ?
        """,
        (callee_decl_id, limit),
    ).fetchall()


def query_callees_from_decl(
    conn: sqlite3.Connection, caller_decl_id: int, limit: int = 50
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
          c.line,
          c.callee_name,
          c.callee_decl_id,
          d.name AS resolved_name,
          d.file_path AS resolved_file_path
        FROM calls c
        LEFT JOIN declarations d ON d.id = c.callee_decl_id
        WHERE c.caller_decl_id = ?
        ORDER BY c.line
        LIMIT ?
        """,
        (caller_decl_id, limit),
    ).fetchall()


def query_declaration_by_id(conn: sqlite3.Connection, decl_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM declarations WHERE id = ?", (decl_id,)).fetchone()


def query_declaration_ids(
    conn: sqlite3.Connection,
    *,
    name: str,
    repo_id: str | None = None,
    path_prefix: str | None = None,
    limit: int = 20,
) -> list[sqlite3.Row]:
    sql = "SELECT id, repo_id, file_path, kind, name, start_line FROM declarations WHERE name = ?"
    params: list[Any] = [name]
    if repo_id:
        sql += " AND repo_id = ?"
        params.append(repo_id)
    if path_prefix:
        sql += " AND file_path LIKE ? ESCAPE '\\'"
        params.append(f"{_escape_like(_normalize_path(path_prefix))}%")
    sql += " ORDER BY file_path LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def query_impact_radius(
    conn: sqlite3.Connection,
    declaration_ids: list[int],
    *,
    depth: int = 3,
    max_nodes: int = 200,
) -> dict[str, Any]:
    """BFS over callers + callees + inheritors within budget.

    Returns a closure dict with node and edge lists suitable for rendering or
    further analysis.  Stops expanding when ``max_nodes`` is reached.
    """
    if not declaration_ids or depth < 1 or max_nodes < 1:
        return {
            "declaration_ids": declaration_ids,
            "depth": depth,
            "max_nodes": max_nodes,
            "nodes": [],
            "edges": [],
            "truncated": False,
        }

    visited: set[int] = set(declaration_ids)
    frontier: set[int] = set(declaration_ids)
    nodes_by_id: dict[int, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def _batch_fetch_nodes(ids: set[int]) -> None:
        """Batch-load declaration rows for all ids not yet in nodes_by_id."""
        missing = [i for i in ids if i not in nodes_by_id]
        if not missing:
            return
        ph2 = ",".join("?" * len(missing))
        rows = conn.execute(
            f"SELECT * FROM declarations WHERE id IN ({ph2})",
            tuple(missing),
        ).fetchall()
        for r in rows:
            nodes_by_id[int(r["id"])] = {str(k): r[k] for k in r.keys()}

    _batch_fetch_nodes(set(declaration_ids))
    truncated = False

    for _hop in range(depth):
        if not frontier:
            break
        next_frontier: set[int] = set()
        ph = ",".join("?" * len(frontier))

        # Callers (inbound)
        caller_rows = conn.execute(
            f"""
            SELECT c.caller_decl_id, c.callee_decl_id, c.callee_name, c.line,
                   d.name AS caller_name, d.kind AS caller_kind, d.file_path AS caller_file
            FROM calls c
            LEFT JOIN declarations d ON d.id = c.caller_decl_id
            WHERE c.callee_decl_id IN ({ph})
              AND c.caller_decl_id IS NOT NULL
            """,
            tuple(frontier),
        ).fetchall()
        for r in caller_rows:
            cid = r["caller_decl_id"]
            if cid is None:
                continue
            cid = int(cid)
            edges.append(
                {
                    "from": cid,
                    "to": int(r["callee_decl_id"]),
                    "kind": "calls",
                    "callee_name": r["callee_name"],
                    "line": r["line"],
                }
            )
            if cid not in visited:
                if len(visited) >= max_nodes:
                    truncated = True
                    continue
                visited.add(cid)
                next_frontier.add(cid)

        # Callees (outbound)
        callee_rows = conn.execute(
            f"""
            SELECT c.caller_decl_id, c.callee_decl_id, c.callee_name, c.line
            FROM calls c
            WHERE c.caller_decl_id IN ({ph})
              AND c.callee_decl_id IS NOT NULL
            """,
            tuple(frontier),
        ).fetchall()
        for r in callee_rows:
            tid = r["callee_decl_id"]
            if tid is None:
                continue
            tid = int(tid)
            edges.append(
                {
                    "from": int(r["caller_decl_id"]),
                    "to": tid,
                    "kind": "calls",
                    "callee_name": r["callee_name"],
                    "line": r["line"],
                }
            )
            if tid not in visited:
                if len(visited) >= max_nodes:
                    truncated = True
                    continue
                visited.add(tid)
                next_frontier.add(tid)

        # Inheritors (subclasses of declarations in frontier)
        inh_rows = conn.execute(
            f"""
            SELECT ih.subclass_decl_id, ih.superclass_decl_id
            FROM inherits ih
            WHERE ih.superclass_decl_id IN ({ph})
            """,
            tuple(frontier),
        ).fetchall()
        for r in inh_rows:
            sid = int(r["subclass_decl_id"])
            edges.append(
                {
                    "from": sid,
                    "to": int(r["superclass_decl_id"]),
                    "kind": "inherits",
                }
            )
            if sid not in visited:
                if len(visited) >= max_nodes:
                    truncated = True
                    continue
                visited.add(sid)
                next_frontier.add(sid)

        # Tests (declarations covered by a test that tests frontier nodes)
        test_rows = conn.execute(
            f"""
            SELECT t.tested_decl_id, t.test_file_path, t.confidence
            FROM tests t
            WHERE t.tested_decl_id IN ({ph})
            """,
            tuple(frontier),
        ).fetchall()
        for r in test_rows:
            edges.append(
                {
                    "from": "test:" + str(r["test_file_path"]),
                    "to": int(r["tested_decl_id"]),
                    "kind": "tests",
                    "confidence": r["confidence"],
                }
            )

        # Batch-fetch declaration metadata for all nodes discovered this hop
        _batch_fetch_nodes(next_frontier)
        frontier = next_frontier

    return {
        "declaration_ids": declaration_ids,
        "depth": depth,
        "max_nodes": max_nodes,
        "nodes": list(nodes_by_id.values()),
        "edges": edges,
        "node_count": len(nodes_by_id),
        "edge_count": len(edges),
        "truncated": truncated,
    }


def _chain_entry_matches_target(entry: dict[str, Any], target_name: str) -> bool:
    t = target_name.strip()
    if not t:
        return False
    res = entry.get("resolved_name")
    if res is not None and str(res) == t:
        return True
    callee = str(entry.get("callee_name", "")).split(".")[-1]
    return callee == t


def build_call_chain(
    conn: sqlite3.Connection,
    start_decl_id: int,
    *,
    max_depth: int = 5,
    breadth_limit: int = 12,
    target_name: str | None = None,
) -> dict[str, Any]:
    """Outgoing callees from ``start_decl_id`` up to ``max_depth`` (BFS).

    When ``target_name`` is set, stop early once a callee in the frontier
    matches that symbol name (resolved declaration name or call tail).
    """
    levels: list[list[dict[str, Any]]] = []
    frontier = {start_decl_id}
    seen: set[int] = {start_decl_id}
    matched_target = False

    for _ in range(max_depth):
        if not frontier:
            break
        next_frontier: set[int] = set()
        level_rows: list[dict[str, Any]] = []
        for cid in frontier:
            rows = query_callees_from_decl(conn, cid, limit=breadth_limit)
            for r in rows:
                callee_id = r["callee_decl_id"]
                entry = {
                    "from_decl_id": cid,
                    "line": int(r["line"]),
                    "callee_name": str(r["callee_name"]),
                    "callee_decl_id": int(callee_id) if callee_id is not None else None,
                    "resolved_name": r["resolved_name"],
                    "resolved_file_path": r["resolved_file_path"],
                }
                level_rows.append(entry)
                if target_name and _chain_entry_matches_target(entry, target_name):
                    matched_target = True
                if callee_id is not None and int(callee_id) not in seen:
                    seen.add(int(callee_id))
                    next_frontier.add(int(callee_id))
        if level_rows:
            levels.append(level_rows)
        if matched_target:
            break
        frontier = next_frontier

    return {
        "start_decl_id": start_decl_id,
        "max_depth": max_depth,
        "levels": levels,
        "matched_target": matched_target,
        "target_name": target_name,
    }
