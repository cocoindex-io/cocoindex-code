"""Data models for CocoIndex Code."""

from dataclasses import dataclass
from typing import Any


@dataclass
class CodeChunk:
    """Represents an indexed code chunk stored in SQLite."""

    id: int
    file_path: str
    language: str
    content: str
    start_line: int
    end_line: int
    embedding: Any  # NDArray - type hint relaxed for compatibility


@dataclass
class TqChunkRow:
    """A code chunk stored in the TurboQuant compressed backend.

    Mirrors :class:`CodeChunk` minus the raw ``embedding`` (which is replaced by
    the quantized representation). ``idx_packed`` holds the bit-packed MSE-stage
    codebook indices, ``qjl_packed`` the bit-packed QJL sign vector,
    ``residual_norm`` the L2 norm of the unit-space residual, and ``norm`` the
    original embedding's L2 norm.
    """

    id: int
    file_path: str
    language: str
    content: str
    start_line: int
    end_line: int
    idx_packed: bytes
    qjl_packed: bytes
    residual_norm: float
    norm: float


@dataclass
class QueryResult:
    """Result from a vector similarity query."""

    file_path: str
    language: str
    content: str
    start_line: int
    end_line: int
    score: float
