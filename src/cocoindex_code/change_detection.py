"""Git-diff to declaration mapper and risk scorer (G3).

Maps changed line ranges to declaration IDs, then computes a risk score:
    risk = centrality_proxy * test_gap * log1p(diff_size)

where:
  centrality_proxy  = in_degree fallback when betweenness is unavailable
  test_gap          = 1.0 if untested, 0.0 if covered
  diff_size         = lines changed in that declaration's range
"""

from __future__ import annotations

import math
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .declarations_db import _normalize_path, db_connection

# Risk-score formula weights — heuristic; tune from PR-review feedback.
# risk = (1 + centrality_proxy) * WEIGHT_TEST_GAP * log1p(diff_size * WEIGHT_DIFF_SIZE)
# centrality_proxy: betweenness when available, else raw in_degree
# test_gap: WEIGHT_TEST_GAP when untested (1.0), 0.0 when covered
# test_gap is 1.0 when untested, 0.2 when covered (not 0.0) so covered hub nodes
# still surface in risk ranking rather than being completely hidden.
WEIGHT_TEST_GAP: float = 1.0  # penalty multiplier for untested declarations
_TEST_GAP_COVERED: float = 0.2  # partial penalty for tested declarations (covers hub visibility)
WEIGHT_DIFF_SIZE: float = 1.0  # scale factor on diff-size inside log1p


@dataclass
class DiffHunk:
    file_path: str
    start_line: int
    end_line: int
    lines_changed: int


@dataclass
class AffectedDeclaration:
    decl_id: int
    repo_id: str
    file_path: str
    name: str
    kind: str
    signature: str | None
    start_line: int
    end_line: int
    exported: bool
    lines_changed: int
    risk_score: float
    tested: bool
    in_degree: int
    betweenness: float | None


def parse_diff_hunks(diff_text: str) -> list[DiffHunk]:
    """Parse unified diff output into (file_path, start_line, end_line, lines_changed) hunks."""
    hunks: list[DiffHunk] = []
    current_file: str | None = None
    hunk_start = 0
    hunk_line = 0
    lines_changed_in_hunk = 0

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ b/"):
            current_file = raw_line[6:].strip()
            continue
        if raw_line.startswith("@@ "):
            if current_file and hunk_start > 0 and lines_changed_in_hunk > 0:
                hunks.append(
                    DiffHunk(
                        file_path=current_file,
                        start_line=hunk_start,
                        end_line=max(hunk_start, hunk_line - 1),
                        lines_changed=lines_changed_in_hunk,
                    )
                )
            m = re.search(r"\+(\d+)(?:,(\d+))?", raw_line)
            if m:
                hunk_start = int(m.group(1))
                hunk_line = hunk_start
                lines_changed_in_hunk = 0
            continue
        if current_file and raw_line.startswith("+") and not raw_line.startswith("+++"):
            lines_changed_in_hunk += 1
            hunk_line += 1
        elif current_file and raw_line.startswith("-") and not raw_line.startswith("---"):
            lines_changed_in_hunk += 1
        elif current_file and not raw_line.startswith(("-", "+")):
            hunk_line += 1

    if current_file and hunk_start > 0 and lines_changed_in_hunk > 0:
        hunks.append(
            DiffHunk(
                file_path=current_file,
                start_line=hunk_start,
                end_line=max(hunk_start, hunk_line - 1),
                lines_changed=lines_changed_in_hunk,
            )
        )
    return hunks


def get_diff_hunks(
    repo_root: Path,
    ref_spec: str = "HEAD",
    *,
    unified: int = 0,
) -> list[DiffHunk]:
    """Run git diff and parse the hunks.

    ``ref_spec`` accepts three shapes:

    - Working-tree diff (default): ``"HEAD"`` — staged + unstaged changes vs HEAD.
    - Single-ref: any commit SHA or branch name, e.g. ``"develop"`` or ``"abc1234"``
      — diff between that ref and HEAD (i.e. ``git diff <ref> --``).
    - Commit-range: ``"develop..HEAD"`` or ``"HEAD~3..HEAD"`` — useful for PR-review
      workflows where you want exactly the commits on your branch.

    Both ``./repo cocoindex impact`` and ``./repo cocoindex review`` accept and
    forward the ``--ref`` argument directly to this function.
    """
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        raise RuntimeError(f"not a git repository (or worktree): {repo_root}")

    if ".." in ref_spec:
        cmd = ["git", "diff", f"--unified={unified}", ref_spec]
    else:
        cmd = ["git", "diff", f"--unified={unified}", ref_spec, "--"]
    try:
        result = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git diff timed out after {exc.timeout}s in {repo_root}") from exc
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git diff failed: {result.stderr.strip()}")
    return parse_diff_hunks(result.stdout)


def map_hunks_to_declarations(
    conn: sqlite3.Connection,
    hunks: list[DiffHunk],
    repo_id: str,
    *,
    path_prefix: str | None = None,
) -> list[tuple[DiffHunk, sqlite3.Row]]:
    """For each hunk, return matching declarations whose range overlaps [start_line, end_line]."""
    out: list[tuple[DiffHunk, sqlite3.Row]] = []
    for hunk in hunks:
        norm_path = _normalize_path(hunk.file_path)
        if path_prefix and not norm_path.startswith(_normalize_path(path_prefix)):
            continue
        rows = conn.execute(
            """
            SELECT * FROM declarations
            WHERE repo_id = ?
              AND (file_path = ? OR file_path LIKE ? ESCAPE '\\')
              AND start_line <= ?
              AND end_line >= ?
            ORDER BY start_line ASC
            """,
            (repo_id, norm_path, "%/" + norm_path, hunk.end_line, hunk.start_line),
        ).fetchall()
        for row in rows:
            out.append((hunk, row))
    return out


