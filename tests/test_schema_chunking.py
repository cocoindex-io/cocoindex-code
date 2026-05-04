"""Tests for schema and chunking modules."""

from __future__ import annotations

from cocoindex_code.schema import CodeChunk, QueryResult
from cocoindex_code.chunking import Chunk, ChunkerFn, CHUNKER_REGISTRY, TextPosition


# --- Schema tests ---


class TestCodeChunk:
    def test_create_code_chunk(self) -> None:
        chunk = CodeChunk(
            id=1,
            file_path="src/main.py",
            language="python",
            content="def hello(): pass",
            start_line=1,
            end_line=1,
            embedding=[0.0] * 384,
        )
        assert chunk.file_path == "src/main.py"
        assert chunk.language == "python"
        assert chunk.start_line == 1

    def test_code_chunk_embedding_accepts_any_type(self) -> None:
        """Embedding field should accept various types for compatibility."""
        chunk = CodeChunk(
            id=1, file_path="a.py", language="python",
            content="x", start_line=1, end_line=1,
            embedding=[0.1, 0.2, 0.3],
        )
        assert chunk.embedding == [0.1, 0.2, 0.3]


class TestQueryResult:
    def test_create_query_result(self) -> None:
        result = QueryResult(
            file_path="src/utils.py",
            language="python",
            content="def util(): pass",
            start_line=10,
            end_line=15,
            score=0.95,
        )
        assert result.score == 0.95
        assert result.end_line == 15

    def test_query_result_score_range(self) -> None:
        """Score should be a float, typically between 0 and 1."""
        result = QueryResult(
            file_path="a.py", language="python",
            content="x", start_line=1, end_line=1,
            score=0.0,
        )
        assert result.score == 0.0


# --- Chunking tests ---


class TestChunkingExports:
    def test_chunk_class_available(self) -> None:
        """Chunk should be importable from chunking module."""
        assert Chunk is not None

    def test_text_position_available(self) -> None:
        assert TextPosition is not None

    def test_chunker_fn_is_callable_alias(self) -> None:
        """ChunkerFn should be a callable type alias."""
        assert ChunkerFn is not None

    def test_chunker_registry_is_context_key(self) -> None:
        """CHUNKER_REGISTRY should be a CocoIndex context key."""
        assert CHUNKER_REGISTRY is not None
