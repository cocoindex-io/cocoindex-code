"""MCP tool implementations for cocoindex analysis (impact radius, change detection, architecture).

These are the backend implementations that implement the MCP tool interface.
They are called from the MCP server to handle tool requests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .analytics.centrality import query_bridge_nodes, query_hub_nodes
from .analytics.communities import query_communities
from .analytics.flows import detect_flows
from .analytics.knowledge_gaps import get_knowledge_gaps
from .change_detection import (
    detect_changes_for_repo,
)
from .declarations_db import db_connection
from .declarations_graph import (
    build_call_chain,
    query_callees_from_decl,
    query_declaration_by_id,
    query_declaration_ids,
    query_find_callers,
    query_impact_radius,
)

_log = logging.getLogger("cocoindex.mcp-handlers")

_MAX_IMPACT_NODES = int(os.environ.get("MAX_IMPACT_NODES", "200"))
_MAX_IMPACT_DEPTH = int(os.environ.get("MAX_IMPACT_DEPTH", "5"))


def query_impact_radius_tool(
    db_path: Path,
    decl_ids: list[int],
    *,
    depth: int = 3,
    max_nodes: int | None = None,
) -> dict[str, Any]:
    """MCP tool: Get BFS impact radius (callers and callees) for declarations.

    Args:
      db_path: Path to declarations database
      decl_ids: Declaration IDs to analyze
      depth: Maximum BFS depth (1-5)
      max_nodes: Maximum nodes to return (default from env MAX_IMPACT_NODES)

    Returns:
      Dictionary with nodes, edges, truncation flag
    """
    depth = max(1, min(depth, _MAX_IMPACT_DEPTH))
    max_nodes = max_nodes or _MAX_IMPACT_NODES
    max_nodes = min(max_nodes, _MAX_IMPACT_NODES)

    try:
        with db_connection(db_path) as conn:
            result = query_impact_radius(conn, decl_ids, depth=depth, max_nodes=max_nodes)
        return {
            "success": True,
            "result": result,
        }
    except Exception as exc:
        _log.error("impact_radius failed: %s", exc)
        return {
            "success": False,
            "error": str(exc),
        }


def detect_changes_tool(
    db_path: Path,
    repo_root: Path,
    repo_id: str,
    ref_spec: str = "HEAD",
    *,
    path_prefix: str | None = None,
    top_n: int = 20,
) -> dict[str, Any]:
    """MCP tool: Detect changed declarations from git diff with risk scores.

    Args:
      db_path: Path to declarations database
      repo_root: Repository root directory
      repo_id: Repository identifier
      ref_spec: Git ref spec (default: HEAD for working tree)
      path_prefix: Optional filter to changes in specific path
      top_n: Number of top-risk declarations to return

    Returns:
      Dictionary with affected declarations, risk scores, and summary
    """
    try:
        result = detect_changes_for_repo(
            db_path,
            repo_root,
            repo_id,
            ref_spec,
            path_prefix=path_prefix,
            top_n=top_n,
        )
        return result
    except Exception as exc:
        _log.error("detect_changes failed: %s", exc)
        return {
            "success": False,
            "error": str(exc),
            "ref_spec": ref_spec,
        }


def get_architecture_overview_tool(
    db_path: Path,
    repo_id: str | None = None,
    *,
    hub_limit: int = 20,
    community_limit: int = 10,
) -> dict[str, Any]:
    """MCP tool: Get architecture overview (hubs, bridges, communities).

    Args:
      db_path: Path to declarations database
      repo_id: Optional repo ID filter
      hub_limit: Number of hub nodes to return
      community_limit: Number of communities to return

    Returns:
      Dictionary with hub nodes, bridge nodes, community summary
    """
    try:
        with db_connection(db_path) as conn:
            hubs = query_hub_nodes(conn, repo_id=repo_id, limit=hub_limit)
            bridges = query_bridge_nodes(conn, repo_id=repo_id, limit=hub_limit // 2)
            communities = query_communities(conn, repo_id=repo_id, limit_per_community=5)

            return {
                "success": True,
                "hub_nodes": hubs,
                "bridge_nodes": bridges,
                "communities": communities,
                "summary": {
                    "hub_count": len(hubs),
                    "bridge_count": len(bridges),
                    "community_count": len(communities),
                },
            }
    except Exception as exc:
        _log.error("architecture_overview failed: %s", exc)
        return {
            "success": False,
            "error": str(exc),
        }


def get_knowledge_gaps_tool(
    db_path: Path,
    repo_id: str | None = None,
    *,
    hub_min_in_degree: int = 3,
    thin_community_max_size: int = 2,
    limit: int = 30,
) -> dict[str, Any]:
    """MCP tool: Get knowledge gaps (untested hubs, isolated nodes, thin communities).

    Args:
      db_path: Path to declarations database
      repo_id: Optional repo ID filter
      hub_min_in_degree: Minimum in-degree to be considered a hub
      thin_community_max_size: Community size threshold for "thin"
      limit: Limit results per category

    Returns:
      Dictionary with gaps in three categories: untested_hubs, isolated_nodes, thin_communities
    """
    try:
        result = get_knowledge_gaps(
            db_path,
            repo_id=repo_id,
            hub_min_in_degree=hub_min_in_degree,
            thin_community_max_size=thin_community_max_size,
            limit=limit,
        )
        return result
    except Exception as exc:
        _log.error("knowledge_gaps failed: %s", exc)
        return {
            "success": False,
            "error": str(exc),
        }


def detect_flows_tool(
    db_path: Path,
    repo_id: str | None = None,
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """MCP tool: Detect flow entry points (FastAPI routes, handlers, etc).

    Args:
      db_path: Path to declarations database
      repo_id: Optional repo ID filter
      limit: Maximum flows to return

    Returns:
      Dictionary with detected flows and their criticality scores
    """
    try:
        result = detect_flows(db_path, repo_id=repo_id, limit=limit)
        return result
    except Exception as exc:
        _log.error("detect_flows failed: %s", exc)
        return {
            "success": False,
            "error": str(exc),
        }


def get_declaration_detail_tool(
    db_path: Path,
    decl_id: int,
) -> dict[str, Any]:
    """MCP tool: Get full details of a declaration.

    Args:
      db_path: Path to declarations database
      decl_id: Declaration ID

    Returns:
      Dictionary with declaration details
    """
    try:
        with db_connection(db_path) as conn:
            row = query_declaration_by_id(conn, decl_id)
            if row is None:
                return {
                    "success": False,
                    "error": f"Declaration {decl_id} not found",
                }
            return {
                "success": True,
                "declaration": {str(k): row[k] for k in row.keys()},
            }
    except Exception as exc:
        _log.error("declaration_detail failed: %s", exc)
        return {
            "success": False,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Additional MCP handlers added for multi-repo and hybrid search integration
# ---------------------------------------------------------------------------


def sync_configured_repos_tool(
    config_path: str | None = None, repo_ids: list[str] | None = None, force: bool = False
) -> dict[str, Any]:
    """MCP tool: Sync and link repositories from a CodebaseConfig file."""
    try:
        import dataclasses

        from .config import load_codebase_config
        from .multi_repo import MultiRepoOrchestrator

        cfg, cfg_path = load_codebase_config(config_path)
        orchestrator = MultiRepoOrchestrator(cfg, cfg_path)
        results = orchestrator.sync_and_link_repos(repo_ids=repo_ids, force=force)
        return {"success": True, "results": [dataclasses.asdict(r) for r in results]}
    except Exception as exc:
        _log.error("sync_configured_repos failed: %s", exc)
        return {"success": False, "error": str(exc)}


def get_repo_sync_status_tool(config_path: str | None = None) -> dict[str, Any]:
    """MCP tool: Return repository sync status for configured repos."""
    try:
        from .config import load_codebase_config
        from .multi_repo import MultiRepoOrchestrator

        cfg, cfg_path = load_codebase_config(config_path)
        orchestrator = MultiRepoOrchestrator(cfg, cfg_path)
        status = orchestrator.run_status()
        return {"success": True, "status": status}
    except Exception as exc:
        _log.error("get_repo_sync_status failed: %s", exc)
        return {"success": False, "error": str(exc)}


def hybrid_search_tool(
    project_root: str, query: str, limit: int = 10, refresh_index: bool = True
) -> dict[str, Any]:
    """MCP tool: Hybrid vector + BM25 search over the project's index."""
    try:
        from pathlib import Path

        from . import client as _client
        from . import hybrid_search as _hyb
        from . import settings as _settings

        if refresh_index:
            _client.index(project_root)
        vec_resp = _client.search(project_root=project_root, query=query, limit=limit)
        vector_results = []
        for r in vec_resp.results:
            vector_results.append(
                {
                    "file_path": r.file_path,
                    "content": r.content,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                    "language": getattr(r, "language", None),
                    "score": r.score,
                }
            )
        db_path = _settings.target_sqlite_db_path(Path(project_root))
        _hyb.ensure_fts_index(db_path, force_rebuild=refresh_index)
        keyword_results = _hyb.keyword_search(db_path, query, limit=limit)
        fused = _hyb.reciprocal_rank_fusion(
            vector_results=vector_results, keyword_results=keyword_results, limit=limit
        )
        return {"success": True, "results": fused}
    except Exception as exc:
        _log.error("hybrid_search failed: %s", exc)
        return {"success": False, "error": str(exc)}


