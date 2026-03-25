"""Shared context keys, embedder factory, and CodeChunk schema."""

from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Union

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

# Models that define a "query" prompt for asymmetric retrieval.
_QUERY_PROMPT_MODELS = {"nomic-ai/nomic-embed-code", "nomic-ai/CodeRankEmbed"}

# Type alias
Embedder = Union["SentenceTransformerEmbedder", "LiteLLMEmbedder"]

# Context keys
EMBEDDER = coco.ContextKey[Embedder]("embedder")
SQLITE_DB = coco.ContextKey[sqlite.ManagedConnection]("index_db", tracked=False)
CODEBASE_DIR = coco.ContextKey[pathlib.Path]("codebase", tracked=False)

# Module-level variable — set by daemon at startup (needed for CodeChunk annotation).
embedder: Embedder | None = None

# Query prompt name — set alongside embedder by create_embedder().
query_prompt_name: str | None = None


def create_embedder(settings: EmbeddingSettings) -> Embedder:
    """Create and return an embedder instance based on settings.

    Also sets the module-level ``embedder`` and ``query_prompt_name`` variables.
    """
    global embedder, query_prompt_name

    if settings.provider == "sentence-transformers":
        from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder

        model_name = settings.model
        # Strip the legacy sbert/ prefix if present
        if model_name.startswith(SBERT_PREFIX):
            model_name = model_name[len(SBERT_PREFIX) :]

        query_prompt_name = "query" if model_name in _QUERY_PROMPT_MODELS else None
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
        )
        query_prompt_name = None
        logger.info(
            "Embedding model (LiteLLM): %s | min_interval_ms: %s",
            settings.model,
            min_interval_ms,
        )

    embedder = instance
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
