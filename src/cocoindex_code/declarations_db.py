"""SQLite storage for declarations, imports, and call references."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS declarations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    signature TEXT,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    exported BOOLEAN DEFAULT FALSE,
    parent_name TEXT,
    source TEXT NOT NULL DEFAULT 'treesitter'
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    module_path TEXT NOT NULL,
    imported_names TEXT,
    start_line INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS "references" (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    callee_name TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    context TEXT,
    source TEXT NOT NULL DEFAULT 'treesitter'
);

CREATE INDEX IF NOT EXISTS idx_decl_name ON declarations(name);
CREATE INDEX IF NOT EXISTS idx_decl_kind ON declarations(kind);
CREATE INDEX IF NOT EXISTS idx_decl_repo_file ON declarations(repo_id, file_path);
-- Partial index speeds up cross-repo callee resolution (exported public surface only).
CREATE INDEX IF NOT EXISTS idx_decl_name_exported ON declarations(name) WHERE exported = 1;
CREATE INDEX IF NOT EXISTS idx_imports_module ON imports(module_path);
CREATE INDEX IF NOT EXISTS idx_refs_callee ON "references"(callee_name);

CREATE TABLE IF NOT EXISTS file_signatures (
    repo_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    signature TEXT NOT NULL,
    last_seen INTEGER NOT NULL,
    PRIMARY KEY (repo_id, file_path)
);

CREATE INDEX IF NOT EXISTS idx_file_signatures_seen ON file_signatures(repo_id, last_seen);
CREATE INDEX IF NOT EXISTS idx_file_signatures_path ON file_signatures(file_path);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    caller_decl_id INTEGER,
    callee_decl_id INTEGER,
    line INTEGER NOT NULL,
    callee_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'treesitter',
    FOREIGN KEY (caller_decl_id) REFERENCES declarations(id) ON DELETE CASCADE,
    FOREIGN KEY (callee_decl_id) REFERENCES declarations(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_calls_callee_decl ON calls(callee_decl_id);
CREATE INDEX IF NOT EXISTS idx_calls_caller_decl ON calls(caller_decl_id);
CREATE INDEX IF NOT EXISTS idx_calls_repo_file ON calls(repo_id, file_path);

CREATE TABLE IF NOT EXISTS inherits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    subclass_decl_id INTEGER NOT NULL,
    superclass_decl_id INTEGER NOT NULL,
    line INTEGER NOT NULL,
    FOREIGN KEY (subclass_decl_id) REFERENCES declarations(id) ON DELETE CASCADE,
    FOREIGN KEY (superclass_decl_id) REFERENCES declarations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_inherits_super ON inherits(superclass_decl_id);
CREATE INDEX IF NOT EXISTS idx_inherits_sub ON inherits(subclass_decl_id);
CREATE INDEX IF NOT EXISTS idx_inherits_repo_file ON inherits(repo_id, file_path);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    started_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at);

CREATE TABLE IF NOT EXISTS tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL,
    test_file_path TEXT NOT NULL,
    tested_decl_id INTEGER NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    method TEXT NOT NULL DEFAULT 'filename',
    FOREIGN KEY (tested_decl_id) REFERENCES declarations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tests_tested_decl ON tests(tested_decl_id);
CREATE INDEX IF NOT EXISTS idx_tests_file ON tests(repo_id, test_file_path);

CREATE TABLE IF NOT EXISTS centrality (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL,
    decl_id INTEGER NOT NULL UNIQUE,
    betweenness REAL NOT NULL DEFAULT 0.0,
    in_degree INTEGER NOT NULL DEFAULT 0,
    out_degree INTEGER NOT NULL DEFAULT 0,
    computed_at INTEGER NOT NULL,
    FOREIGN KEY (decl_id) REFERENCES declarations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_centrality_repo ON centrality(repo_id);
CREATE INDEX IF NOT EXISTS idx_centrality_betweenness ON centrality(betweenness DESC);

CREATE TABLE IF NOT EXISTS communities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL,
    community_id INTEGER NOT NULL,
    decl_id INTEGER NOT NULL,
    level INTEGER NOT NULL DEFAULT 0,
    computed_at INTEGER NOT NULL,
    FOREIGN KEY (decl_id) REFERENCES declarations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_communities_repo ON communities(repo_id, community_id);
CREATE INDEX IF NOT EXISTS idx_communities_decl ON communities(decl_id);
"""


