"""Shared resources for CocoIndex Code."""

from dataclasses import dataclass
from typing import Annotated

from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder
from numpy.typing import NDArray

from .config import Config

# Load configuration at module level
config = Config.from_env()

# Initialize embedder at module level
embedder = SentenceTransformerEmbedder(config.embedding_model)


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