def ripgrep_bounded_tool(
    project_root: str,
    pattern: str,
    path_prefix: str | None = None,
    glob: str | None = None,
    fixed_strings: bool = True,
    max_matches: int = 200,
    per_file_cap: int = 40,
    wall_timeout_s: float = 25.0,
) -> dict[str, Any]:
    """MCP tool: Run bounded ripgrep within unified root and return keyword-like matches."""
    try:
        from pathlib import Path

        from . import rg_bounded as _rg

        root = Path(project_root)
        return _rg.run_bounded_rg(
            root,
            pattern,
            path_prefix=path_prefix,
            glob=glob,
            fixed_strings=fixed_strings,
            max_matches=max_matches,
            per_file_cap=per_file_cap,
            wall_timeout_s=wall_timeout_s,
        )
    except Exception as exc:
        _log.error("ripgrep_bounded failed: %s", exc)
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Native codebase intelligence handlers
# ---------------------------------------------------------------------------


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {str(k): row[k] for k in row.keys()}


def _context_manifest_path(project_root: Path) -> Path:
    return project_root / ".cocoindex_code" / "context_artifacts.json"


def _load_context_manifest(project_root: Path) -> dict[str, Any]:
    path = _context_manifest_path(project_root)
    if not path.is_file():
        return {"artifacts": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"artifacts": {}}
    return raw if isinstance(raw, dict) else {"artifacts": {}}


