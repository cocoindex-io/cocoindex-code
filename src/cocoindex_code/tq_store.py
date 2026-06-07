"""TurboQuant compressed vector store backed by plain SQLite tables.

Unlike the sqlite-vec path (which uses a ``vec0`` virtual table and C KNN), the
TurboQuant backend stores bit-packed quantized rows in ordinary tables and runs
search as a vectorized inner-product scan in NumPy.

Two tables:

* ``code_chunks_tq`` — one row per chunk: id, file_path, language, content,
  start_line, end_line, and the quantized payload (packed MSE indices, packed
  QJL signs, residual norm, original norm).
* ``tq_metadata`` — a single row describing the index: bit-width, dimension, and
  the seed used to derive the rotation / QJL matrices. The matrices themselves
  are regenerated from the seed on load, so they never need to be serialized.

Search honors the same filters as ``query.py``'s sqlite-vec path: ``languages``
(exact match), ``paths`` (GLOB), ``limit``, and ``offset``.
"""

from __future__ import annotations

import fnmatch
import sqlite3
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from .schema import QueryResult, TqChunkRow
from .turbo_quant import (
    TurboQuant,
    pack_indices,
    pack_signs,
)

TQ_TABLE = "code_chunks_tq"
TQ_METADATA_TABLE = "tq_metadata"


# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------


def create_chunk_table(conn: sqlite3.Connection) -> None:
    """Create the ``code_chunks_tq`` table if absent.

    Used by standalone callers and tests. In the live indexer the cocoindex
    ``mount_table_target`` owns this table's creation, so the indexer only calls
    :func:`create_metadata_table` and must NOT call this.
    """
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TQ_TABLE} (
            id INTEGER PRIMARY KEY,
            file_path TEXT NOT NULL,
            language TEXT NOT NULL,
            content TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            idx_packed BLOB NOT NULL,
            qjl_packed BLOB NOT NULL,
            residual_norm REAL NOT NULL,
            norm REAL NOT NULL
        )
        """
    )


def create_metadata_table(conn: sqlite3.Connection) -> None:
    """Create the ``tq_metadata`` table if absent."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TQ_METADATA_TABLE} (
            id INTEGER PRIMARY KEY CHECK (id = 0),
            backend TEXT NOT NULL,
            bits INTEGER NOT NULL,
            dim INTEGER NOT NULL,
            seed INTEGER NOT NULL
        )
        """
    )


def create_tables(conn: sqlite3.Connection) -> None:
    """Create both TurboQuant tables (standalone / test convenience)."""
    create_chunk_table(conn)
    create_metadata_table(conn)


def write_metadata(conn: sqlite3.Connection, *, bits: int, dim: int, seed: int) -> None:
    """Write (or replace) the single metadata row."""
    conn.execute(
        f"INSERT OR REPLACE INTO {TQ_METADATA_TABLE} (id, backend, bits, dim, seed) "
        f"VALUES (0, ?, ?, ?, ?)",
        ("turbo-quant", bits, dim, seed),
    )


@dataclass
class TqMetadata:
    backend: str
    bits: int
    dim: int
    seed: int


def read_metadata(conn: sqlite3.Connection) -> TqMetadata | None:
    """Read the metadata row, or ``None`` if the table/row is absent."""
    try:
        row = conn.execute(
            f"SELECT backend, bits, dim, seed FROM {TQ_METADATA_TABLE} WHERE id = 0"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return TqMetadata(backend=row[0], bits=int(row[1]), dim=int(row[2]), seed=int(row[3]))


def insert_rows(conn: sqlite3.Connection, rows: list[TqChunkRow]) -> None:
    """Bulk-insert quantized chunk rows."""
    conn.executemany(
        f"""
        INSERT OR REPLACE INTO {TQ_TABLE}
            (id, file_path, language, content, start_line, end_line,
             idx_packed, qjl_packed, residual_norm, norm)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r.id,
                r.file_path,
                r.language,
                r.content,
                r.start_line,
                r.end_line,
                r.idx_packed,
                r.qjl_packed,
                r.residual_norm,
                r.norm,
            )
            for r in rows
        ],
    )


def quantize_row(
    tq: TurboQuant,
    *,
    chunk_id: int,
    file_path: str,
    language: str,
    content: str,
    start_line: int,
    end_line: int,
    embedding: npt.NDArray[np.floating],
) -> TqChunkRow:
    """Quantize one embedding into a storable :class:`TqChunkRow`."""
    mse_idx, qjl_signs, residual_norm, norm = tq.quantize_prod(embedding)
    # MSE stage uses bits-1; a 1-bit prod index has a 0-bit MSE stage (no bytes).
    mse_bits = tq.bits - 1
    idx_packed = pack_indices(mse_idx, mse_bits) if mse_bits >= 1 else b""
    qjl_packed = pack_signs(qjl_signs)
    return TqChunkRow(
        id=chunk_id,
        file_path=file_path,
        language=language,
        content=content,
        start_line=start_line,
        end_line=end_line,
        idx_packed=idx_packed,
        qjl_packed=qjl_packed,
        residual_norm=residual_norm,
        norm=norm,
    )


