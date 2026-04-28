"""Lightweight native declaration and reference indexing for code graph tools."""

from __future__ import annotations

import ast
import bisect
import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .declarations_db import (
    Declaration,
    ImportRecord,
    InheritEdge,
    ReferenceRecord,
    db_connection,
    delete_file_records,
    finalize_signature_cleanup_with_analytics,
    init_db,
    insert_declarations,
    insert_imports,
    insert_references,
    rebuild_calls_for_file,
    rebuild_inherits_for_file,
    replace_file,
    set_file_signature,
)

_CODE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
_SKIP_DIRS = {
    ".git",
    ".cocoindex_code",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
_DECL_RE = re.compile(
    r"^\s*(?P<export>export\s+)?(?:(?:async\s+)?function|class|interface|type)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_CONST_RE = re.compile(
    r"^\s*(?P<export>export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=",
    re.MULTILINE,
)
_CALL_RE = re.compile(r"\b(?P<name>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*\(")
_EXTENDS_RE = re.compile(
    r"^\s*(?:export\s+)?(?:class|interface)\s+"
    r"(?P<sub>[A-Za-z_$][\w$]*)\s+extends\s+(?P<sup>[A-Za-z_$][\w$.]*)",
    re.MULTILINE,
)
_IMPORT_RE = re.compile(r"^\s*import\s+.*?\s+from\s+['\"](?P<module>[^'\"]+)['\"]", re.MULTILINE)
_CALL_SKIP = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "function",
    "return",
    "typeof",
    "sizeof",
    "new",
}


@dataclass
class _ParsedFile:
    declarations: list[Declaration] = field(default_factory=list)
    imports: list[ImportRecord] = field(default_factory=list)
    references: list[ReferenceRecord] = field(default_factory=list)
    inherits: list[InheritEdge] = field(default_factory=list)


def _iter_code_files(root: Path, changed_paths: list[str] | None = None) -> list[Path]:
    if changed_paths:
        files = []
        for rel in changed_paths:
            path = (root / rel).resolve()
            try:
                path.relative_to(root.resolve())
            except ValueError:
                continue
            if path.is_file() and path.suffix.lower() in _CODE_EXTENSIONS:
                files.append(path)
        return sorted(set(files))

    files: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [dirname for dirname in dirnames if dirname not in _SKIP_DIRS]
        for filename in filenames:
            path = Path(current_root) / filename
            if path.suffix.lower() in _CODE_EXTENSIONS:
                files.append(path)
    return files


def _iter_deleted_code_paths(root: Path, changed_paths: list[str] | None = None) -> list[Path]:
    if not changed_paths:
        return []
    deleted: list[Path] = []
    resolved_root = root.resolve()
    for rel in changed_paths:
        path = (root / rel).resolve()
        try:
            path.relative_to(resolved_root)
        except ValueError:
            continue
        if not path.exists() and path.suffix.lower() in _CODE_EXTENSIONS:
            deleted.append(path)
    return sorted(set(deleted))


def _repo_and_file(root: Path, path: Path, default_repo_id: str) -> tuple[str, str]:
    rel = path.relative_to(root).as_posix()
    parts = rel.split("/", 1)
    if default_repo_id == "auto" and len(parts) == 2:
        return parts[0], parts[1]
    return default_repo_id, rel


def _file_signature(path: Path) -> str:
    stat = path.stat()
    digest = hashlib.sha256()
    digest.update(str(stat.st_size).encode())
    digest.update(str(stat.st_mtime_ns).encode())
    return digest.hexdigest()


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _parse_python(repo_id: str, file_path: str, text: str) -> _ParsedFile:
    parsed = _ParsedFile()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return parsed

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            parsed.declarations.append(
                Declaration(
                    repo_id=repo_id,
                    file_path=file_path,
                    kind=kind,
                    name=node.name,
                    signature=None,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    exported=not node.name.startswith("_"),
                    source="native",
                )
            )
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    name = _call_name(base)
                    if name:
                        parsed.inherits.append(
                            InheritEdge(repo_id, file_path, node.name, name, node.lineno)
                        )
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            names = [alias.name for alias in node.names]
            parsed.imports.append(
                ImportRecord(
                    repo_id=repo_id,
                    file_path=file_path,
                    module_path=module or ",".join(names),
                    imported_names=",".join(names),
                    start_line=node.lineno,
                )
            )
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name:
                parsed.references.append(
                    ReferenceRecord(repo_id, file_path, name, node.lineno, None, source="native")
                )
    return parsed


