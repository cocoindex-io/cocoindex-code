"""Shared singletons: config, embedder, and CocoIndex lifecycle."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

import cocoindex as coco
from cocoindex.connectors import sqlite
from numpy.typing import NDArray

if TYPE_CHECKING:
    from cocoindex.ops.litellm import LiteLLMEmbedder

    from .embedder import LocalEmbedder

from .config import config

logger = logging.getLogger(__name__)

SBERT_PREFIX = "sbert/"

# Initialize embedder at module level based on model prefix
embedder: LocalEmbedder | LiteLLMEmbedder
if config.embedding_model.startswith(SBERT_PREFIX):
    from .embedder import LocalEmbedder

    _model_name = config.embedding_model[len(SBERT_PREFIX) :]
    # Models that define a "query" prompt for asymmetric retrieval.
    _QUERY_PROMPT_MODELS = {"nomic-ai/nomic-embed-code", "nomic-ai/CodeRankEmbed"}
    _query_prompt_name: str | None = "query" if _model_name in _QUERY_PROMPT_MODELS else None
    # Models whose custom remote code is known-compatible with transformers 5.x.
    _KNOWN_REMOTE_CODE_MODELS = {"nomic-ai/CodeRankEmbed"}
    _trust = config.trust_remote_code or _model_name in _KNOWN_REMOTE_CODE_MODELS
    embedder = LocalEmbedder(
        _model_name,
        device=config.device,
        trust_remote_code=_trust,
        query_prompt_name=_query_prompt_name,
    )
    logger.info(
        "Embedding model: %s | device: %s | trust_remote_code: %s",
        config.embedding_model,
        config.device,
        _trust,
    )
else:
    from cocoindex.ops.litellm import LiteLLMEmbedder

    embedder = LiteLLMEmbedder(config.embedding_model)
    logger.info("Embedding model (LiteLLM): %s", config.embedding_model)

# Context key for SQLite database (connection managed in lifespan)
SQLITE_DB = coco.ContextKey[sqlite.SqliteDatabase]("sqlite_db")


@coco.lifespan
def coco_lifespan(builder: coco.EnvironmentBuilder) -> Iterator[None]:
    """Set up database connection."""
    # Ensure index directory exists
    config.index_dir.mkdir(parents=True, exist_ok=True)

    # Set CocoIndex state database path
    builder.settings.db_path = config.cocoindex_db_path

    # Connect to SQLite with vector extension
    conn = sqlite.connect(str(config.target_sqlite_db_path), load_vec="auto")
    builder.provide(SQLITE_DB, sqlite.register_db("index_db", conn))

    yield

    conn.close()


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
