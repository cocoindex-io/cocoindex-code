"""Tests for embedder creation helpers."""

from __future__ import annotations

from cocoindex_code.litellm_embedder import PacedLiteLLMEmbedder
from cocoindex_code.settings import EmbeddingSettings
from cocoindex_code.shared import create_embedder


def test_create_embedder_uses_default_litellm_pacing() -> None:
    embedder = create_embedder(
        EmbeddingSettings(
            provider="litellm",
            model="text-embedding-3-small",
        )
    )
    assert isinstance(embedder, PacedLiteLLMEmbedder)
    assert embedder._min_request_interval_seconds == 0.005


def test_create_embedder_uses_paced_litellm_embedder() -> None:
    embedder = create_embedder(
        EmbeddingSettings(
            provider="litellm",
            model="text-embedding-3-small",
            min_interval_ms=300,
        )
    )
    assert isinstance(embedder, PacedLiteLLMEmbedder)
    assert embedder._min_request_interval_seconds == 0.3