def _bulk_unpack_indices(blobs: list[bytes], n: int, dim: int, bits: int) -> npt.NDArray[np.int8]:
    """Decode ``n`` equal-length packed-index blobs into an ``(n, dim)`` int8 matrix.

    Vectorized equivalent of calling :func:`unpack_indices` per row: stacks all
    blobs into one uint8 matrix and unpacks the whole batch in a single pass.
    """
    if n == 0:
        return np.empty((0, dim), dtype=np.int8)
    raw = np.frombuffer(b"".join(blobs), dtype=np.uint8).reshape(n, -1)
    bits_mat = np.unpackbits(raw, axis=1)[:, : dim * bits].reshape(n, dim, bits)
    weights = (1 << np.arange(bits - 1, -1, -1)).astype(np.int8)
    return (bits_mat.astype(np.int8) @ weights).astype(np.int8)


def _bulk_unpack_signs(blobs: list[bytes], n: int, dim: int) -> npt.NDArray[np.int8]:
    """Decode ``n`` packed sign blobs into an ``(n, dim)`` int8 ``+-1`` matrix."""
    if n == 0:
        return np.empty((0, dim), dtype=np.int8)
    raw = np.frombuffer(b"".join(blobs), dtype=np.uint8).reshape(n, -1)
    bit = np.unpackbits(raw, axis=1)[:, :dim]
    return np.where(bit > 0, np.int8(1), np.int8(-1)).astype(np.int8)


# ---------------------------------------------------------------------------
# In-memory store + search
# ---------------------------------------------------------------------------


