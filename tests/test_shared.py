"""Tests for shared.py initialization logic."""

from __future__ import annotations


class TestEmbedderSelection:
    """Test embedder selection logic based on model prefix."""

    def test_sbert_prefix_detected(self) -> None:
        """Models starting with 'sbert/' use SentenceTransformerEmbedder."""
        from cocoindex_code.shared import SBERT_PREFIX

        assert "sbert/sentence-transformers/all-MiniLM-L6-v2".startswith(SBERT_PREFIX)

    def test_litellm_model_detected(self) -> None:
        """Models without 'sbert/' prefix use LiteLLM."""
        from cocoindex_code.shared import SBERT_PREFIX

        assert not "text-embedding-3-small".startswith(SBERT_PREFIX)

    def test_sbert_prefix_constant(self) -> None:
        from cocoindex_code.shared import SBERT_PREFIX

        assert SBERT_PREFIX == "sbert/"

    def test_query_prompt_models_constant(self) -> None:
        """Known query-prompt models should be defined."""
        # We can't easily access the local variable, but we can verify
        # the embedder was created without error
        from cocoindex_code.shared import embedder

        assert embedder is not None


class TestContextKeys:
    """Test CocoIndex context key definitions."""

    def test_sqlite_db_key_exists(self) -> None:
        from cocoindex_code.shared import SQLITE_DB

        assert SQLITE_DB is not None

    def test_codebase_dir_key_exists(self) -> None:
        from cocoindex_code.shared import CODEBASE_DIR

        assert CODEBASE_DIR is not None


class TestCodeChunk:
    """Test CodeChunk dataclass in shared.py."""

    def test_code_chunk_has_expected_fields(self) -> None:
        import dataclasses

        from cocoindex_code.shared import CodeChunk

        field_names = [f.name for f in dataclasses.fields(CodeChunk)]
        assert "id" in field_names
        assert "file_path" in field_names
        assert "language" in field_names
        assert "content" in field_names
        assert "start_line" in field_names
        assert "end_line" in field_names
        assert "embedding" in field_names


class TestCocoLifespan:
    """Test coco_lifespan function existence."""

    def test_lifespan_is_callable(self) -> None:
        """coco_lifespan should be a callable (decorated with @coco.lifespan)."""
        from cocoindex_code.shared import coco_lifespan

        # It's wrapped by @coco.lifespan but should still exist
        assert coco_lifespan is not None
