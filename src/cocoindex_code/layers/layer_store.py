from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .layer import Layer
from .layer_kind import LayerKind
from .layer_manifest import LayerManifest
from .layer_paths import LayerPaths


class LayerStore:
    """Persistent daemon metadata store for Git overlay control-plane state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.state_dir = path.parent
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );

                CREATE TABLE IF NOT EXISTS repositories (
                    repo_id TEXT PRIMARY KEY,
                    repo_name TEXT NOT NULL,
                    remote_url TEXT NOT NULL,
                    normalized_remote_url TEXT NOT NULL,
                    repo_relative_root TEXT NOT NULL,
                    last_seen_root TEXT NOT NULL,
                    last_seen_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS worktrees (
                    worktree_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    worktree_name TEXT NOT NULL,
                    branch_name TEXT NOT NULL,
                    last_seen_path TEXT NOT NULL,
                    last_seen_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS overlay_policies (
                    repo_id TEXT PRIMARY KEY,
                    base_ref TEXT NOT NULL,
                    dirty_enabled INTEGER NOT NULL,
                    environment_strategy TEXT NOT NULL,
                    branch_ttl_seconds REAL NOT NULL,
                    dirty_ttl_seconds REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS layers (
                    layer_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    ref_name TEXT,
                    commit_sha TEXT,
                    base_commit TEXT,
                    merge_base TEXT,
                    base_layer_id TEXT,
                    worktree_id TEXT,
                    config_hash TEXT,
                    source_dir TEXT NOT NULL,
                    db_dir TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_accessed_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS overlay_manifests (
                    layer_id TEXT PRIMARY KEY,
                    affected_paths_json TEXT NOT NULL,
                    tombstoned_paths_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL
                );
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(layers)").fetchall()}
            if "merge_base" not in columns:
                conn.execute("ALTER TABLE layers ADD COLUMN merge_base TEXT")
            if "worktree_id" not in columns:
                conn.execute("ALTER TABLE layers ADD COLUMN worktree_id TEXT")
            if "config_hash" not in columns:
                conn.execute("ALTER TABLE layers ADD COLUMN config_hash TEXT")
            conn.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (1)")

    def upsert_overlay_policy(
        self,
        *,
        repo_id: str,
        base_ref: str,
        dirty_enabled: bool = True,
        environment_strategy: str = "per-layer",
        branch_ttl_seconds: float = 14 * 24 * 60 * 60,
        dirty_ttl_seconds: float = 24 * 60 * 60,
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO overlay_policies (
                    repo_id, base_ref, dirty_enabled, environment_strategy,
                    branch_ttl_seconds, dirty_ttl_seconds, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id) DO UPDATE SET
                    base_ref=excluded.base_ref,
                    dirty_enabled=excluded.dirty_enabled,
                    environment_strategy=excluded.environment_strategy,
                    branch_ttl_seconds=excluded.branch_ttl_seconds,
                    dirty_ttl_seconds=excluded.dirty_ttl_seconds,
                    updated_at=excluded.updated_at
                """,
                (
                    repo_id,
                    base_ref,
                    1 if dirty_enabled else 0,
                    environment_strategy,
                    branch_ttl_seconds,
                    dirty_ttl_seconds,
                    now,
                ),
            )

    def get_overlay_base_ref(self, repo_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT base_ref FROM overlay_policies WHERE repo_id = ?", (repo_id,)
            ).fetchone()
        return row["base_ref"] if row is not None else None

    def upsert_repository(
        self,
        *,
        repo_id: str,
        repo_name: str,
        remote_url: str,
        normalized_remote_url: str,
        repo_relative_root: str,
        last_seen_root: Path,
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO repositories (
                    repo_id, repo_name, remote_url, normalized_remote_url,
                    repo_relative_root, last_seen_root, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id) DO UPDATE SET
                    repo_name=excluded.repo_name,
                    remote_url=excluded.remote_url,
                    normalized_remote_url=excluded.normalized_remote_url,
                    repo_relative_root=excluded.repo_relative_root,
                    last_seen_root=excluded.last_seen_root,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    repo_id,
                    repo_name,
                    remote_url,
                    normalized_remote_url,
                    repo_relative_root,
                    str(last_seen_root),
                    now,
                ),
            )

    def upsert_worktree(
        self,
        *,
        worktree_id: str,
        repo_id: str,
        worktree_name: str,
        branch_name: str,
        last_seen_path: Path,
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO worktrees (
                    worktree_id, repo_id, worktree_name, branch_name,
                    last_seen_path, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(worktree_id) DO UPDATE SET
                    repo_id=excluded.repo_id,
                    worktree_name=excluded.worktree_name,
                    branch_name=excluded.branch_name,
                    last_seen_path=excluded.last_seen_path,
                    last_seen_at=excluded.last_seen_at
                """,
                (worktree_id, repo_id, worktree_name, branch_name, str(last_seen_path), now),
            )

    def _row_to_layer(self, row: sqlite3.Row) -> Layer:
        db_dir = Path(row["db_dir"])
        manifest = self.get_manifest(row["layer_id"])
        return Layer(
            id=row["layer_id"],
            repo_id=row["repo_id"],
            kind=LayerKind(row["kind"]),
            paths=LayerPaths(
                root=Path(row["source_dir"]).parent,
                source=Path(row["source_dir"]),
                cocoindex_db=db_dir / "cocoindex.db",
                target_sqlite=db_dir / "target_sqlite.db",
            ),
            manifest=manifest,
            ref_name=row["ref_name"],
            commit_hash=row["commit_sha"],
            base_commit_hash=row["base_commit"],
            merge_base_hash=row["merge_base"],
            base_layer_id=row["base_layer_id"],
            worktree_id=row["worktree_id"],
            config_hash=row["config_hash"],
            status=row["status"],
            created_at=row["created_at"],
            last_accessed_at=row["last_accessed_at"],
        )

    def upsert_layer(
        self,
        *,
        layer_id: str,
        repo_id: str,
        kind: LayerKind,
        ref_name: str | None,
        commit: str | None,
        base_commit: str | None,
        base_layer_id: str | None,
        source_dir: Path,
        db_dir: Path,
        status: str,
        merge_base: str | None = None,
        worktree_id: str | None = None,
        config_hash: str | None = None,
    ) -> Layer:
        now = time.time()
        existing = self.get_layer(layer_id)
        created_at = existing.created_at if existing is not None else now
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO layers (
                    layer_id, repo_id, kind, ref_name, commit_sha, base_commit,
                    merge_base, base_layer_id, worktree_id, config_hash,
                    source_dir, db_dir, status, created_at, last_accessed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(layer_id) DO UPDATE SET
                    repo_id=excluded.repo_id,
                    kind=excluded.kind,
                    ref_name=excluded.ref_name,
                    commit_sha=excluded.commit_sha,
                    base_commit=excluded.base_commit,
                    merge_base=excluded.merge_base,
                    base_layer_id=excluded.base_layer_id,
                    worktree_id=excluded.worktree_id,
                    config_hash=excluded.config_hash,
                    source_dir=excluded.source_dir,
                    db_dir=excluded.db_dir,
                    status=excluded.status,
                    last_accessed_at=excluded.last_accessed_at
                """,
                (
                    layer_id,
                    repo_id,
                    kind.value,
                    ref_name,
                    commit,
                    base_commit,
                    merge_base,
                    base_layer_id,
                    worktree_id,
                    config_hash,
                    str(source_dir),
                    str(db_dir),
                    status,
                    created_at,
                    now,
                ),
            )
        record = self.get_layer(layer_id)
        assert record is not None
        return record

    def get_layer(self, layer_id: str) -> Layer | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM layers WHERE layer_id = ?", (layer_id,)).fetchone()
        return self._row_to_layer(row) if row is not None else None

    def mark_layer_ready(self, layer_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE layers SET status = 'ready', last_accessed_at = ? WHERE layer_id = ?",
                (time.time(), layer_id),
            )

    def touch_layer(self, layer_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE layers SET last_accessed_at = ? WHERE layer_id = ?",
                (time.time(), layer_id),
            )

    def replace_manifest(
        self,
        layer_id: str,
        *,
        affected_paths: list[str] | tuple[str, ...],
        tombstoned_paths: list[str] | tuple[str, ...],
        expires_at: float | None,
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO overlay_manifests (
                    layer_id, affected_paths_json, tombstoned_paths_json,
                    created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(layer_id) DO UPDATE SET
                    affected_paths_json=excluded.affected_paths_json,
                    tombstoned_paths_json=excluded.tombstoned_paths_json,
                    expires_at=excluded.expires_at
                """,
                (
                    layer_id,
                    json.dumps(sorted(set(affected_paths))),
                    json.dumps(sorted(set(tombstoned_paths))),
                    now,
                    expires_at,
                ),
            )

    def get_manifest(self, layer_id: str) -> LayerManifest | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM overlay_manifests WHERE layer_id = ?", (layer_id,)
            ).fetchone()
        if row is None:
            return None
        return LayerManifest(
            affected_paths=frozenset(json.loads(row["affected_paths_json"])),
            tombstoned_paths=frozenset(json.loads(row["tombstoned_paths_json"])),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def list_layers(self, *, repo_id: str | None = None) -> list[Layer]:
        with self._connect() as conn:
            if repo_id is None:
                rows = conn.execute("SELECT * FROM layers ORDER BY created_at").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM layers WHERE repo_id = ? ORDER BY created_at",
                    (repo_id,),
                ).fetchall()
        return [self._row_to_layer(row) for row in rows]

    def list_expired_layers(self, now: float | None = None) -> list[Layer]:
        cutoff = time.time() if now is None else now
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT layers.*
                FROM layers
                JOIN overlay_manifests USING(layer_id)
                WHERE overlay_manifests.expires_at IS NOT NULL
                  AND overlay_manifests.expires_at < ?
                """,
                (cutoff,),
            ).fetchall()
        return [self._row_to_layer(row) for row in rows]

    def delete_layers(self, layer_ids: list[str] | tuple[str, ...]) -> None:
        if not layer_ids:
            return
        with self._connect() as conn:
            conn.executemany(
                "DELETE FROM overlay_manifests WHERE layer_id = ?", [(i,) for i in layer_ids]
            )
            conn.executemany("DELETE FROM layers WHERE layer_id = ?", [(i,) for i in layer_ids])

    def prune_expired(self, now: float | None = None) -> list[Layer]:
        layers = self.list_expired_layers(now)
        self.delete_layers(tuple(layer.id for layer in layers))
        return layers


LayerRecord = Layer
OverlayManifest = LayerManifest
