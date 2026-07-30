"""SentenceTransformer embedding isolated behind a recyclable MPS worker."""

from __future__ import annotations

import gc
import logging
import multiprocessing
import os
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any, Protocol, cast

import cocoindex as coco
import numpy as np
from cocoindex.resources import schema
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

_MPS_DEFAULT_BATCH_SIZE = 8
_WORKER_JOIN_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class MPSWorkerConfig:
    """Serializable configuration owned by the embedding worker process."""

    model_name: str
    device: str | None
    trust_remote_code: bool
    batch_size: int
    memory_limit_ratio: float


class _SentenceTransformerModel(Protocol):
    @property
    def device(self) -> Any: ...

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int,
        prompt_name: str | None,
        convert_to_numpy: bool,
        normalize_embeddings: bool,
        show_progress_bar: bool,
    ) -> NDArray[np.float32]: ...

    def get_sentence_embedding_dimension(self) -> int | None: ...


WorkerTarget = Callable[[Connection, MPSWorkerConfig], None]


class EmbeddingWorkerError(RuntimeError):
    """Base error for failures at the embedding process boundary."""


class EmbeddingWorkerTimeoutError(EmbeddingWorkerError):
    """The worker did not answer before its configured deadline."""


class EmbeddingWorkerCrashedError(EmbeddingWorkerError):
    """The worker exited or broke its IPC channel before answering."""


class EmbeddingWorkerRemoteError(EmbeddingWorkerError):
    """The worker answered with an embedding/model error."""


def apply_mps_allocator_guards(*, low_watermark_ratio: float, high_watermark_ratio: float) -> None:
    """Set conservative PyTorch MPS allocator defaults without overriding user env.

    PyTorch reads these values from the process environment.  The daemon calls
    this before importing ``torch`` or ``sentence_transformers``; spawned
    workers then inherit the same bounds.
    """

    os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", str(low_watermark_ratio))
    os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", str(high_watermark_ratio))


