"""Benchmark: TurboQuant compressed backend vs raw float32 (sqlite-vec-equivalent).

Reports the headline trade-off numbers — index size, in-memory size, query
latency, and recall@{1,10} vs exact float32 ground truth — on real embeddings of
this repository's own source.

Run with::

    uv run pytest tests/benchmark_turbo_quant.py -m benchmark -s

Excluded from the default test run (see the ``benchmark`` marker in
pyproject.toml). Carries soft assertions so genuine regressions still fail, but
the primary output is the printed table.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import numpy as np
import pytest

from cocoindex_code.tq_store import (
    TqStore,
    create_tables,
    insert_rows,
    quantize_row,
    store_size_bytes,
    write_metadata,
)
from cocoindex_code.turbo_quant import TurboQuant

pytestmark = pytest.mark.benchmark

_MODEL = "sentence-transformers/paraphrase-MiniLM-L3-v2"  # d=384, matches conftest
_N_QUERIES = 50
_SEED = 0


def _load_corpus_texts(limit: int = 1500) -> list[str]:
    """Chunk this repo's own Python source into snippets for embedding."""
    root = Path(__file__).resolve().parent.parent / "src"
    texts: list[str] = []
    for path in sorted(root.rglob("*.py")):
        lines = path.read_text(errors="ignore").splitlines()
        for i in range(0, len(lines), 20):
            block = "\n".join(lines[i : i + 20]).strip()
            if len(block) > 40:
                texts.append(block)
            if len(texts) >= limit:
                return texts
    return texts


def _embed(texts: list[str]) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(_MODEL.split("/", 1)[1])
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)


def _recall_at_k(exact_top: set[int], got: list[int], k: int) -> float:
    return len(exact_top & set(got[:k])) / float(k)


def _build_tq(embs: np.ndarray, bits: int) -> tuple[sqlite3.Connection, TqStore]:
    dim = embs.shape[1]
    tq = TurboQuant(dim=dim, bits=bits, seed=_SEED)
    conn = sqlite3.connect(":memory:")
    create_tables(conn)
    write_metadata(conn, bits=bits, dim=dim, seed=_SEED)
    rows = [
        quantize_row(
            tq,
            chunk_id=i,
            file_path=f"f{i}.py",
            language="python",
            content=f"chunk {i}",
            start_line=i,
            end_line=i + 1,
            embedding=embs[i],
        )
        for i in range(len(embs))
    ]
    insert_rows(conn, rows)
    return conn, TqStore.load(conn)


def test_benchmark_report() -> None:
    texts = _load_corpus_texts()
    embs = _embed(texts)
    n, dim = embs.shape
    rng = np.random.default_rng(123)
    query_ids = rng.choice(n, size=min(_N_QUERIES, n), replace=False)

    # Exact float32 ground truth (brute force).
    def exact_topk(q: np.ndarray, k: int) -> list[int]:
        return [int(i) for i in np.argsort(-(embs @ q))[:k]]

    raw_float32_bytes = n * dim * 4  # sqlite-vec stores raw float32

    print(f"\n=== TurboQuant benchmark — n={n} chunks, dim={dim} ===")
    print(
        f"{'backend':<16}{'size(MB)':>10}{'ratio':>8}{'mem(MB)':>10}"
        f"{'q-lat(ms)':>11}{'recall@1':>10}{'recall@10':>11}"
    )

    # Float32 baseline latency (numpy brute force == exact).
    t0 = time.perf_counter()
    for qi in query_ids:
        exact_topk(embs[qi], 10)
    f32_lat = (time.perf_counter() - t0) / len(query_ids) * 1000
    print(
        f"{'float32(exact)':<16}{raw_float32_bytes / 1e6:>10.2f}{1.0:>8.1f}"
        f"{raw_float32_bytes / 1e6:>10.2f}{f32_lat:>11.3f}{1.0:>10.3f}{1.0:>11.3f}"
    )

    results = {}
    for bits in (2, 4):
        conn, store = _build_tq(embs, bits)
        disk = store_size_bytes(conn)
        mem = store.loaded_nbytes()

        # Latency.
        t0 = time.perf_counter()
        for qi in query_ids:
            store.search(embs[qi], limit=10)
        lat = (time.perf_counter() - t0) / len(query_ids) * 1000

        # Recall.
        r1, r10 = [], []
        for qi in query_ids:
            q = embs[qi]
            exact1 = set(exact_topk(q, 1))
            exact10 = set(exact_topk(q, 10))
            got = [int(r.content.split()[1]) for r in store.search(q, limit=10)]
            r1.append(_recall_at_k(exact1, got, 1))
            r10.append(_recall_at_k(exact10, got, 10))
        recall1 = float(np.mean(r1))
        recall10 = float(np.mean(r10))
        ratio = raw_float32_bytes / disk if disk else float("inf")
        results[bits] = (ratio, recall1, recall10)
        print(
            f"{'turbo-quant b' + str(bits):<16}{disk / 1e6:>10.2f}{ratio:>8.1f}"
            f"{mem / 1e6:>10.2f}{lat:>11.3f}{recall1:>10.3f}{recall10:>11.3f}"
        )
        conn.close()

    print(
        "\nTakeaway: TurboQuant wins on index size + memory; float32 wins on "
        "query latency (numpy/C exact scan). Recall stays high at 4-bit.\n"
    )

    # Soft regression gates.
    ratio4, _, recall10_4 = results[4]
    assert ratio4 >= 6.0, f"4-bit compression ratio {ratio4:.1f}x < 6x"
    assert recall10_4 >= 0.80, f"4-bit recall@10 {recall10_4:.2f} < 0.80"
    _, _, recall10_2 = results[2]
    assert recall10_2 >= 0.55, f"2-bit recall@10 {recall10_2:.2f} < 0.55"
