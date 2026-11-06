"""Embedding compatibility contract for experimental shared SQLite snapshots.

This is an application-owned table, separate from CocoIndex's incremental state.
Only a new index can acquire provenance; an existing unlabelled index must never
be certified just because an incremental update completed.
"""

from __future__ import annotations

import json
import sqlite3

from cocoindex.connectors.sqlite import ManagedConnection

from .embedder_params import resolve_embedder_params
from .settings import EmbeddingSettings

_TABLE = "ccc_snapshot_metadata"


def embedding_contract(settings: EmbeddingSettings) -> str:
    """Describe resolved semantic settings, excluding device and rate limiting."""
    params = resolve_embedder_params(settings)
    model = settings.model
    if settings.provider == "sentence-transformers" and model.startswith("sbert/"):
        model = model[len("sbert/") :]
    return json.dumps(
        {
            "version": 1,
            "provider": settings.provider,
            "model": model,
            "indexing_params": params.indexing,
            "query_params": params.query,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def begin_snapshot_update(db: ManagedConnection, contract: str) -> bool:
    """Invalidate readiness before writes; return whether provenance is known.

    A configuration change permanently removes provenance until a local reset.
    Relabelling an incremental index could otherwise certify old/mixed vectors.
    Failed updates with an unchanged contract can be retried.
    """
    with db.transaction() as conn:
        has_metadata = _has_table(conn, _TABLE)
        if not _has_table(conn, "code_chunks_vec"):
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "contract TEXT NOT NULL, ready INTEGER NOT NULL CHECK (ready IN (0, 1)))"
            )
            conn.execute(f"INSERT OR REPLACE INTO {_TABLE} VALUES (1, ?, 0)", (contract,))
            return True
        if not has_metadata:
            return False
        row = conn.execute(f"SELECT contract FROM {_TABLE} WHERE id=1").fetchone()
        if row is None or row[0] != contract:
            conn.execute(f"DELETE FROM {_TABLE}")
            return False
        conn.execute(f"UPDATE {_TABLE} SET ready=0 WHERE id=1")
        return True


def finish_snapshot_update(db: ManagedConnection, contract: str) -> None:
    """Mark known provenance ready only after indexing completed without errors."""
    with db.transaction() as conn:
        conn.execute(f"UPDATE {_TABLE} SET ready=1 WHERE id=1 AND contract=?", (contract,))


def require_snapshot_compatible(db: ManagedConnection, expected: str | None) -> None:
    """Fail closed before embedding, reading metadata from the queried DB handle."""
    advice = (
        "Use matching embedding provider, model, indexing_params and query_params. "
        "Ask the team leader to reset/rebuild the leader-local index and republish "
        "a completed snapshot if metadata is missing or incompatible."
    )
    if expected is None:
        raise RuntimeError("Cannot verify shared index: daemon embedding settings unavailable.")
    try:
        with db.readonly() as conn:
            if not _has_table(conn, _TABLE):
                raise RuntimeError(
                    f"Cannot verify shared index: missing embedding metadata. {advice}"
                )
            row = conn.execute(f"SELECT contract, ready FROM {_TABLE} WHERE id=1").fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(
            f"Cannot verify shared index: unreadable embedding metadata. {advice}"
        ) from exc
    if row is None:
        raise RuntimeError(f"Cannot verify shared index: unknown embedding provenance. {advice}")
    if row[0] != expected:
        raise RuntimeError(
            f"Shared index embedding settings differ or metadata is unsupported. {advice}"
        )
    if row[1] != 1:
        raise RuntimeError(
            "Shared index is not ready: leader indexing is incomplete or failed. "
            "Ask the team leader to finish indexing and republish a completed snapshot."
        )