def _artifact_files(project_root: Path, raw_path: str) -> list[Path]:
    path = (project_root / raw_path).resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError:
        return []
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.is_file())
    return []


def _hash_files(files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(str(path).encode("utf-8", errors="replace"))
        digest.update(str(stat.st_size).encode())
        digest.update(str(stat.st_mtime_ns).encode())
    return digest.hexdigest()


def _context_artifact_items(raw_artifacts: Any) -> list[dict[str, Any]]:
    """Normalize supported coco-context.yml artifact shapes.

    Accepted forms:
      artifacts:
        - name: architecture
          path: docs/architecture.md

      artifacts:
        architecture:
          path: docs/architecture.md
    """
    artifacts: list[dict[str, Any]] = []
    if isinstance(raw_artifacts, list):
        for item in raw_artifacts:
            if isinstance(item, dict):
                artifacts.append(dict(item))
        return artifacts
    if isinstance(raw_artifacts, dict):
        for name, value in raw_artifacts.items():
            if isinstance(value, dict):
                artifact = dict(value)
            elif isinstance(value, str):
                artifact = {"path": value}
            else:
                continue
            artifact.setdefault("name", str(name))
            artifacts.append(artifact)
    return artifacts


def codebase_graph_stats_tool(db_path: Path, repo_id: str | None = None) -> dict[str, Any]:
    """Return file/symbol graph statistics from the declarations DB."""
    try:
        with db_connection(db_path) as conn:
            repo_clause = "WHERE repo_id = ?" if repo_id else ""
            params: list[Any] = [repo_id] if repo_id else []
            decls = conn.execute(
                f"SELECT COUNT(*) FROM declarations {repo_clause}", params
            ).fetchone()[0]
            files = conn.execute(
                f"SELECT COUNT(DISTINCT file_path) FROM declarations {repo_clause}", params
            ).fetchone()[0]
            calls = conn.execute(f"SELECT COUNT(*) FROM calls {repo_clause}", params).fetchone()[0]
            inherits = conn.execute(
                f"SELECT COUNT(*) FROM inherits {repo_clause}", params
            ).fetchone()[0]
            imports = conn.execute(
                f"SELECT COUNT(*) FROM imports {repo_clause}", params
            ).fetchone()[0]
            connected = conn.execute(
                f"""
                SELECT d.file_path,
                       COUNT(c1.id) + COUNT(c2.id) AS connections
                FROM declarations d
                LEFT JOIN calls c1 ON c1.caller_decl_id = d.id
                LEFT JOIN calls c2 ON c2.callee_decl_id = d.id
                {repo_clause.replace("repo_id", "d.repo_id") if repo_clause else ""}
                GROUP BY d.file_path
                ORDER BY connections DESC, d.file_path
                LIMIT 20
                """,
                params,
            ).fetchall()
            return {
                "success": True,
                "repo_id": repo_id,
                "files": int(files),
                "symbols": int(decls),
                "imports": int(imports),
                "call_edges": int(calls),
                "inherit_edges": int(inherits),
                "most_connected_files": [_row_dict(r) for r in connected],
            }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def codebase_graph_query_tool(
    db_path: Path, file_path: str, repo_id: str | None = None
) -> dict[str, Any]:
    """Return imports and dependents for a file from declarations/imports/calls."""
    try:
        with db_connection(db_path) as conn:
            path = file_path.replace("\\", "/").lstrip("/")
            params: list[Any] = [path, f"%/{path}"]
            repo_filter = ""
            if repo_id:
                repo_filter = " AND repo_id = ?"
                params.append(repo_id)
            imports = conn.execute(
                f"""
                SELECT repo_id, file_path, module_path, imported_names, start_line
                FROM imports
                WHERE (file_path = ? OR file_path LIKE ?){repo_filter}
                ORDER BY start_line
                """,
                params,
            ).fetchall()
            dependents = conn.execute(
                f"""
                SELECT DISTINCT c.repo_id, c.file_path, c.line, c.callee_name
                FROM calls c
                JOIN declarations d ON d.id = c.callee_decl_id
                WHERE (d.file_path = ? OR d.file_path LIKE ?)
                  {"AND d.repo_id = ?" if repo_id else ""}
                ORDER BY c.file_path, c.line
                LIMIT 200
                """,
                ([path, f"%/{path}", repo_id] if repo_id else [path, f"%/{path}"]),
            ).fetchall()
            symbols = conn.execute(
                f"""
                SELECT id, repo_id, file_path, kind, name, start_line, end_line, exported
                FROM declarations
                WHERE (file_path = ? OR file_path LIKE ?){repo_filter}
                ORDER BY start_line
                """,
                params,
            ).fetchall()
            return {
                "success": True,
                "file_path": path,
                "imports": [_row_dict(r) for r in imports],
                "imported_by": [_row_dict(r) for r in dependents],
                "symbols": [_row_dict(r) for r in symbols],
            }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def codebase_graph_circular_tool(
    db_path: Path, repo_id: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Find simple file-level cycles from materialized call edges."""
    try:
        with db_connection(db_path) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT c1.file_path AS from_file, d.file_path AS to_file
                FROM calls c1
                JOIN declarations d ON d.id = c1.callee_decl_id
                WHERE c1.callee_decl_id IS NOT NULL
                  AND c1.file_path != d.file_path
                  AND (? IS NULL OR c1.repo_id = ?)
                """,
                (repo_id, repo_id),
            ).fetchall()
        graph: dict[str, set[str]] = {}
        for row in rows:
            graph.setdefault(str(row["from_file"]), set()).add(str(row["to_file"]))

        cycles: list[list[str]] = []
        seen_keys: set[tuple[str, ...]] = set()

        def visit(start: str, node: str, path_stack: list[str]) -> None:
            if len(cycles) >= limit or len(path_stack) > 12:
                return
            for nxt in graph.get(node, set()):
                if nxt == start and len(path_stack) > 1:
                    cycle = path_stack + [start]
                    key = tuple(sorted(set(cycle)))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        cycles.append(cycle)
                elif nxt not in path_stack:
                    visit(start, nxt, path_stack + [nxt])

        for node in list(graph):
            visit(node, node, [node])
            if len(cycles) >= limit:
                break
        return {"success": True, "cycles": cycles, "cycle_count": len(cycles)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def codebase_symbols_tool(
    db_path: Path,
    *,
    file: str | None = None,
    query: str | None = None,
    repo_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """List symbols in a file or search symbol names."""
    try:
        with db_connection(db_path) as conn:
            sql = (
                "SELECT id, repo_id, file_path, kind, name, start_line, end_line, exported "
                "FROM declarations WHERE 1=1"
            )
            params: list[Any] = []
            if file:
                norm = file.replace("\\", "/").lstrip("/")
                sql += " AND (file_path = ? OR file_path LIKE ?)"
                params.extend([norm, f"%/{norm}"])
            if query:
                sql += " AND name LIKE ? ESCAPE '\\'"
                escaped_query = query.replace("%", "\\%").replace("_", "\\_")
                params.append(f"%{escaped_query}%")
            if repo_id:
                sql += " AND repo_id = ?"
                params.append(repo_id)
            sql += " ORDER BY file_path, start_line LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return {"success": True, "symbols": [_row_dict(r) for r in rows]}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def codebase_symbol_tool(
    db_path: Path,
    *,
    name: str,
    file: str | None = None,
    repo_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Return definition, callers, and callees for one or more matching symbols."""
    try:
        with db_connection(db_path) as conn:
            refs = query_declaration_ids(
                conn, name=name, repo_id=repo_id, path_prefix=file, limit=limit
            )
            contexts = []
            for ref in refs:
                decl_id = int(ref["id"])
                decl = query_declaration_by_id(conn, decl_id)
                callers = query_find_callers(conn, decl_id, limit=50)
                callees = query_callees_from_decl(conn, decl_id, limit=50)
                contexts.append(
                    {
                        "declaration": _row_dict(decl) if decl else _row_dict(ref),
                        "callers": [_row_dict(r) for r in callers],
                        "callees": [_row_dict(r) for r in callees],
                    }
                )
            return {"success": True, "matches": len(contexts), "symbols": contexts}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def codebase_impact_tool(
    db_path: Path,
    *,
    target: str,
    repo_id: str | None = None,
    depth: int = 3,
    max_nodes: int = 200,
) -> dict[str, Any]:
    """Impact analysis for either a file path or symbol name."""
    try:
        target_clean = target.strip()
        with db_connection(db_path) as conn:
            is_path = "/" in target_clean or "." in Path(target_clean).name
            if is_path:
                norm = target_clean.replace("\\", "/").lstrip("/")
                rows = conn.execute(
                    """
                    SELECT id FROM declarations
                    WHERE (file_path = ? OR file_path LIKE ?)
                      AND (? IS NULL OR repo_id = ?)
                    LIMIT ?
                    """,
                    (norm, f"%/{norm}", repo_id, repo_id, max_nodes),
                ).fetchall()
            else:
                rows = query_declaration_ids(
                    conn, name=target_clean, repo_id=repo_id, limit=max_nodes
                )
            decl_ids = [int(r["id"]) for r in rows]
            result = query_impact_radius(conn, decl_ids, depth=depth, max_nodes=max_nodes)
            return {
                "success": True,
                "target": target_clean,
                "target_kind": "file" if is_path else "symbol",
                "declaration_ids": decl_ids,
                "result": result,
            }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def codebase_flow_tool(
    db_path: Path,
    *,
    entrypoint: str | None = None,
    file: str | None = None,
    repo_id: str | None = None,
    depth: int = 5,
    limit: int = 50,
) -> dict[str, Any]:
    """Trace forward call flow, or list likely entry points when omitted."""
    try:
        with db_connection(db_path) as conn:
            if not entrypoint:
                rows = conn.execute(
                    """
                    SELECT d.id, d.repo_id, d.file_path, d.kind, d.name, d.start_line,
                           COUNT(c.id) AS out_degree
                    FROM declarations d
                    LEFT JOIN calls c ON c.caller_decl_id = d.id
                    WHERE (? IS NULL OR d.repo_id = ?)
                    GROUP BY d.id
                    HAVING out_degree > 0
                    ORDER BY out_degree DESC, d.file_path
                    LIMIT ?
                    """,
                    (repo_id, repo_id, limit),
                ).fetchall()
                return {"success": True, "entrypoints": [_row_dict(r) for r in rows]}
            refs = query_declaration_ids(
                conn,
                name=entrypoint,
                repo_id=repo_id,
                path_prefix=file,
                limit=limit,
            )
            if not refs:
                return {"success": False, "error": f"No symbol named {entrypoint!r} found"}
            if len(refs) > 1 and not file:
                return {
                    "success": False,
                    "error": f"Symbol {entrypoint!r} is ambiguous; pass file to disambiguate",
                    "matches": [_row_dict(r) for r in refs],
                }
            chain = build_call_chain(conn, int(refs[0]["id"]), max_depth=depth, breadth_limit=20)
            return {"success": True, "entrypoint": _row_dict(refs[0]), "flow": chain}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def codebase_context_list_tool(project_root: Path) -> dict[str, Any]:
    """List locally configured context artifacts."""
    config_path = project_root / "coco-context.yml"
    artifacts: list[dict[str, Any]] = []
    manifest = _load_context_manifest(project_root)
    indexed = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    try:
        if config_path.is_file():
            import yaml

            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raw = {}
            for item in _context_artifact_items(raw.get("artifacts")):
                artifact = dict(item)
                name = artifact.get("name")
                if isinstance(name, str):
                    artifact["index"] = indexed.get(name)
                artifacts.append(artifact)
        return {
            "success": True,
            "config": str(config_path),
            "manifest": str(_context_manifest_path(project_root)),
            "artifacts": artifacts,
            "message": None
            if config_path.is_file()
            else "No context artifacts configured. Create coco-context.yml with an artifacts list.",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def codebase_context_index_tool(project_root: Path) -> dict[str, Any]:
    """Index local context artifact metadata into the CocoIndex project directory."""
    cfg = codebase_context_list_tool(project_root)
    if not cfg.get("success"):
        return cfg

    indexed: dict[str, Any] = {}
    for item in cfg.get("artifacts", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        raw_path = item.get("path")
        if not isinstance(name, str) or not isinstance(raw_path, str):
            continue
        files = _artifact_files(project_root, raw_path)
        indexed[name] = {
            "name": name,
            "path": raw_path,
            "description": item.get("description"),
            "file_count": len(files),
            "signature": _hash_files(files),
            "indexed_at": int(time.time()),
            "missing": not files,
        }

    manifest_path = _context_manifest_path(project_root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"artifacts": indexed}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "success": True,
        "manifest": str(manifest_path),
        "indexed": list(indexed.values()),
        "indexed_count": len(indexed),
    }


def codebase_context_remove_tool(project_root: Path) -> dict[str, Any]:
    """Remove local context artifact metadata."""
    manifest_path = _context_manifest_path(project_root)
    existed = manifest_path.exists()
    try:
        if existed:
            manifest_path.unlink()
        return {"success": True, "removed": 1 if existed else 0, "manifest": str(manifest_path)}
    except OSError as exc:
        return {"success": False, "error": str(exc), "manifest": str(manifest_path)}


def codebase_context_search_tool(
    project_root: Path, query: str, artifact: str | None = None, limit: int = 10
) -> dict[str, Any]:
    """Simple native context search over configured artifact text files."""
    try:
        cfg = codebase_context_list_tool(project_root)
        if not cfg.get("success"):
            return cfg
        terms = [t.lower() for t in query.split() if t.strip()]
        hits: list[dict[str, Any]] = []
        for item in cfg.get("artifacts", []):
            if artifact and item.get("name") != artifact:
                continue
            raw_path = item.get("path")
            if not isinstance(raw_path, str):
                continue
            files = _artifact_files(project_root, raw_path)
            for file_path in files:
                try:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                lines = text.splitlines()
                for idx, line in enumerate(lines, start=1):
                    lower = line.lower()
                    score = sum(1 for term in terms if term in lower)
                    if score:
                        hits.append(
                            {
                                "artifact": item.get("name"),
                                "file_path": str(file_path.relative_to(project_root)),
                                "line": idx,
                                "content": line,
                                "score": float(score),
                            }
                        )
                        if len(hits) >= limit:
                            return {"success": True, "results": hits}
        return {"success": True, "results": hits}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