@dataclass
class Declaration:
    repo_id: str
    file_path: str
    kind: str
    name: str
    signature: str | None
    start_line: int
    end_line: int
    exported: bool
    parent_name: str | None = None
    source: str = "treesitter"


@dataclass
class ImportRecord:
    repo_id: str
    file_path: str
    module_path: str
    imported_names: str | None
    start_line: int


@dataclass
class ReferenceRecord:
    repo_id: str
    file_path: str
    callee_name: str
    start_line: int
    context: str | None
    source: str = "treesitter"


@dataclass
class InheritEdge:
    repo_id: str
    file_path: str
    subclass_name: str
    superclass_name: str
    line: int


@contextmanager
def db_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate_file_signature_scan_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(file_signatures)")}
    if "scan_mtime" not in cols:
        conn.execute("ALTER TABLE file_signatures ADD COLUMN scan_mtime INTEGER")
    if "scan_size" not in cols:
        conn.execute("ALTER TABLE file_signatures ADD COLUMN scan_size INTEGER")


def _migrate_calls_confidence(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(calls)")}
    if "confidence" not in cols:
        conn.execute("ALTER TABLE calls ADD COLUMN confidence REAL")
    if "confidence_tier" not in cols:
        conn.execute(
            "ALTER TABLE calls ADD COLUMN confidence_tier TEXT NOT NULL DEFAULT 'EXTRACTED'"
        )


def _migrate_inherits_confidence(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(inherits)")}
    if "confidence_tier" not in cols:
        conn.execute(
            "ALTER TABLE inherits ADD COLUMN confidence_tier TEXT NOT NULL DEFAULT 'EXTRACTED'"
        )


def _migrate_analytics_tables(conn: sqlite3.Connection) -> None:
    """Ensure analytics tables exist even on older DBs created before this version."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id TEXT NOT NULL,
            test_file_path TEXT NOT NULL,
            tested_decl_id INTEGER NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            method TEXT NOT NULL DEFAULT 'filename',
            FOREIGN KEY (tested_decl_id) REFERENCES declarations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_tests_tested_decl ON tests(tested_decl_id);
        CREATE INDEX IF NOT EXISTS idx_tests_file ON tests(repo_id, test_file_path);

        CREATE TABLE IF NOT EXISTS centrality (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id TEXT NOT NULL,
            decl_id INTEGER NOT NULL UNIQUE,
            betweenness REAL NOT NULL DEFAULT 0.0,
            in_degree INTEGER NOT NULL DEFAULT 0,
            out_degree INTEGER NOT NULL DEFAULT 0,
            computed_at INTEGER NOT NULL,
            FOREIGN KEY (decl_id) REFERENCES declarations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_centrality_repo ON centrality(repo_id);
        CREATE INDEX IF NOT EXISTS idx_centrality_betweenness ON centrality(betweenness DESC);

        CREATE TABLE IF NOT EXISTS communities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id TEXT NOT NULL,
            community_id INTEGER NOT NULL,
            decl_id INTEGER NOT NULL,
            level INTEGER NOT NULL DEFAULT 0,
            computed_at INTEGER NOT NULL,
            FOREIGN KEY (decl_id) REFERENCES declarations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_communities_repo ON communities(repo_id, community_id);
        CREATE INDEX IF NOT EXISTS idx_communities_decl ON communities(decl_id);
        """
    )


def _migrate_add_source_columns(conn: sqlite3.Connection) -> None:
    decl_cols = {row[1] for row in conn.execute("PRAGMA table_info(declarations)")}
    if "source" not in decl_cols:
        conn.execute(
            "ALTER TABLE declarations ADD COLUMN source TEXT NOT NULL DEFAULT 'treesitter'"
        )
    ref_cols = {row[1] for row in conn.execute('PRAGMA table_info("references")')}
    if "source" not in ref_cols:
        conn.execute(
            "ALTER TABLE \"references\" ADD COLUMN source TEXT NOT NULL DEFAULT 'treesitter'"
        )
    call_cols = {row[1] for row in conn.execute("PRAGMA table_info(calls)")}
    if "source" not in call_cols:
        conn.execute("ALTER TABLE calls ADD COLUMN source TEXT NOT NULL DEFAULT 'treesitter'")


def init_db(db_path_or_conn: Path | sqlite3.Connection) -> None:
    if isinstance(db_path_or_conn, sqlite3.Connection):
        db_path_or_conn.execute("PRAGMA foreign_keys = ON")
        db_path_or_conn.executescript(SCHEMA_SQL)
        db_path_or_conn.execute("PRAGMA journal_mode=WAL")
        _migrate_add_source_columns(db_path_or_conn)
        _migrate_file_signature_scan_columns(db_path_or_conn)
        _migrate_calls_confidence(db_path_or_conn)
        _migrate_inherits_confidence(db_path_or_conn)
        _migrate_analytics_tables(db_path_or_conn)
        return
    db_path_or_conn.parent.mkdir(parents=True, exist_ok=True)
    with db_connection(db_path_or_conn) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_SQL)
        conn.execute("PRAGMA journal_mode=WAL")
        _migrate_add_source_columns(conn)
        _migrate_file_signature_scan_columns(conn)
        _migrate_calls_confidence(conn)
        _migrate_inherits_confidence(conn)
        _migrate_analytics_tables(conn)


