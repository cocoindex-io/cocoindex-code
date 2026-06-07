"""Tests for the TurboQuant compressed store: persist, load, search, filters."""

from __future__ import annotations

import math
import sqlite3

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

_DIM = 128
_BITS = 4
_SEED = 13


def _unit(rng: np.random.Generator, d: int) -> np.ndarray:
    v = rng.standard_normal(d).astype(np.float32)
    return v / np.linalg.norm(v)


def _build_index(conn, embeddings, *, languages=None, file_paths=None, seed=_SEED, bits=_BITS):
    """Quantize and persist a list of embeddings; return the TurboQuant used."""
    tq = TurboQuant(dim=_DIM, bits=bits, seed=seed)
    create_tables(conn)
    write_metadata(conn, bits=bits, dim=_DIM, seed=seed)
    rows = []
    for i, emb in enumerate(embeddings):
        lang = languages[i] if languages else "python"
        fp = file_paths[i] if file_paths else f"src/file_{i}.py"
        rows.append(
            quantize_row(
                tq,
                chunk_id=i,
                file_path=fp,
                language=lang,
                content=f"chunk {i}",
                start_line=i,
                end_line=i + 1,
                embedding=emb,
            )
        )
    insert_rows(conn, rows)
    return tq


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def test_persist_load_search_finds_nearest(conn) -> None:
    rng = np.random.default_rng(1)
    embs = [_unit(rng, _DIM) for _ in range(50)]
    _build_index(conn, embs)

    store = TqStore.load(conn)
    assert len(store) == 50

    # Query with one of the indexed vectors -> it should rank top-1.
    target = 17
    results = store.search(embs[target], limit=1)
    assert len(results) == 1
    assert results[0].content == f"chunk {target}"


def test_scores_descending(conn) -> None:
    rng = np.random.default_rng(2)
    embs = [_unit(rng, _DIM) for _ in range(30)]
    _build_index(conn, embs)
    store = TqStore.load(conn)
    results = store.search(embs[0], limit=10)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_language_filter(conn) -> None:
    rng = np.random.default_rng(3)
    embs = [_unit(rng, _DIM) for _ in range(30)]
    langs = ["python" if i % 2 == 0 else "go" for i in range(30)]
    _build_index(conn, embs, languages=langs)
    store = TqStore.load(conn)
    results = store.search(embs[0], limit=30, languages=["python"])
    assert all(r.language == "python" for r in results)
    assert len(results) == 15


def test_multi_language_filter(conn) -> None:
    rng = np.random.default_rng(4)
    embs = [_unit(rng, _DIM) for _ in range(30)]
    langs = ["python", "go", "rust"] * 10
    _build_index(conn, embs, languages=langs)
    store = TqStore.load(conn)
    results = store.search(embs[0], limit=30, languages=["python", "go"])
    assert {r.language for r in results} <= {"python", "go"}
    assert len(results) == 20


def test_path_filter(conn) -> None:
    rng = np.random.default_rng(5)
    embs = [_unit(rng, _DIM) for _ in range(20)]
    fps = [f"src/{i}.py" if i < 10 else f"tests/{i}.py" for i in range(20)]
    _build_index(conn, embs, file_paths=fps)
    store = TqStore.load(conn)
    results = store.search(embs[0], limit=20, paths=["src/*"])
    assert all(r.file_path.startswith("src/") for r in results)
    assert len(results) == 10


def test_combined_language_and_path_filter(conn) -> None:
    rng = np.random.default_rng(6)
    embs = [_unit(rng, _DIM) for _ in range(20)]
    langs = ["python" if i % 2 == 0 else "go" for i in range(20)]
    fps = [f"src/{i}.py" if i < 10 else f"lib/{i}.py" for i in range(20)]
    _build_index(conn, embs, languages=langs, file_paths=fps)
    store = TqStore.load(conn)
    results = store.search(embs[0], limit=20, languages=["python"], paths=["src/*"])
    for r in results:
        assert r.language == "python"
        assert r.file_path.startswith("src/")


def test_offset_and_limit(conn) -> None:
    rng = np.random.default_rng(7)
    embs = [_unit(rng, _DIM) for _ in range(40)]
    _build_index(conn, embs)
    store = TqStore.load(conn)
    full = store.search(embs[0], limit=10, offset=0)
    paged = store.search(embs[0], limit=5, offset=5)
    # paged should equal items 6..10 of the full ranking.
    assert [r.content for r in full[5:10]] == [r.content for r in paged]


def test_empty_candidate_set_returns_empty(conn) -> None:
    rng = np.random.default_rng(8)
    embs = [_unit(rng, _DIM) for _ in range(10)]
    _build_index(conn, embs, languages=["python"] * 10)
    store = TqStore.load(conn)
    assert store.search(embs[0], limit=10, languages=["haskell"]) == []


def test_empty_store_returns_empty(conn) -> None:
    create_tables(conn)
    write_metadata(conn, bits=_BITS, dim=_DIM, seed=_SEED)
    store = TqStore.load(conn)
    assert len(store) == 0
    assert store.search(np.ones(_DIM, dtype=np.float32), limit=5) == []


def test_reload_matches_in_memory_search(conn) -> None:
    """Search after reload (matrices regenerated from seed) matches first load."""
    rng = np.random.default_rng(9)
    embs = [_unit(rng, _DIM) for _ in range(25)]
    _build_index(conn, embs)
    store1 = TqStore.load(conn)
    r1 = store1.search(embs[3], limit=5)
    store2 = TqStore.load(conn)  # fresh load, fresh TurboQuant from seed
    r2 = store2.search(embs[3], limit=5)
    assert [x.content for x in r1] == [x.content for x in r2]
    assert [round(x.score, 5) for x in r1] == [round(x.score, 5) for x in r2]


def test_store_size_reflects_bits(conn) -> None:
    rng = np.random.default_rng(10)
    embs = [_unit(rng, _DIM) for _ in range(100)]
    _build_index(conn, embs, bits=4)
    size = store_size_bytes(conn)
    # idx is (bits-1)=3 bits/coord, qjl is 1 bit/coord, over dim coords, 100 rows.
    expected_per_row = math.ceil(_DIM * 3 / 8) + math.ceil(_DIM * 1 / 8)
    assert size == expected_per_row * 100


def test_recall_at_10_reasonable(conn) -> None:
    """Sanity: 4-bit prod search recovers most exact top-10 neighbors."""
    rng = np.random.default_rng(11)
    embs = np.array([_unit(rng, _DIM) for _ in range(300)])
    _build_index(conn, list(embs), bits=4)
    store = TqStore.load(conn)

    queries = [_unit(rng, _DIM) for _ in range(30)]
    recalls = []
    for q in queries:
        exact = set(np.argsort(-(embs @ q))[:10].tolist())
        got_rows = store.search(q, limit=10)
        # Map results back to indices via content "chunk {i}".
        got = {int(r.content.split()[1]) for r in got_rows}
        recalls.append(len(exact & got) / 10.0)
    mean_recall = float(np.mean(recalls))
    assert mean_recall >= 0.6, f"recall@10 too low: {mean_recall:.2f}"
