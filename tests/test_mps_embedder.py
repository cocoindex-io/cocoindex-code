"""MPS embedding memory and worker-lifecycle safety tests."""

from __future__ import annotations

import threading
import time
from multiprocessing.connection import Connection
from typing import Any

import numpy as np
import pytest

from cocoindex_code.mps_embedder import (
    EmbeddingWorkerClient,
    EmbeddingWorkerCrashedError,
    EmbeddingWorkerTimeoutError,
    MPSWorkerConfig,
    _encode_with_oom_backoff,
    _should_recycle_mps_worker,
)


class _OomUntilSmallBatchModel:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def encode(self, texts: list[str], *, batch_size: int, **kwargs: Any) -> np.ndarray:
        self.batch_sizes.append(batch_size)
        if batch_size > 2:
            raise RuntimeError("MPS backend out of memory")
        return np.zeros((len(texts), 3), dtype=np.float32)


def _recycling_worker(conn: Connection, config: MPSWorkerConfig) -> None:
    del config
    request = conn.recv()
    conn.send(
        {
            "ok": True,
            "result": request["payload"],
            "recycle": True,
            "driver_memory": 100,
            "recommended_memory": 200,
        }
    )
    conn.close()


def _hanging_worker(conn: Connection, config: MPSWorkerConfig) -> None:
    del config
    conn.recv()
    time.sleep(10)


def _worker_config() -> MPSWorkerConfig:
    return MPSWorkerConfig(
        model_name="unused-in-boundary-test",
        device="mps",
        trust_remote_code=False,
        batch_size=8,
        memory_limit_ratio=0.35,
    )


def test_mps_oom_retries_with_smaller_inner_batches() -> None:
    model = _OomUntilSmallBatchModel()
    empty_cache_calls = 0

    def empty_cache() -> None:
        nonlocal empty_cache_calls
        empty_cache_calls += 1

    result = _encode_with_oom_backoff(
        model,
        ["a", "b", "c"],
        batch_size=8,
        prompt_name=None,
        normalize_embeddings=True,
        empty_cache=empty_cache,
    )

    assert result.shape == (3, 3)
    assert model.batch_sizes == [8, 4, 2]
    assert empty_cache_calls == 2


def test_non_oom_embedding_error_is_not_retried() -> None:
    class BrokenModel:
        def encode(self, texts: list[str], *, batch_size: int, **kwargs: Any) -> np.ndarray:
            raise RuntimeError("invalid prompt")

    with pytest.raises(RuntimeError, match="invalid prompt"):
        _encode_with_oom_backoff(
            BrokenModel(),
            ["a"],
            batch_size=8,
            prompt_name=None,
            normalize_embeddings=True,
            empty_cache=lambda: None,
        )


def test_worker_recycles_only_after_driver_memory_crosses_limit() -> None:
    assert (
        _should_recycle_mps_worker(
            driver_memory=349,
            recommended_memory=1000,
            memory_limit_ratio=0.35,
        )
        is False
    )
    assert (
        _should_recycle_mps_worker(
            driver_memory=350,
            recommended_memory=1000,
            memory_limit_ratio=0.35,
        )
        is True
    )


def test_worker_recycle_response_starts_a_fresh_process_for_next_request() -> None:
    client = EmbeddingWorkerClient(
        _worker_config(),
        timeout_seconds=2,
        worker_target=_recycling_worker,
    )
    try:
        assert client.request("echo", [1]) == [1]
        assert client.generation == 1
        assert client.request("echo", [2]) == [2]
        assert client.generation == 2
    finally:
        client.close()


def test_worker_timeout_terminates_stuck_process() -> None:
    client = EmbeddingWorkerClient(
        _worker_config(),
        timeout_seconds=0.1,
        worker_target=_hanging_worker,
    )
    try:
        with pytest.raises(EmbeddingWorkerTimeoutError, match="0.1"):
            client.request("embed", ["stuck"])
        assert client.worker_alive is False
    finally:
        client.close()


def test_close_terminates_worker_even_while_request_is_stuck() -> None:
    client = EmbeddingWorkerClient(
        _worker_config(),
        timeout_seconds=2,
        worker_target=_hanging_worker,
    )
    request_finished = threading.Event()

    def request() -> None:
        try:
            client.request("embed", ["stuck"])
        except EmbeddingWorkerCrashedError:
            pass
        finally:
            request_finished.set()

    thread = threading.Thread(target=request)
    thread.start()
    time.sleep(0.3)

    started = time.monotonic()
    client.close()
    elapsed = time.monotonic() - started

    thread.join(timeout=2)
    assert elapsed < 1
    assert request_finished.is_set() is True
    assert client.worker_alive is False