def reset_file_records(conn: sqlite3.Connection, repo_id: str, file_path: str) -> None:
    path = _normalize_path(file_path)
    conn.execute("DELETE FROM calls WHERE repo_id = ? AND file_path = ?", (repo_id, path))
    conn.execute("DELETE FROM inherits WHERE repo_id = ? AND file_path = ?", (repo_id, path))
    conn.execute("DELETE FROM tests WHERE repo_id = ? AND test_file_path = ?", (repo_id, path))
    conn.execute("DELETE FROM declarations WHERE repo_id = ? AND file_path = ?", (repo_id, path))
    conn.execute("DELETE FROM imports WHERE repo_id = ? AND file_path = ?", (repo_id, path))
    conn.execute('DELETE FROM "references" WHERE repo_id = ? AND file_path = ?', (repo_id, path))


def insert_declarations(conn: sqlite3.Connection, records: Iterable[Declaration]) -> None:
    conn.executemany(
        """
        INSERT INTO declarations
            (
                repo_id, file_path, kind, name, signature,
                start_line, end_line, exported, parent_name, source
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r.repo_id,
                _normalize_path(r.file_path),
                r.kind,
                r.name,
                r.signature,
                r.start_line,
                r.end_line,
                1 if r.exported else 0,
                r.parent_name,
                r.source,
            )
            for r in records
        ],
    )


def insert_imports(conn: sqlite3.Connection, records: Iterable[ImportRecord]) -> None:
    conn.executemany(
        """
        INSERT INTO imports
            (repo_id, file_path, module_path, imported_names, start_line)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                r.repo_id,
                _normalize_path(r.file_path),
                r.module_path,
                r.imported_names,
                r.start_line,
            )
            for r in records
        ],
    )


def insert_references(conn: sqlite3.Connection, records: Iterable[ReferenceRecord]) -> None:
    conn.executemany(
        """
        INSERT INTO "references"
            (repo_id, file_path, callee_name, start_line, context, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r.repo_id,
                _normalize_path(r.file_path),
                r.callee_name,
                r.start_line,
                r.context,
                r.source,
            )
            for r in records
        ],
    )


def set_file_signature(
    conn: sqlite3.Connection,
    repo_id: str,
    file_path: str,
    signature: str,
    last_seen: int,
    *,
    scan_mtime: int | None = None,
    scan_size: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO file_signatures
            (repo_id, file_path, signature, last_seen, scan_mtime, scan_size)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_id, file_path) DO UPDATE SET
          signature = excluded.signature,
          last_seen = excluded.last_seen,
          scan_mtime = excluded.scan_mtime,
          scan_size = excluded.scan_size
        """,
        (repo_id, _normalize_path(file_path), signature, last_seen, scan_mtime, scan_size),
    )


def get_file_signature(conn: sqlite3.Connection, repo_id: str, file_path: str) -> str | None:
    row = conn.execute(
        "SELECT signature FROM file_signatures WHERE repo_id = ? AND file_path = ?",
        (repo_id, _normalize_path(file_path)),
    ).fetchone()
    if row is None:
        return None
    value = row[0]
    return str(value) if isinstance(value, str) else None


