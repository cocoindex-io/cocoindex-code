"""Shared singletons: config, embedder, and CocoIndex lifecycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

import cocoindex as coco
from cocoindex.connectors import sqlite
from cocoindex.connectors.localfs import FilePath
from numpy.typing import NDArray

if TYPE_CHECKING:
    from cocoindex.ops.litellm import LiteLLMEmbedder
    from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder

from .config import config

logger = logging.getLogger(__name__)

SBERT_PREFIX = "sbert/"

# Initialize embedder at module level based on model prefix
embedder: SentenceTransformerEmbedder | LiteLLMEmbedder
if config.embedding_model.startswith(SBERT_PREFIX):
    from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder

    _model_name = config.embedding_model[len(SBERT_PREFIX) :]
    # Models that define a "query" prompt for asymmetric retrieval.
    _QUERY_PROMPT_MODELS = {"nomic-ai/nomic-embed-code", "nomic-ai/CodeRankEmbed"}
    query_prompt_name: str | None = "query" if _model_name in _QUERY_PROMPT_MODELS else None
    embedder = SentenceTransformerEmbedder(
        _model_name,
        device=config.device,
        trust_remote_code=True,
    )
    logger.info(
        "Embedding model: %s | device: %s",
        config.embedding_model,
        config.device,
    )
else:
    from cocoindex.ops.litellm import LiteLLMEmbedder

    embedder = LiteLLMEmbedder(config.embedding_model)
    query_prompt_name = None
    logger.info("Embedding model (LiteLLM): %s", config.embedding_model)

# Context key for SQLite database (connection managed in lifespan)
SQLITE_DB = coco.ContextKey[sqlite.SqliteDatabase]("sqlite_db")
# Context key for codebase root directory (provided in lifespan)
CODEBASE_DIR = coco.ContextKey[FilePath]("codebase_dir")


@dataclass
class CodeChunk:
    """Schema for storing code chunks in SQLite."""

    id: int
    file_path: str
    language: str
    content: str
    start_line: int
    end_line: int
    embedding: Annotated[NDArray, embedder]  # type: ignore[type-arg]