def _fetch_centrality(
    conn: sqlite3.Connection,
    decl_id: int,
) -> tuple[float | None, int]:
    """Return (betweenness, in_degree).  Falls back to in_degree from calls table."""
    row = conn.execute(
        "SELECT betweenness, in_degree FROM centrality WHERE decl_id = ?",
        (decl_id,),
    ).fetchone()
    if row is not None:
        return float(row[0]), int(row[1])
    count_row = conn.execute(
        "SELECT COUNT(*) FROM calls WHERE callee_decl_id = ?",
        (decl_id,),
    ).fetchone()
    in_deg = int(count_row[0]) if count_row else 0
    return None, in_deg


def _is_tested(conn: sqlite3.Connection, decl_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM tests WHERE tested_decl_id = ? LIMIT 1",
        (decl_id,),
    ).fetchone()
    return row is not None


def score_affected_declarations(
    conn: sqlite3.Connection,
    hunks: list[DiffHunk],
    repo_id: str,
    *,
    path_prefix: str | None = None,
) -> list[AffectedDeclaration]:
    """Return declarations touched by diff hunks, ranked by risk score."""
    mapping = map_hunks_to_declarations(conn, hunks, repo_id, path_prefix=path_prefix)
    seen_decl_ids: dict[int, AffectedDeclaration] = {}

    for hunk, row in mapping:
        decl_id = int(row["id"])
        betweenness, in_degree = _fetch_centrality(conn, decl_id)
        tested = _is_tested(conn, decl_id)

        centrality_proxy = betweenness if betweenness is not None else float(in_degree)
        test_gap = WEIGHT_TEST_GAP if not tested else _TEST_GAP_COVERED
        diff_size = hunk.lines_changed

        risk = (1.0 + centrality_proxy) * test_gap * math.log1p(diff_size * WEIGHT_DIFF_SIZE)

        if decl_id in seen_decl_ids:
            existing = seen_decl_ids[decl_id]
            merged = AffectedDeclaration(
                decl_id=existing.decl_id,
                repo_id=existing.repo_id,
                file_path=existing.file_path,
                name=existing.name,
                kind=existing.kind,
                signature=existing.signature,
                start_line=existing.start_line,
                end_line=existing.end_line,
                exported=existing.exported,
                lines_changed=existing.lines_changed + hunk.lines_changed,
                risk_score=max(existing.risk_score, risk),
                tested=existing.tested,
                in_degree=existing.in_degree,
                betweenness=existing.betweenness,
            )
            seen_decl_ids[decl_id] = merged
        else:
            sig = row["signature"]
            seen_decl_ids[decl_id] = AffectedDeclaration(
                decl_id=decl_id,
                repo_id=repo_id,
                file_path=str(row["file_path"]),
                name=str(row["name"]),
                kind=str(row["kind"]),
                signature=str(sig) if sig else None,
                start_line=int(row["start_line"]),
                end_line=int(row["end_line"]),
                exported=bool(row["exported"]),
                lines_changed=hunk.lines_changed,
                risk_score=risk,
                tested=tested,
                in_degree=in_degree,
                betweenness=betweenness,
            )

    return sorted(seen_decl_ids.values(), key=lambda d: d.risk_score, reverse=True)


def get_minimal_context(
    affected: list[AffectedDeclaration],
    *,
    top_n: int = 10,
    token_budget: int = 100,
) -> list[dict[str, Any]]:
    """Return top-N affected exports as compact dicts for AI review context."""
    exported_first = sorted(
        affected,
        key=lambda d: (not d.exported, -d.risk_score),
    )
    result: list[dict[str, Any]] = []
    tokens_used = 0
    for decl in exported_first[:top_n]:
        sig = decl.signature or f"{decl.kind} {decl.name}"
        approx_tokens = len(sig.split())
        if tokens_used + approx_tokens > token_budget and result:
            break
        tokens_used += approx_tokens
        result.append(
            {
                "decl_id": decl.decl_id,
                "name": decl.name,
                "kind": decl.kind,
                "file_path": decl.file_path,
                "signature": sig[:300],
                "risk_score": round(decl.risk_score, 4),
                "tested": decl.tested,
                "exported": decl.exported,
                "lines_changed": decl.lines_changed,
            }
        )
    return result


def detect_changes_for_repo(
    db_path: Path,
    repo_root: Path,
    repo_id: str,
    ref_spec: str = "HEAD",
    *,
    path_prefix: str | None = None,
    top_n: int = 20,
) -> dict[str, Any]:
    """High-level entry: git diff → declarations → risk scores → minimal context."""
    try:
        hunks = get_diff_hunks(repo_root, ref_spec)
    except Exception as exc:
        return {"success": False, "error": str(exc), "ref_spec": ref_spec}

    with db_connection(db_path) as conn:
        affected = score_affected_declarations(conn, hunks, repo_id, path_prefix=path_prefix)
        context = get_minimal_context(affected, top_n=top_n)

    return {
        "success": True,
        "ref_spec": ref_spec,
        "repo_id": repo_id,
        "hunks_parsed": len(hunks),
        "declarations_affected": len(affected),
        "affected": [
            {
                "decl_id": d.decl_id,
                "name": d.name,
                "kind": d.kind,
                "file_path": d.file_path,
                "risk_score": round(d.risk_score, 4),
                "tested": d.tested,
                "exported": d.exported,
                "lines_changed": d.lines_changed,
                "in_degree": d.in_degree,
                "betweenness": d.betweenness,
            }
            for d in affected[:top_n]
        ],
        "minimal_context": context,
    }
