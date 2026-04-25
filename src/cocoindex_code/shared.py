"""Shared context keys, embedder factory, and CodeChunk schema."""

from __future__ import annotations

import importlib.util
import logging
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, NamedTuple, Union

import cocoindex as coco
import numpy as np
import numpy.typing as npt
from cocoindex.connectors import sqlite

if TYPE_CHECKING:
    from cocoindex.ops.litellm import LiteLLMEmbedder
    from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder

from .settings import EmbeddingSettings

logger = logging.getLogger(__name__)

SBERT_PREFIX = "sbert/"
DEFAULT_LITELLM_MIN_INTERVAL_MS = 5

# Type alias
Embedder = Union["SentenceTransformerEmbedder", "LiteLLMEmbedder"]

# Context keys
EMBEDDER = coco.ContextKey[Embedder]("embedder", detect_change=True)
SQLITE_DB = coco.ContextKey[sqlite.ManagedConnection]("index_db")
CODEBASE_DIR = coco.ContextKey[pathlib.Path]("codebase")
INDEXING_EMBED_PARAMS = coco.ContextKey[dict[str, Any]]("indexing_embed_params")
QUERY_EMBED_PARAMS = coco.ContextKey[dict[str, Any]]("query_embed_params")


def is_sentence_transformers_installed() -> bool:
    """Return True if the `sentence_transformers` package can be imported.

    Uses `find_spec` rather than `import` to avoid triggering the slow,
    torch-loading import as a side effect of the check.
    """
    return importlib.util.find_spec("sentence_transformers") is not None


class EmbeddingCheckResult(NamedTuple):
    """Outcome of a single embed-test call. See `check_embedding`.

    Exactly one of ``dim`` / ``error`` is set: ``error is None`` means success.
    """

    dim: int | None
    error: str | None


async def check_embedding(
    embedder: Embedder,
    params: dict[str, Any] | None = None,
) -> EmbeddingCheckResult:
    """Run a single embed call against *embedder* and report dim or error.

    *params* are spread into ``embed()`` so callers can verify indexing vs
    query params separately (they may use different keys at runtime).

    Never raises. Used by the daemon's doctor path (`daemon._check_model`).
    """
    kwargs = dict(params) if params else {}
    try:
        vec = await embedder.embed("hello world", **kwargs)
        return EmbeddingCheckResult(dim=len(vec), error=None)
    except Exception as e:
        msg = " ".join(f"{type(e).__name__}: {e}".splitlines())
        if len(msg) > 500:
            msg = msg[:500] + "…"
        return EmbeddingCheckResult(dim=None, error=msg)


def create_embedder(
    settings: EmbeddingSettings,
    indexing_params: dict[str, Any] | None = None,
) -> Embedder:
    """Create and return an embedder instance based on settings.

    For LiteLLM embedders, *indexing_params* (e.g. ``{"input_type": "passage"}``)
    are passed to the constructor as default kwargs forwarded into every
    ``litellm.aembedding`` call — including paths that don't go through
    :data:`INDEXING_EMBED_PARAMS` (e.g. the dimension probe in ``_get_dim``,
    or any helper that calls ``embed()`` with no per-side kwargs). Per-call
    overrides (the ``query_params`` spread at query time) still take effect
    because :meth:`LiteLLMEmbedder._embed` overlays kwargs on top of the
    constructor's ``self._kwargs``.

    *indexing_params* is ignored for sentence-transformers — its constructor
    doesn't accept arbitrary kwargs; ``prompt_name`` is a per-call argument
    only and the indexing default is supplied at the call site via
    :data:`INDEXING_EMBED_PARAMS`.
    """
    if settings.provider == "sentence-transformers":
        from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder

        model_name = settings.model
        # Strip the legacy sbert/ prefix if present
        if model_name.startswith(SBERT_PREFIX):
            model_name = model_name[len(SBERT_PREFIX) :]

        instance: Embedder = SentenceTransformerEmbedder(
            model_name,
            device=settings.device,
            trust_remote_code=True,
        )
        logger.info("Embedding model: %s | device: %s", settings.model, settings.device)
    else:
        from .litellm_embedder import PacedLiteLLMEmbedder

        min_interval_ms = (
            settings.min_interval_ms
            if settings.min_interval_ms is not None
            else DEFAULT_LITELLM_MIN_INTERVAL_MS
        )
        instance = PacedLiteLLMEmbedder(
            settings.model,
            min_interval_ms=min_interval_ms,
            **(dict(indexing_params) if indexing_params else {}),
        )
        logger.info(
            "Embedding model (LiteLLM): %s | min_interval_ms: %s",
            settings.model,
            min_interval_ms,
        )

    return instance


@dataclass
class CodeChunk:
    """Schema for storing code chunks in SQLite."""

    id: int
    file_path: str
    language: str
    content: str
    start_line: int
    end_line: int
    embedding: Annotated[npt.NDArray[np.float32], EMBEDDER]