def get_file_signature_scan(
    conn: sqlite3.Connection, repo_id: str, file_path: str
) -> tuple[str | None, int | None, int | None]:
    """Return ``(signature, scan_mtime, scan_size)``; missing row → ``(None, None, None)``."""
    row = conn.execute(
        """
        SELECT signature, scan_mtime, scan_size
        FROM file_signatures
        WHERE repo_id = ? AND file_path = ?
        """,
        (repo_id, _normalize_path(file_path)),
    ).fetchone()
    if row is None:
        return (None, None, None)
    sig = row[0]
    sig_s = str(sig) if isinstance(sig, str) else None
    mt = row[1]
    sz = row[2]
    mt_i = int(mt) if mt is not None else None
    sz_i = int(sz) if sz is not None else None
    return (sig_s, mt_i, sz_i)


def replace_file(conn: sqlite3.Connection, repo_id: str, file_path: str) -> None:
    reset_file_records(conn, repo_id, file_path)


def finalize_signature_cleanup(conn: sqlite3.Connection, repo_id: str, run_id: int) -> None:
    for table in ("calls", "inherits", "declarations", "imports", '"references"'):
        conn.execute(
            f"""
            DELETE FROM {table}
            WHERE repo_id = ?
              AND file_path NOT IN (
                SELECT file_path FROM file_signatures
                WHERE repo_id = ? AND last_seen = ?
              )
            """,
            (repo_id, repo_id, run_id),
        )
    conn.execute(
        """
        DELETE FROM file_signatures
        WHERE repo_id = ?
          AND last_seen != ?
        """,
        (repo_id, run_id),
    )


def query_declarations(
    conn: sqlite3.Connection,
    name_pattern: str | None = None,
    kind: str | None = None,
    path_prefix: str | None = None,
    repo_id: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM declarations WHERE 1=1"
    params: list[Any] = []
    if name_pattern:
        sql += " AND name LIKE ? ESCAPE '\\'"
        params.append(f"%{_escape_like(name_pattern)}%")
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if path_prefix:
        sql += " AND file_path LIKE ? ESCAPE '\\'"
        params.append(f"{_escape_like(_normalize_path(path_prefix))}%")
    if repo_id:
        sql += " AND repo_id = ?"
        params.append(repo_id)
    sql += " ORDER BY file_path, start_line LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def query_references(
    conn: sqlite3.Connection,
    callee_name: str,
    path_prefix: str | None = None,
    repo_id: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM \"references\" WHERE callee_name LIKE ? ESCAPE '\\'"
    params: list[Any] = [f"%{_escape_like(callee_name)}%"]
    if path_prefix:
        sql += " AND file_path LIKE ? ESCAPE '\\'"
        params.append(f"{_escape_like(_normalize_path(path_prefix))}%")
    if repo_id:
        sql += " AND repo_id = ?"
        params.append(repo_id)
    sql += " ORDER BY file_path, start_line LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def query_imports(
    conn: sqlite3.Connection,
    module_path: str,
    repo_id: str | None = None,
    imported_name: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM imports WHERE module_path LIKE ? ESCAPE '\\'"
    params: list[Any] = [f"%{_escape_like(module_path)}%"]
    if repo_id:
        sql += " AND repo_id = ?"
        params.append(repo_id)
    if imported_name:
        sql += " AND imported_names LIKE ? ESCAPE '\\'"
        params.append(f"%{_escape_like(imported_name)}%")
    sql += " ORDER BY file_path, start_line LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def query_caller_files(
    conn: sqlite3.Connection,
    repo_id: str,
    file_path: str,
) -> set[str]:
    """Return files (excluding ``file_path``) that reference any declaration
    declared inside ``file_path``. Used to compute the "affected closure" for
    incremental subset reindexing: when a file changes, its callers may also
    need AST re-extraction so their reference rows stay consistent.
    """
    path = _normalize_path(file_path)
    rows = conn.execute(
        """
        SELECT DISTINCT r.file_path
        FROM "references" AS r
        JOIN declarations AS d
          ON d.repo_id = r.repo_id
         AND d.name = r.callee_name
        WHERE d.repo_id = ?
          AND d.file_path = ?
          AND r.file_path != ?
        """,
        (repo_id, path, path),
    ).fetchall()
    return {str(row[0]) for row in rows if row[0]}


