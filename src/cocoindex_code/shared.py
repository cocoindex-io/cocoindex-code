"""Shared resources for CocoIndex Code."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

import cocoindex as coco
from cocoindex.connectors import sqlite
from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder
from numpy.typing import NDArray

from .config import Config

# Load configuration at module level
config = Config.from_env()

# Initialize embedder at module level
embedder = SentenceTransformerEmbedder(config.embedding_model)

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
