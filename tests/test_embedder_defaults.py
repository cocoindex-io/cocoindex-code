"""Tests for the curated default-params table."""

from __future__ import annotations

import re

import pytest

from cocoindex_code.embedder_defaults import (
    _DEFAULT_PARAMS,
    LEGACY_QUERY_PROMPT_MODELS,
    DefaultParamsEntry,
    _assert_legacy_bridge_invariant,
    lookup_defaults,
)


def test_lookup_defaults_exact_match() -> None:
    indexing, query = lookup_defaults("sentence-transformers", "nomic-ai/CodeRankEmbed")
    assert indexing == {}
    assert query == {"prompt_name": "query"}


def test_lookup_defaults_regex_match_snowflake() -> None:
    indexing, query = lookup_defaults(
        "sentence-transformers", "Snowflake/snowflake-arctic-embed-xs"
    )
    assert indexing == {}
    assert query == {"prompt_name": "query"}


def test_lookup_defaults_regex_match_voyage() -> None:
    indexing, query = lookup_defaults("litellm", "voyage/voyage-3")
    assert indexing == {"input_type": "document"}
    assert query == {"input_type": "query"}


def test_lookup_defaults_regex_match_cohere() -> None:
    indexing, query = lookup_defaults("litellm", "cohere/embed-english-v3.0")
    assert indexing == {"input_type": "search_document"}
    assert query == {"input_type": "search_query"}


def test_lookup_defaults_regex_match_gemini_embedding_001() -> None:
    indexing, query = lookup_defaults("litellm", "gemini/gemini-embedding-001")
    assert indexing == {"input_type": "RETRIEVAL_DOCUMENT"}
    assert query == {"input_type": "RETRIEVAL_QUERY"}


def test_lookup_defaults_regex_match_gemini_text_embedding_legacy() -> None:
    indexing, query = lookup_defaults("litellm", "gemini/text-embedding-004")
    assert indexing == {"input_type": "RETRIEVAL_DOCUMENT"}
    assert query == {"input_type": "RETRIEVAL_QUERY"}


def test_lookup_defaults_openai_no_match() -> None:
    """OpenAI embeddings are symmetric — no recommended params."""
    assert lookup_defaults("litellm", "openai/text-embedding-3-small") == (None, None)
    assert lookup_defaults("litellm", "text-embedding-3-large") == (None, None)


def test_lookup_defaults_no_match() -> None:
    assert lookup_defaults("litellm", "openai/text-embedding-3-small") == (None, None)


def test_lookup_defaults_provider_mismatch() -> None:
    # litellm/voyage regex should not match when provider is sentence-transformers
    assert lookup_defaults("sentence-transformers", "voyage/voyage-3") == (None, None)


def test_lookup_defaults_exact_takes_priority_over_regex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact entries placed before regex entries are returned first."""
    table = [
        DefaultParamsEntry(
            "litellm",
            "voyage/voyage-3",
            {"input_type": "OVERRIDDEN_DOC"},
            {"input_type": "OVERRIDDEN_QUERY"},
        ),
        DefaultParamsEntry(
            "litellm",
            re.compile(r"voyage/.+"),
            {"input_type": "document"},
            {"input_type": "query"},
        ),
    ]
    monkeypatch.setattr("cocoindex_code.embedder_defaults._DEFAULT_PARAMS", table)
    indexing, query = lookup_defaults("litellm", "voyage/voyage-3")
    assert indexing == {"input_type": "OVERRIDDEN_DOC"}
    assert query == {"input_type": "OVERRIDDEN_QUERY"}


def test_lookup_defaults_returns_fresh_dicts() -> None:
    """Callers can mutate the returned dicts without corrupting the table."""
    _, query1 = lookup_defaults("sentence-transformers", "nomic-ai/CodeRankEmbed")
    assert query1 is not None
    query1["prompt_name"] = "mutated"
    _, query2 = lookup_defaults("sentence-transformers", "nomic-ai/CodeRankEmbed")
    assert query2 == {"prompt_name": "query"}


def test_legacy_models_have_matching_defaults() -> None:
    """Every legacy model must have an exact sentence-transformers entry with
    query_params={'prompt_name': 'query'}.
    """
    for model in LEGACY_QUERY_PROMPT_MODELS:
        _, query = lookup_defaults("sentence-transformers", model)
        assert query == {"prompt_name": "query"}


def test_legacy_bridge_invariant_assertion_detects_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The invariant check should raise when a legacy model has no matching entry."""
    # Strip all sentence-transformers entries so no legacy model has a match.
    stripped = [e for e in _DEFAULT_PARAMS if e.provider != "sentence-transformers"]
    monkeypatch.setattr("cocoindex_code.embedder_defaults._DEFAULT_PARAMS", stripped)
    with pytest.raises(AssertionError, match="has no matching"):
        _assert_legacy_bridge_invariant()
