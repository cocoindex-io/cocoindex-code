"""Tests for validate_params and resolve_embedder_params."""

from __future__ import annotations

import pytest

from cocoindex_code.embedder_params import (
    EmbedderParams,
    resolve_embedder_params,
    validate_params,
)
from cocoindex_code.settings import EmbeddingSettings


def test_validate_params_accepts_known_keys() -> None:
    validate_params("sentence-transformers", {}, {"prompt_name": "query"})
    validate_params(
        "litellm", {"input_type": "passage"}, {"input_type": "query", "dimensions": 512}
    )


def test_validate_params_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="input_type"):
        validate_params("sentence-transformers", {"input_type": "passage"}, {})


def test_validate_params_rejects_excluded_normalize_embeddings() -> None:
    with pytest.raises(ValueError, match="normalize_embeddings"):
        validate_params("sentence-transformers", {"normalize_embeddings": False}, {})


def test_validate_params_rejects_excluded_encoding_format() -> None:
    with pytest.raises(ValueError, match="encoding_format"):
        validate_params("litellm", {"encoding_format": "base64"}, {})


def test_validate_params_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unknown provider"):
        validate_params("nonsense", {}, {})


def test_resolve_embedder_params_user_set_verbatim() -> None:
    settings = EmbeddingSettings(
        provider="litellm",
        model="cohere/embed-english-v3.0",
        indexing_params={"input_type": "search_document"},
        query_params={"input_type": "search_query"},
    )
    assert resolve_embedder_params(settings) == EmbedderParams(
        indexing={"input_type": "search_document"},
        query={"input_type": "search_query"},
        used_backward_compat=False,
    )


def test_resolve_embedder_params_empty_query_suppresses_legacy_bridge() -> None:
    settings = EmbeddingSettings(
        provider="sentence-transformers",
        model="nomic-ai/CodeRankEmbed",
        indexing_params=None,
        query_params={},
    )
    assert resolve_embedder_params(settings) == EmbedderParams(
        indexing={}, query={}, used_backward_compat=False
    )


def test_resolve_embedder_params_empty_indexing_suppresses_legacy_bridge() -> None:
    settings = EmbeddingSettings(
        provider="sentence-transformers",
        model="nomic-ai/CodeRankEmbed",
        indexing_params={},
        query_params=None,
    )
    # indexing_params set but query_params None — should still NOT fire the
    # legacy bridge because the user has expressed intent.
    assert resolve_embedder_params(settings) == EmbedderParams(
        indexing={}, query={}, used_backward_compat=False
    )


def test_resolve_embedder_params_legacy_bridge_fires() -> None:
    settings = EmbeddingSettings(
        provider="sentence-transformers",
        model="nomic-ai/CodeRankEmbed",
    )
    assert resolve_embedder_params(settings) == EmbedderParams(
        indexing={},
        query={"prompt_name": "query"},
        used_backward_compat=True,
    )


def test_resolve_embedder_params_legacy_bridge_only_for_legacy_models() -> None:
    settings = EmbeddingSettings(
        provider="sentence-transformers",
        model="nomic-ai/nomic-embed-text-v1.5",  # not in LEGACY_QUERY_PROMPT_MODELS
    )
    assert resolve_embedder_params(settings) == EmbedderParams(
        indexing={}, query={}, used_backward_compat=False
    )


def test_resolve_embedder_params_no_match_returns_empty() -> None:
    settings = EmbeddingSettings(
        provider="litellm",
        model="openai/text-embedding-3-small",
    )
    assert resolve_embedder_params(settings) == EmbedderParams(
        indexing={}, query={}, used_backward_compat=False
    )


def test_resolve_embedder_params_rejects_invalid_user_config() -> None:
    settings = EmbeddingSettings(
        provider="sentence-transformers",
        model="anything",
        indexing_params={"prompt_name": "x"},
        query_params={"input_type": "y"},  # invalid for sentence-transformers
    )
    with pytest.raises(ValueError):
        resolve_embedder_params(settings)