def _line_starts(text: str) -> list[int]:
    return [0, *(match.end() for match in re.finditer("\n", text))]


def _line_for_offset(line_starts: list[int], offset: int) -> int:
    return bisect.bisect_right(line_starts, offset)


def _parse_script(repo_id: str, file_path: str, text: str) -> _ParsedFile:
    parsed = _ParsedFile()
    line_starts = _line_starts(text)
    for match in _DECL_RE.finditer(text):
        raw = match.group(0)
        first = raw.strip().split()[0]
        kind = "function"
        if "class " in raw:
            kind = "class"
        elif "interface " in raw:
            kind = "interface"
        elif "type " in raw:
            kind = "type"
        name = match.group("name")
        line = _line_for_offset(line_starts, match.start())
        parsed.declarations.append(
            Declaration(
                repo_id,
                file_path,
                kind,
                name,
                None,
                line,
                line,
                bool(match.group("export")) or first == "export",
                source="native",
            )
        )
    for match in _CONST_RE.finditer(text):
        name = match.group("name")
        line = _line_for_offset(line_starts, match.start())
        parsed.declarations.append(
            Declaration(
                repo_id,
                file_path,
                "variable",
                name,
                None,
                line,
                line,
                bool(match.group("export")),
                source="native",
            )
        )
    for match in _EXTENDS_RE.finditer(text):
        parsed.inherits.append(
            InheritEdge(
                repo_id,
                file_path,
                match.group("sub"),
                match.group("sup").split(".")[-1],
                _line_for_offset(line_starts, match.start()),
            )
        )
    for match in _IMPORT_RE.finditer(text):
        parsed.imports.append(
            ImportRecord(
                repo_id,
                file_path,
                match.group("module"),
                None,
                _line_for_offset(line_starts, match.start()),
            )
        )
    for match in _CALL_RE.finditer(text):
        name = match.group("name")
        if name.split(".", 1)[0] in _CALL_SKIP:
            continue
        parsed.references.append(
            ReferenceRecord(
                repo_id,
                file_path,
                name,
                _line_for_offset(line_starts, match.start()),
                None,
                source="native",
            )
        )
    return parsed


def index_code_declarations(
    project_root: Path,
    db_path: Path,
    *,
    repo_id: str = "local",
    changed_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Populate declaration, reference, import, and graph tables from source files."""
    root = project_root.resolve()
    init_db(db_path)
    files = _iter_code_files(root, changed_paths)
    run_id = time.time_ns()
    stats = {
        "files": 0,
        "deleted_files": 0,
        "declarations": 0,
        "references": 0,
        "imports": 0,
        "inherits": 0,
    }

    with db_connection(db_path) as conn:
        touched_repo_ids: set[str] = set()
        for path in _iter_deleted_code_paths(root, changed_paths):
            resolved_repo_id, file_path = _repo_and_file(root, path, repo_id)
            delete_file_records(conn, resolved_repo_id, file_path)
            stats["deleted_files"] += 1
        for path in files:
            resolved_repo_id, file_path = _repo_and_file(root, path, repo_id)
            touched_repo_ids.add(resolved_repo_id)
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
                stat = path.stat()
            except OSError:
                continue
            parsed = (
                _parse_python(resolved_repo_id, file_path, text)
                if path.suffix.lower() == ".py"
                else _parse_script(resolved_repo_id, file_path, text)
            )
            replace_file(conn, resolved_repo_id, file_path)
            insert_declarations(conn, parsed.declarations)
            insert_imports(conn, parsed.imports)
            insert_references(conn, parsed.references)
            rebuild_calls_for_file(conn, resolved_repo_id, file_path)
            rebuild_inherits_for_file(conn, resolved_repo_id, file_path, parsed.inherits)
            set_file_signature(
                conn,
                resolved_repo_id,
                file_path,
                _file_signature(path),
                run_id,
                scan_mtime=stat.st_mtime_ns,
                scan_size=stat.st_size,
            )
            stats["files"] += 1
            stats["declarations"] += len(parsed.declarations)
            stats["references"] += len(parsed.references)
            stats["imports"] += len(parsed.imports)
            stats["inherits"] += len(parsed.inherits)
        if changed_paths is None:
            for touched_repo_id in touched_repo_ids:
                finalize_signature_cleanup_with_analytics(conn, touched_repo_id, run_id)

    return {"success": True, "repo_id": repo_id, **stats}
