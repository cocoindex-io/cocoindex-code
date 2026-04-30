"""Generic flow detection: auto-detect entry points by criticality.

Detects FastAPI routes, Bun HTTP handlers, queue consumers, and Redis pub/sub
subscribers from declarations and imports.  Sorts by criticality (in-degree
of detected entry point + whether a test exists).

This is a structural heuristic — not full AST analysis.  Extend patterns as needed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..declarations_db import db_connection

# Pattern families matched against declaration signatures
_FASTAPI_PATTERNS = [
    re.compile(r"@(app|router)\.(get|post|put|delete|patch|options|head|websocket)\s*\(", re.I),
]
_BUN_HTTP_PATTERNS = [
    re.compile(r"serve\s*\("),
    re.compile(r"Bun\.serve\s*\("),
]
_QUEUE_PATTERNS = [
    re.compile(r"(subscribe|consume|addConsumer|listen)\s*\(", re.I),
    re.compile(r"bull(mq)?\.(add|process|worker)", re.I),
]
_REDIS_PUBSUB_PATTERNS = [
    re.compile(r"\.subscribe\s*\(", re.I),
    re.compile(r"redis\.(subscribe|psubscribe)\s*\(", re.I),
]

_ALL_PATTERN_FAMILIES: list[tuple[str, list[re.Pattern[str]]]] = [
    ("fastapi_route", _FASTAPI_PATTERNS),
    ("bun_http", _BUN_HTTP_PATTERNS),
    ("queue_consumer", _QUEUE_PATTERNS),
    ("redis_pubsub", _REDIS_PUBSUB_PATTERNS),
]


def _score_criticality(in_degree: int, tested: bool) -> float:
    return float(in_degree + (1 if tested else 0))


def detect_flows(
    db_path: Path,
    repo_id: str | None = None,
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """Detect flow entry points and rank by criticality."""
    with db_connection(db_path) as conn:
        clause = "WHERE repo_id = ?" if repo_id else ""
        params: list[Any] = [repo_id] if repo_id else []

        rows = conn.execute(
            f"""
            SELECT d.id, d.repo_id, d.file_path, d.name, d.kind,
                   d.signature, d.start_line, d.exported,
                   (SELECT COUNT(*) FROM calls c WHERE c.callee_decl_id = d.id) AS in_degree,
                   (SELECT COUNT(*) FROM tests t WHERE t.tested_decl_id = d.id) AS test_count
            FROM declarations d
            {clause}
            """,
            params,
        ).fetchall()

        detected: list[dict[str, Any]] = []
        for row in rows:
            sig = str(row["signature"] or "")
            matched_families: list[str] = []
            for family_name, patterns in _ALL_PATTERN_FAMILIES:
                for pat in patterns:
                    if pat.search(sig):
                        matched_families.append(family_name)
                        break

            if not matched_families:
                continue

            in_deg = int(row["in_degree"])
            tested = int(row["test_count"]) > 0
            detected.append(
                {
                    "decl_id": int(row["id"]),
                    "repo_id": str(row["repo_id"]),
                    "file_path": str(row["file_path"]),
                    "name": str(row["name"]),
                    "kind": str(row["kind"]),
                    "signature": sig[:300],
                    "start_line": int(row["start_line"]),
                    "exported": bool(row["exported"]),
                    "flow_types": matched_families,
                    "in_degree": in_deg,
                    "tested": tested,
                    "criticality": _score_criticality(in_deg, tested),
                }
            )

        detected.sort(key=lambda d: d["criticality"], reverse=True)
        return {
            "success": True,
            "flows_detected": len(detected),
            "flows": detected[:limit],
        }