def query_inheritor_files(
    conn: sqlite3.Connection,
    repo_id: str,
    file_path: str,
) -> set[str]:
    """Return files that declare subclasses inheriting from a class in ``file_path``."""
    path = _normalize_path(file_path)
    rows = conn.execute(
        """
        SELECT DISTINCT sub.file_path
        FROM inherits AS ih
        JOIN declarations AS sup ON sup.id = ih.superclass_decl_id
        JOIN declarations AS sub ON sub.id = ih.subclass_decl_id
        WHERE sup.repo_id = ?
          AND sup.file_path = ?
          AND sub.file_path != ?
        """,
        (repo_id, path, path),
    ).fetchall()
    return {str(row[0]) for row in rows if row[0]}


def delete_file_records(conn: sqlite3.Connection, repo_id: str, file_path: str) -> None:
    """Remove declarations/imports/references and the signature row for a file.
    Used when a file is deleted in an incremental subset reindex."""
    path = _normalize_path(file_path)
    reset_file_records(conn, repo_id, path)
    conn.execute(
        "DELETE FROM file_signatures WHERE repo_id = ? AND file_path = ?",
        (repo_id, path),
    )


def query_file_exports(
    conn: sqlite3.Connection,
    path_prefix: str,
    repo_id: str | None = None,
    kind: str | None = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM declarations WHERE file_path LIKE ? ESCAPE '\\'"
    params: list[Any] = [f"{_escape_like(_normalize_path(path_prefix))}%"]
    if repo_id:
        sql += " AND repo_id = ?"
        params.append(repo_id)
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " AND exported = 1 ORDER BY file_path, start_line LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def rebuild_calls_for_file(conn: sqlite3.Connection, repo_id: str, file_path: str) -> int:
    """Compatibility wrapper around the graph call-edge builder."""
    from .declarations_graph import rebuild_calls_for_file as _rebuild_calls_for_file

    return _rebuild_calls_for_file(conn, repo_id, file_path)


def rebuild_inherits_for_file(
    conn: sqlite3.Connection,
    repo_id: str,
    file_path: str,
    edges: Iterable[InheritEdge] = (),
) -> int:
    """Compatibility wrapper around the graph inheritance-edge builder."""
    from .declarations_graph import rebuild_inherits_for_file as _rebuild_inherits_for_file

    return _rebuild_inherits_for_file(conn, repo_id, file_path, edges)


@dataclass
class TestEdge:
    __test__ = False  # not a pytest test class
    repo_id: str
    test_file_path: str
    tested_decl_id: int
    confidence: float = 1.0
    method: str = "filename"


def insert_test_edges(conn: sqlite3.Connection, records: Iterable[TestEdge]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO tests
            (repo_id, test_file_path, tested_decl_id, confidence, method)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                r.repo_id,
                _normalize_path(r.test_file_path),
                r.tested_decl_id,
                r.confidence,
                r.method,
            )
            for r in records
        ],
    )


def query_tested_decl_ids(
    conn: sqlite3.Connection,
    repo_id: str | None = None,
) -> set[int]:
    """Return the set of declaration IDs that have at least one test edge."""
    sql = "SELECT DISTINCT tested_decl_id FROM tests"
    params: list[Any] = []
    if repo_id:
        sql += " WHERE repo_id = ?"
        params.append(repo_id)
    rows = conn.execute(sql, params).fetchall()
    return {int(r[0]) for r in rows}


def query_tests_for_decl(
    conn: sqlite3.Connection,
    decl_id: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM tests WHERE tested_decl_id = ?",
        (decl_id,),
    ).fetchall()


def finalize_signature_cleanup_with_analytics(
    conn: sqlite3.Connection, repo_id: str, run_id: int
) -> None:
    """Like finalize_signature_cleanup but also cleans tests rows for removed files."""
    for table in ("calls", "inherits", "declarations", "imports", '"references"'):
        conn.execute(
            f"""
            DELETE FROM {table}
            WHERE repo_id = ?
              AND file_path NOT IN (
                SELECT file_path FROM file_signatures
                WHERE repo_id = ? AND last_seen = ?
              )
            """,
            (repo_id, repo_id, run_id),
        )
    conn.execute(
        """
        DELETE FROM tests
        WHERE repo_id = ?
          AND test_file_path NOT IN (
            SELECT file_path FROM file_signatures
            WHERE repo_id = ? AND last_seen = ?
          )
        """,
        (repo_id, repo_id, run_id),
    )
    conn.execute(
        """
        DELETE FROM file_signatures
        WHERE repo_id = ?
          AND last_seen != ?
        """,
        (repo_id, run_id),
    )
