"""Threshold-based duplicate detection over an existing code index."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import msgspec
import numpy as np
from cocoindex.connectors import sqlite

_BLOCK_SIZE = 256


class ChunkLocation(msgspec.Struct, frozen=True):
    file_path: str
    language: str
    start_line: int
    end_line: int


def find_duplicate_groups(
    database: Path,
    *,
    threshold: float,
    min_lines: int,
    languages: list[str],
    path: str | None,
) -> list[list[ChunkLocation]]:
    """Read a snapshot and group chunks connected by cosine-similarity matches."""
    conditions = ["end_line - start_line + 1 >= ?"]
    params: list[str | int] = [min_lines]
    if languages:
        conditions.append(f"language IN ({', '.join('?' for _ in languages)})")
        params.extend(languages)
    if path is not None:
        conditions.append("file_path GLOB ?")
        params.append(path)

    locations: list[ChunkLocation] = []
    embeddings = []
    try:
        with (
            closing(
                sqlite.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True, load_vec=True)
            ) as db,
            db.readonly() as conn,
        ):
            for file_path, language, start, end, embedding in conn.execute(
                f"""
                SELECT file_path, language, start_line, end_line, embedding
                FROM code_chunks_vec
                WHERE {" AND ".join(conditions)}
                ORDER BY file_path, start_line, end_line, id
                """,
                params,
            ):
                vector = np.frombuffer(embedding, dtype=np.float32).astype(np.float64)
                norm = np.linalg.norm(vector)
                if np.isfinite(norm) and norm > 0:
                    locations.append(ChunkLocation(file_path, language, start, end))
                    embeddings.append(vector / norm)
    except sqlite3.OperationalError as exc:
        if not database.is_file() or "no such table: code_chunks_vec" in str(exc):
            raise ValueError("Index not created yet. Run `ccc index` first.") from exc
        raise

    if len(embeddings) < 2:
        return []
    vectors = np.stack(embeddings)
    del embeddings

    # Union by size with path compression. Each qualifying pair is visited once.
    parents = list(range(len(vectors)))
    sizes = [1] * len(vectors)

    def root(node: int) -> int:
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    # Roundoff in a dot product of normalized vectors can put an exact match
    # slightly below 1. Leave every other threshold unchanged.
    cutoff = 1.0 - 1e-12 if threshold == 1.0 else threshold
    for left_start in range(0, len(vectors), _BLOCK_SIZE):
        left = vectors[left_start : left_start + _BLOCK_SIZE]
        for right_start in range(left_start, len(vectors), _BLOCK_SIZE):
            right = vectors[right_start : right_start + _BLOCK_SIZE]
            matches = left @ right.T >= cutoff
            if left_start == right_start:
                matches = np.triu(matches, k=1)
            for left_offset, right_offset in zip(*np.nonzero(matches), strict=True):
                a = root(left_start + int(left_offset))
                b = root(right_start + int(right_offset))
                if a != b:
                    if sizes[a] < sizes[b]:
                        a, b = b, a
                    parents[b] = a
                    sizes[a] += sizes[b]

    groups: dict[int, list[ChunkLocation]] = {}
    for idx, location in enumerate(locations):
        leader = root(idx)
        if sizes[leader] > 1:
            groups.setdefault(leader, []).append(location)
    return list(groups.values())