def _is_oom_error(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    return "out of memory" in message or "mps backend oom" in message


def _encode_with_oom_backoff(
    model: _SentenceTransformerModel,
    texts: list[str],
    *,
    batch_size: int,
    prompt_name: str | None,
    normalize_embeddings: bool,
    empty_cache: Callable[[], None],
) -> NDArray[np.float32]:
    """Encode, halving the inner batch after recoverable MPS OOM failures."""

    current_batch_size = max(1, batch_size)
    while True:
        try:
            return model.encode(
                texts,
                batch_size=current_batch_size,
                prompt_name=prompt_name,
                convert_to_numpy=True,
                normalize_embeddings=normalize_embeddings,
                show_progress_bar=False,
            )
        except RuntimeError as exc:
            if not _is_oom_error(exc) or current_batch_size == 1:
                raise
            empty_cache()
            next_batch_size = max(1, current_batch_size // 2)
            logger.warning(
                "MPS embedding OOM at batch size %d; retrying with %d",
                current_batch_size,
                next_batch_size,
            )
            current_batch_size = next_batch_size


def _should_recycle_mps_worker(
    *,
    driver_memory: int,
    recommended_memory: int,
    memory_limit_ratio: float,
) -> bool:
    """Return whether driver-owned bytes crossed the worker recycle threshold."""

    return recommended_memory > 0 and driver_memory >= recommended_memory * memory_limit_ratio


def _mps_memory_after_cache_cleanup(
    model: _SentenceTransformerModel,
    *,
    memory_limit_ratio: float,
) -> tuple[bool, int | None, int | None]:
    """Inspect MPS driver memory and clear cache only when near the recycle bound."""

    if str(model.device).split(":", 1)[0] != "mps":
        return False, None, None

    import torch

    torch.mps.synchronize()
    driver_memory = int(torch.mps.driver_allocated_memory())
    recommended_memory = int(torch.mps.recommended_max_memory())
    if not _should_recycle_mps_worker(
        driver_memory=driver_memory,
        recommended_memory=recommended_memory,
        memory_limit_ratio=memory_limit_ratio,
    ):
        return False, driver_memory, recommended_memory

    gc.collect()
    torch.mps.empty_cache()
    torch.mps.synchronize()
    driver_memory = int(torch.mps.driver_allocated_memory())
    return (
        _should_recycle_mps_worker(
            driver_memory=driver_memory,
            recommended_memory=recommended_memory,
            memory_limit_ratio=memory_limit_ratio,
        ),
        driver_memory,
        recommended_memory,
    )


def _sentence_transformer_worker_main(conn: Connection, config: MPSWorkerConfig) -> None:
    """Own the model and all Metal allocations until recycling or shutdown."""

    try:
        from sentence_transformers import SentenceTransformer

        model = cast(
            _SentenceTransformerModel,
            SentenceTransformer(
                config.model_name,
                device=config.device,
                trust_remote_code=config.trust_remote_code,
            ),
        )

        while True:
            request = cast(dict[str, Any], conn.recv())
            operation = request.get("operation")
            if operation == "close":
                return

            payload = request.get("payload")
            if operation == "dimension":
                result: Any = model.get_sentence_embedding_dimension()
                if result is None:
                    raise RuntimeError(
                        f"Embedding dimension is unknown for model {config.model_name}."
                    )
                result = int(result)
            elif operation == "embed":
                if not isinstance(payload, dict):
                    raise TypeError("embed worker payload must be a mapping")
                result = _encode_with_oom_backoff(
                    model,
                    list(payload["texts"]),
                    batch_size=int(payload.get("batch_size", config.batch_size)),
                    prompt_name=payload.get("prompt_name"),
                    normalize_embeddings=bool(payload.get("normalize_embeddings", True)),
                    empty_cache=_empty_mps_cache,
                )
            else:
                raise ValueError(f"Unknown embedding worker operation: {operation!r}")

            try:
                recycle, driver_memory, recommended_memory = _mps_memory_after_cache_cleanup(
                    model,
                    memory_limit_ratio=config.memory_limit_ratio,
                )
            except Exception:
                # The allocator watermarks remain the hard safety boundary when
                # a PyTorch build cannot report one of the advisory metrics.
                logger.exception("Unable to inspect MPS worker memory")
                recycle, driver_memory, recommended_memory = False, None, None

            conn.send(
                {
                    "ok": True,
                    "result": result,
                    "recycle": recycle,
                    "driver_memory": driver_memory,
                    "recommended_memory": recommended_memory,
                }
            )
            if recycle:
                return
    except EOFError:
        return
    except BaseException as exc:
        try:
            conn.send(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "recycle": True,
                }
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        conn.close()


def _empty_mps_cache() -> None:
    import torch

    gc.collect()
    torch.mps.empty_cache()


class EmbeddingWorkerClient:
    """Synchronous, thread-safe owner of one disposable worker process."""

    def __init__(
        self,
        config: MPSWorkerConfig,
        *,
        timeout_seconds: float,
        worker_target: WorkerTarget = _sentence_transformer_worker_main,
    ) -> None:
        self._config = config
        self._timeout_seconds = timeout_seconds
        self._worker_target = worker_target
        self._context = multiprocessing.get_context("spawn")
        self._lock = threading.Lock()
        self._process: BaseProcess | None = None
        self._conn: Connection | None = None
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def worker_alive(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.is_alive()

    def _start_locked(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._discard_locked(graceful=False)
        parent_conn, child_conn = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=self._worker_target,
            args=(child_conn, self._config),
            name="cocoindex-mps-embedder",
            daemon=True,
        )
        process.start()
        child_conn.close()
        self._process = process
        self._conn = parent_conn
        self._generation += 1
        logger.info(
            "Started MPS embedding worker PID %s (generation %d)",
            process.pid,
            self._generation,
        )

    def request(self, operation: str, payload: Any) -> Any:
        with self._lock:
            self._start_locked()
            assert self._conn is not None
            try:
                self._conn.send({"operation": operation, "payload": payload})
                if not self._conn.poll(self._timeout_seconds):
                    self._discard_locked(graceful=False, force=True)
                    raise EmbeddingWorkerTimeoutError(
                        f"Embedding worker exceeded {self._timeout_seconds:g}s timeout"
                    )
                response = cast(dict[str, Any], self._conn.recv())
            except EmbeddingWorkerTimeoutError:
                raise
            except (BrokenPipeError, EOFError, OSError) as exc:
                self._discard_locked(graceful=False, force=True)
                raise EmbeddingWorkerCrashedError(
                    f"Embedding worker exited before answering: {exc}"
                ) from exc

            if not response.get("ok"):
                self._discard_locked(graceful=False)
                error_type = response.get("error_type", "Exception")
                message = response.get("error", "unknown worker error")
                remote_traceback = response.get("traceback", "")
                raise EmbeddingWorkerRemoteError(
                    f"{error_type}: {message}\nWorker traceback:\n{remote_traceback}"
                )

            if response.get("recycle"):
                driver_memory = response.get("driver_memory")
                recommended_memory = response.get("recommended_memory")
                logger.warning(
                    "Recycling MPS embedding worker after driver memory reached %s/%s bytes",
                    driver_memory,
                    recommended_memory,
                )
                self._discard_locked(graceful=False)

            return response.get("result")

    def _discard_locked(self, *, graceful: bool, force: bool = False) -> None:
        process = self._process
        conn = self._conn
        self._process = None
        self._conn = None

        if graceful and conn is not None and process is not None and process.is_alive():
            try:
                conn.send({"operation": "close", "payload": None})
            except (BrokenPipeError, EOFError, OSError):
                pass
        if conn is not None:
            conn.close()

        if process is None:
            return
        if force and process.is_alive():
            process.terminate()
        process.join(timeout=_WORKER_JOIN_TIMEOUT_SECONDS)
        if process.is_alive():
            process.terminate()
            process.join(timeout=_WORKER_JOIN_TIMEOUT_SECONDS)
        try:
            process.close()
        except ValueError:
            pass

    def close(self) -> None:
        if self._lock.acquire(timeout=0.1):
            try:
                self._discard_locked(graceful=True)
            finally:
                self._lock.release()
            return

        # A request can hold the lock while blocked in a Metal kernel or IPC
        # wait.  Killing the child is safe without mutating the connection
        # fields; the request thread observes EOF and performs normal cleanup.
        process = self._process
        if process is not None and process.is_alive():
            logger.warning("Force-terminating busy MPS embedding worker during shutdown")
            process.terminate()
            process.join(timeout=_WORKER_JOIN_TIMEOUT_SECONDS)


class MPSWorkerSentenceTransformerEmbedder(schema.VectorSchemaProvider):
    """SentenceTransformer embedder whose Metal allocations live in a child process."""

    def __init__(
        self,
        model_name_or_path: str,
        *,
        device: str | None = "mps",
        trust_remote_code: bool = False,
        batch_size: int = _MPS_DEFAULT_BATCH_SIZE,
        memory_limit_ratio: float = 0.35,
        worker_timeout_seconds: float = 300.0,
    ) -> None:
        self._model_name_or_path = model_name_or_path
        self._device = device
        self._trust_remote_code = trust_remote_code
        self._batch_size = batch_size
        self._memory_limit_ratio = memory_limit_ratio
        self._worker_timeout_seconds = worker_timeout_seconds
        self._worker: EmbeddingWorkerClient | None = None
        self._worker_lock = threading.Lock()

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def memory_limit_ratio(self) -> float:
        return self._memory_limit_ratio

    @property
    def worker_timeout_seconds(self) -> float:
        return self._worker_timeout_seconds

    def __getstate__(self) -> dict[str, Any]:
        return {
            "model_name_or_path": self._model_name_or_path,
            "device": self._device,
            "trust_remote_code": self._trust_remote_code,
            "batch_size": self._batch_size,
            "memory_limit_ratio": self._memory_limit_ratio,
            "worker_timeout_seconds": self._worker_timeout_seconds,
        }

    def __setstate__(self, state: dict[str, Any]) -> None:
        self._model_name_or_path = state["model_name_or_path"]
        self._device = state["device"]
        self._trust_remote_code = state["trust_remote_code"]
        self._batch_size = state["batch_size"]
        self._memory_limit_ratio = state["memory_limit_ratio"]
        self._worker_timeout_seconds = state["worker_timeout_seconds"]
        self._worker = None
        self._worker_lock = threading.Lock()

    def _get_worker(self) -> EmbeddingWorkerClient:
        if self._worker is None:
            with self._worker_lock:
                if self._worker is None:
                    self._worker = EmbeddingWorkerClient(
                        MPSWorkerConfig(
                            model_name=self._model_name_or_path,
                            device=self._device,
                            trust_remote_code=self._trust_remote_code,
                            batch_size=self._batch_size,
                            memory_limit_ratio=self._memory_limit_ratio,
                        ),
                        timeout_seconds=self._worker_timeout_seconds,
                    )
        return self._worker

    def _request_with_crash_retry(self, operation: str, payload: Any) -> Any:
        for attempt in range(2):
            try:
                return self._get_worker().request(operation, payload)
            except (EmbeddingWorkerCrashedError, EmbeddingWorkerTimeoutError):
                if attempt == 1:
                    raise
                logger.warning("Restarting embedding worker after %s failure", operation)
                if operation == "embed" and isinstance(payload, dict):
                    payload = dict(payload)
                    payload["batch_size"] = max(1, int(payload["batch_size"]) // 2)
        raise AssertionError("unreachable")

    @coco.fn.as_async(batching=True, runner=coco.GPU, max_batch_size=64)
    def _embed(
        self,
        texts: list[str],
        prompt_name: str | None = None,
        normalize_embeddings: bool = True,
    ) -> list[NDArray[np.float32]]:
        result = cast(
            NDArray[np.float32],
            self._request_with_crash_retry(
                "embed",
                {
                    "texts": texts,
                    "batch_size": self._batch_size,
                    "prompt_name": prompt_name,
                    "normalize_embeddings": normalize_embeddings,
                },
            ),
        )
        return list(result)

    @coco.fn(memo=True, version=2, logic_tracking="self")
    async def embed(
        self,
        text: str,
        prompt_name: str | None = None,
        normalize_embeddings: bool = True,
    ) -> NDArray[np.float32]:
        result: NDArray[np.float32] = await self._embed(
            text,
            prompt_name,
            normalize_embeddings,
        )
        return result

    @coco.fn.as_async(runner=coco.GPU, memo=True)
    def dimension(self) -> int:
        return int(self._request_with_crash_retry("dimension", None))

    async def __coco_vector_schema__(self) -> schema.VectorSchema:
        return schema.VectorSchema(dtype=np.dtype(np.float32), size=await self.dimension())

    def __coco_memo_key__(self) -> object:
        return (
            self._model_name_or_path,
            self._device,
            self._trust_remote_code,
            self._batch_size,
            self._memory_limit_ratio,
        )

    def close(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.close()
            self._worker = None
