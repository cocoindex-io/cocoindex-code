"""Local SentenceTransformer embedder with device and trust_remote_code support."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

import cocoindex as coco
import cocoindex.asyncio as coco_aio
import numpy as np
from cocoindex.resources import schema as _schema
from numpy.typing import NDArray

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class LocalEmbedder(_schema.VectorSchemaProvider):
    """SentenceTransformer embedder with explicit device and trust_remote_code support.

    Drop-in replacement for cocoindex's SentenceTransformerEmbedder that supports:
    - Explicit device selection (e.g. "cuda", "cpu")
    - trust_remote_code for models with custom pooling (e.g. Jina models)
    """

    def __init__(
        self,
        model_name_or_path: str,
        *,
        device: str = "cpu",
        trust_remote_code: bool = False,
        normalize_embeddings: bool = True,
        query_prompt_name: str | None = None,
    ) -> None:
        self._model_name_or_path = model_name_or_path
        self._device = device
        self._trust_remote_code = trust_remote_code
        self._normalize_embeddings = normalize_embeddings
        self._query_prompt_name = query_prompt_name
        self._model: SentenceTransformer | None = None
        self._lock = threading.Lock()

    def __getstate__(self) -> dict[str, Any]:
        return {
            "model_name_or_path": self._model_name_or_path,
            "device": self._device,
            "trust_remote_code": self._trust_remote_code,
            "normalize_embeddings": self._normalize_embeddings,
            "query_prompt_name": self._query_prompt_name,
        }

    def __setstate__(self, state: dict[str, Any]) -> None:
        self._model_name_or_path = state["model_name_or_path"]
        self._device = state["device"]
        self._trust_remote_code = state["trust_remote_code"]
        self._normalize_embeddings = state["normalize_embeddings"]
        self._query_prompt_name = state.get("query_prompt_name")
        self._model = None
        self._lock = threading.Lock()

    def _get_model(self) -> SentenceTransformer:
        """Lazy-load the model with thread-safe double-checked locking."""
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(
                        self._model_name_or_path,
                        device=self._device,
                        trust_remote_code=self._trust_remote_code,
                    )
        return self._model

    @coco_aio.function(batching=True, runner=coco.GPU, memo=True, max_batch_size=16)
    def embed(self, texts: list[str]) -> list[NDArray[np.float32]]:
        """Embed a batch of texts into float32 vectors."""
        model = self._get_model()
        embeddings: NDArray[np.float32] = model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=self._normalize_embeddings,
        )  # type: ignore[assignment]
        return list(embeddings)

    @coco_aio.function(batching=True, runner=coco.GPU, memo=True, max_batch_size=16)
    def embed_query(self, texts: list[str]) -> list[NDArray[np.float32]]:
        """Embed query texts, applying query_prompt_name if configured."""
        model = self._get_model()
        embeddings: NDArray[np.float32] = model.encode(
            texts,
            prompt_name=self._query_prompt_name,
            convert_to_numpy=True,
            normalize_embeddings=self._normalize_embeddings,
        )  # type: ignore[assignment]
        return list(embeddings)

    @coco_aio.function(runner=coco.GPU, memo=True)
    def __coco_vector_schema__(self) -> _schema.VectorSchema:
        """Return the vector schema (dimension + dtype) for this model."""
        model = self._get_model()
        dim = model.get_sentence_embedding_dimension()
        if dim is None:
            raise RuntimeError(
                f"Embedding dimension is unknown for model {self._model_name_or_path}."
            )
        return _schema.VectorSchema(dtype=np.dtype(np.float32), size=dim)

    def __coco_memo_key__(self) -> object:
        return (
            self._model_name_or_path,
            self._device,
            self._trust_remote_code,
            self._normalize_embeddings,
            self._query_prompt_name,
        )