class TqStore:
    """Loaded, searchable TurboQuant index held in NumPy arrays."""

    def __init__(self, tq: TurboQuant, metadata: TqMetadata) -> None:
        self.tq = tq
        self.metadata = metadata
        self._ids: list[int] = []
        self._file_paths: list[str] = []
        self._languages: list[str] = []
        self._contents: list[str] = []
        self._start_lines: list[int] = []
        self._end_lines: list[int] = []
        # Decoded quantized payload as dense arrays for vectorized scoring.
        # Indices (0..15 for <=4 bits) and signs (+-1) fit in int8, keeping the
        # in-memory footprint ~bits-proportional rather than blowing up to int64.
        self._mse_idx: npt.NDArray[np.int8] = np.empty((0, tq.dim), dtype=np.int8)
        self._qjl: npt.NDArray[np.int8] = np.empty((0, tq.dim), dtype=np.int8)
        self._residual_norms: npt.NDArray[np.float32] = np.empty(0, dtype=np.float32)
        self._norms: npt.NDArray[np.float32] = np.empty(0, dtype=np.float32)

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> TqStore:
        """Load the full index into memory. Raises if metadata is missing."""
        metadata = read_metadata(conn)
        if metadata is None:
            raise RuntimeError("TurboQuant metadata not found; index not built with turbo-quant")
        tq = TurboQuant(dim=metadata.dim, bits=metadata.bits, seed=metadata.seed)
        store = cls(tq, metadata)
        store._load_rows(conn)
        return store

    def _load_rows(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            f"SELECT id, file_path, language, content, start_line, end_line, "
            f"idx_packed, qjl_packed, residual_norm, norm FROM {TQ_TABLE} ORDER BY id"
        ).fetchall()
        n = len(rows)
        dim = self.tq.dim
        mse_bits = self.tq.bits - 1

        # Metadata columns: cheap Python-side gather.
        self._ids = [int(r[0]) for r in rows]
        self._file_paths = [r[1] for r in rows]
        self._languages = [r[2] for r in rows]
        self._contents = [r[3] for r in rows]
        self._start_lines = [int(r[4]) for r in rows]
        self._end_lines = [int(r[5]) for r in rows]
        self._residual_norms = np.array([r[8] for r in rows], dtype=np.float32)
        self._norms = np.array([r[9] for r in rows], dtype=np.float32)

        # Quantized payload: decode all rows in one vectorized pass instead of a
        # per-row unpack loop (the dominant cost at scale). Every row's blob has
        # the same byte length, so the blobs stack into a single uint8 matrix and
        # np.unpackbits decodes the whole batch at once.
        self._mse_idx = (
            _bulk_unpack_indices([r[6] for r in rows], n, dim, mse_bits)
            if mse_bits >= 1
            else np.zeros((n, dim), dtype=np.int8)
        )
        self._qjl = _bulk_unpack_signs([r[7] for r in rows], n, dim)

    def __len__(self) -> int:
        return len(self._ids)

    # -- filtering ----------------------------------------------------------

    def _candidate_mask(
        self, languages: list[str] | None, paths: list[str] | None
    ) -> npt.NDArray[np.bool_]:
        n = len(self._ids)
        mask = np.ones(n, dtype=bool)
        if languages:
            lang_set = set(languages)
            mask &= np.array([lg in lang_set for lg in self._languages], dtype=bool)
        if paths:
            path_mask = np.array(
                [any(fnmatch.fnmatch(fp, pat) for pat in paths) for fp in self._file_paths],
                dtype=bool,
            )
            mask &= path_mask
        return mask

    # -- search -------------------------------------------------------------

    def search(
        self,
        query_embedding: npt.NDArray[np.floating],
        limit: int = 10,
        offset: int = 0,
        languages: list[str] | None = None,
        paths: list[str] | None = None,
    ) -> list[QueryResult]:
        """Top-(limit) inner-product search over the (filtered) candidate set.

        Returns results in descending estimated-inner-product order. The score is
        the unbiased inner-product estimate (higher = more similar), consistent
        with the sqlite-vec path returning a higher-is-better similarity.
        """
        n = len(self._ids)
        if n == 0:
            return []
        mask = self._candidate_mask(languages, paths)
        cand = np.nonzero(mask)[0]
        if cand.size == 0:
            return []

        scores = self._score(np.asarray(query_embedding, dtype=np.float32), cand)

        # Top (limit+offset) by score, then slice the offset window.
        want = min(limit + offset, cand.size)
        # argpartition for the top `want`, then sort that slice descending.
        part = np.argpartition(-scores, want - 1)[:want]
        ordered = part[np.argsort(-scores[part])]
        window = ordered[offset : offset + limit]

        results: list[QueryResult] = []
        for local in window:
            global_i = int(cand[local])
            results.append(
                QueryResult(
                    file_path=self._file_paths[global_i],
                    language=self._languages[global_i],
                    content=self._contents[global_i],
                    start_line=self._start_lines[global_i],
                    end_line=self._end_lines[global_i],
                    score=float(scores[local]),
                )
            )
        return results

    def _score(
        self, q: npt.NDArray[np.float32], cand: npt.NDArray[np.int64]
    ) -> npt.NDArray[np.float32]:
        """Vectorized unbiased inner-product estimate for candidate rows.

        Mirrors :meth:`TurboQuant.inner_product_prod` but batched across rows:

            score = norm * ( <q, u_mse> + sqrt(pi/2)/d * gamma * <S q, qjl> )

        where ``u_mse`` is the dequantized MSE term (unit-space) and ``S q`` is
        projected once for the whole batch.
        """
        tq = self.tq
        dim = tq.dim
        mse_bits = tq.bits - 1

        # MSE term: dequantize candidate MSE indices back to unit space, dot q.
        if mse_bits >= 1:
            codebook = (tq._codebook(mse_bits)).astype(np.float32)  # scaled centroids
            # y_hat[cand] : (m, d) rotated reconstructions; rotate back via Pi^T.
            y_hat = codebook[self._mse_idx[cand]]  # (m, d)
            u_mse = y_hat @ tq._rotation  # (m,d)@(d,d) == (Pi^T y_hat) rows
            mse_term = u_mse @ q  # (m,)
        else:
            mse_term = np.zeros(cand.size, dtype=np.float32)

        # QJL term: project q once, then dot with each row's sign vector.
        sq = tq._qjl @ q  # (d,)
        qjl_dot = self._qjl[cand] @ sq  # (m,)
        coef = np.float32(np.sqrt(np.pi / 2.0) / dim)
        qjl_term = coef * self._residual_norms[cand] * qjl_dot

        return self._norms[cand] * (mse_term + qjl_term)

    # -- size accounting (for the benchmark) --------------------------------

    def loaded_nbytes(self) -> int:
        """Approximate in-memory size of the decoded arrays."""
        return int(
            self._mse_idx.nbytes
            + self._qjl.nbytes
            + self._residual_norms.nbytes
            + self._norms.nbytes
        )


def index_table_name(conn: sqlite3.Connection) -> str | None:
    """Return the chunk table backing this index, or ``None`` if not indexed.

    Lets backend-agnostic callers (``ccc doctor``, status) count chunks without
    hard-coding ``code_chunks_vec``. Prefers the TurboQuant table when present.
    """
    for name in (TQ_TABLE, "code_chunks_vec"):
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
            (name,),
        ).fetchone()
        if row is not None:
            return name
    return None


def store_size_bytes(conn: sqlite3.Connection) -> int:
    """On-disk payload size: total bytes of the packed blobs in ``code_chunks_tq``."""
    row = conn.execute(
        f"SELECT COALESCE(SUM(LENGTH(idx_packed) + LENGTH(qjl_packed)), 0) FROM {TQ_TABLE}"
    ).fetchone()
    return int(row[0])
