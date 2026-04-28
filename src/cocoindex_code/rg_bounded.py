"""Bounded ripgrep for CocoIndex Code: ``rg --json`` streaming, unified-root only."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_PATTERN_LEN = 512
MAX_GLOB_LEN = 120

_RG_EXE: str | None | bool = False


def _rg_binary() -> str | None:
    """Resolve ``rg`` once per process (``COCOINDEX_RG_PATH`` overrides ``PATH``)."""
    global _RG_EXE
    if _RG_EXE is False:
        override = os.environ.get("COCOINDEX_RG_PATH", "").strip()
        _RG_EXE = override or shutil.which("rg") or None
    return _RG_EXE if isinstance(_RG_EXE, str) else None


def reset_rg_binary_cache_for_tests() -> None:
    global _RG_EXE
    _RG_EXE = False


def resolve_rg_paths(root: Path, path_prefix: str | None) -> tuple[Path, list[str]]:
    """Return cwd (unified root) and path arguments for ``rg`` (never escapes ``root``)."""
    root = root.resolve()
    if not path_prefix or not path_prefix.strip():
        return root, ["."]

    rel = path_prefix.strip().replace("\\", "/").lstrip("/")
    segments = [s for s in rel.split("/") if s != ""]
    if any(s == ".." for s in segments):
        raise ValueError("path_prefix must not contain '..'")

    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("path_prefix escapes unified root") from exc
    if not target.exists():
        raise ValueError(f"path_prefix does not exist: {path_prefix}")

    rel_arg = target.relative_to(root).as_posix()
    return root, [rel_arg]


def _path_text(data: dict[str, Any]) -> str:
    p = data.get("path")
    if isinstance(p, dict):
        return str(p.get("text", ""))
    if isinstance(p, str):
        return p
    return ""


def _line_text(data: dict[str, Any]) -> str:
    lines = data.get("lines")
    if isinstance(lines, dict):
        return str(lines.get("text", "")).rstrip("\n\r")
    return ""


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=4.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2.0)


def _append_match(matches: list[dict[str, Any]], data: dict[str, Any]) -> None:
    path_text = _path_text(data)
    content = _line_text(data)
    try:
        line_no = int(data["line_number"])
    except (KeyError, TypeError, ValueError):
        return
    matches.append(
        {
            "file_path": path_text,
            "line": line_no,
            "text": content,
            "start_line": line_no,
            "end_line": line_no,
            "content": content,
            "score": 1.0,
        }
    )


def run_bounded_rg(
    root: Path,
    pattern: str,
    *,
    path_prefix: str | None = None,
    glob: str | None = None,
    fixed_strings: bool = True,
    max_matches: int = 200,
    per_file_cap: int = 40,
    wall_timeout_s: float = 25.0,
) -> dict[str, Any]:
    """Stream ``rg --json`` with a hard wall-clock limit."""
    logger.debug(
        "Running bounded ripgrep: pattern=%s..., root=%s, path_prefix=%s",
        pattern[:50],
        root,
        path_prefix,
    )

    if len(pattern) > MAX_PATTERN_LEN:
        logger.warning(f"Pattern exceeds {MAX_PATTERN_LEN} characters")
        return {
            "success": False,
            "error": f"pattern exceeds {MAX_PATTERN_LEN} characters",
            "matches": [],
        }
    if glob is not None:
        g = glob.strip()
        if len(g) > MAX_GLOB_LEN:
            logger.warning(f"Glob exceeds {MAX_GLOB_LEN} characters")
            return {
                "success": False,
                "error": f"glob exceeds {MAX_GLOB_LEN} characters",
                "matches": [],
            }
        if ".." in g.replace("\\", "/"):
            return {"success": False, "error": "glob must not contain '..'", "matches": []}

    try:
        cwd, path_args = resolve_rg_paths(root, path_prefix)
    except ValueError as exc:
        logger.error(f"Invalid rg paths: {exc}")
        return {"success": False, "error": str(exc), "matches": []}

    rg = _rg_binary()
    if not rg:
        logger.error("ripgrep (rg) not found")
        return {
            "success": False,
            "error": "ripgrep (rg) not found; set COCOINDEX_RG_PATH or install ripgrep",
            "matches": [],
        }

    cmd: list[str] = [
        rg,
        "--json",
        "--max-count",
        str(per_file_cap),
        "--max-filesize",
        "4M",
        "--hidden",
        "--glob",
        "!.git/*",
    ]
    threads = os.environ.get("COCOINDEX_RG_THREADS", "").strip()
    if threads.isdigit() and int(threads) > 0:
        cmd.extend(["--threads", threads])

    if fixed_strings:
        cmd.append("--fixed-strings")
    if glob:
        cmd.extend(["--glob", glob])
    cmd.append(pattern)
    cmd.extend(path_args)

    matches: list[dict[str, Any]] = []
    truncated = False

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return {"success": False, "error": str(exc), "matches": []}

    timer: threading.Timer | None = None
    try:
        assert proc.stdout is not None
        timer = threading.Timer(wall_timeout_s, lambda: _terminate_process(proc))
        timer.daemon = True
        timer.start()

        for line in proc.stdout:
            if len(matches) >= max_matches:
                truncated = True
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "match":
                continue
            data = obj.get("data")
            if not isinstance(data, dict):
                continue
            _append_match(matches, data)

        if len(matches) >= max_matches:
            truncated = True

    finally:
        if timer is not None:
            timer.cancel()
        if proc.poll() is None:
            _terminate_process(proc)
        try:
            proc.stdout.close()
        except Exception:
            pass
    code = proc.returncode if proc.returncode is not None else -1
    if code not in (0, 1) and not matches:
        return {
            "success": False,
            "error": f"rg failed (exit {code})",
            "matches": [],
        }

    results = [
        {
            "file_path": m["file_path"],
            "language": None,
            "content": m["content"],
            "start_line": m["start_line"],
            "end_line": m["end_line"],
            "score": m["score"],
            "hit_source": "rg",
        }
        for m in matches
    ]

    return {
        "success": True,
        "matches": matches,
        "results": results,
        "truncated": truncated,
        "rg_returncode": code,
    }


def rg_fallback_for_keyword(
    root: Path,
    query: str,
    *,
    path_prefix: str | None,
    limit: int,
) -> dict[str, Any] | None:
    """Run bounded rg for a keyword-style query; returns None if rg unavailable or failed."""
    out = run_bounded_rg(
        root,
        query,
        path_prefix=path_prefix,
        fixed_strings=True,
        max_matches=max(limit, 20),
        per_file_cap=min(80, max(20, limit * 2)),
    )
    if not out.get("success"):
        return None
    return out
