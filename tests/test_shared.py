"""Tests for embedder creation helpers."""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pytest

from cocoindex_code.litellm_embedder import PacedLiteLLMEmbedder
from cocoindex_code.mps_embedder import MPSWorkerSentenceTransformerEmbedder
from cocoindex_code.settings import EmbeddingSettings
from cocoindex_code.shared import (
    check_embedding,
    create_embedder,
    is_sentence_transformers_installed,
)


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


def test_create_embedder_litellm_passes_indexing_params_as_constructor_default() -> None:
    """Indexing params become default kwargs forwarded into every litellm call —
    covering paths that don't go through INDEXING_EMBED_PARAMS (dim probe, etc.).
    """
    embedder = create_embedder(
        EmbeddingSettings(provider="litellm", model="cohere/embed-english-v3.0"),
        indexing_params={"input_type": "search_document"},
    )
    assert isinstance(embedder, PacedLiteLLMEmbedder)
    assert embedder._kwargs == {"input_type": "search_document"}


def test_create_embedder_sentence_transformers_ignores_indexing_params() -> None:
    """The SentenceTransformer constructor doesn't accept arbitrary kwargs;
    indexing_params is silently ignored for that provider.
    """
    embedder = create_embedder(
        EmbeddingSettings(
            provider="sentence-transformers", model="sentence-transformers/all-MiniLM-L6-v2"
        ),
        indexing_params={"prompt_name": "passage"},
    )
    # No exception, and prompt_name is not stashed on the constructor —
    # it's a per-call argument supplied via the embed() call site.
    assert not isinstance(embedder, PacedLiteLLMEmbedder)


def test_create_embedder_uses_isolated_worker_and_allocator_guards_for_mps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTORCH_MPS_HIGH_WATERMARK_RATIO", raising=False)
    monkeypatch.delenv("PYTORCH_MPS_LOW_WATERMARK_RATIO", raising=False)

    embedder = create_embedder(
        EmbeddingSettings(
            provider="sentence-transformers",
            model="sentence-transformers/all-MiniLM-L6-v2",
            device="mps",
            batch_size=4,
            mps_memory_limit_ratio=0.3,
            mps_low_watermark_ratio=0.4,
            mps_high_watermark_ratio=0.5,
            worker_timeout_seconds=90,
        )
    )

    assert isinstance(embedder, MPSWorkerSentenceTransformerEmbedder)
    assert embedder.batch_size == 4
    assert embedder.memory_limit_ratio == 0.3
    assert embedder.worker_timeout_seconds == 90
    assert os.environ["PYTORCH_MPS_LOW_WATERMARK_RATIO"] == "0.4"
    assert os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] == "0.5"


def test_explicit_mps_allocator_env_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTORCH_MPS_LOW_WATERMARK_RATIO", "0.25")
    monkeypatch.setenv("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.3")

    create_embedder(
        EmbeddingSettings(
            provider="sentence-transformers",
            model="sentence-transformers/all-MiniLM-L6-v2",
            device="mps",
        )
    )

    assert os.environ["PYTORCH_MPS_LOW_WATERMARK_RATIO"] == "0.25"
    assert os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] == "0.3"


def test_is_sentence_transformers_installed_true_in_dev() -> None:
    # Dev env pulls in sentence-transformers via the `dev` extras group.
    assert is_sentence_transformers_installed() is True


def test_is_sentence_transformers_installed_false_when_find_spec_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert is_sentence_transformers_installed() is False


class _StubOkEmbedder:
    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] | None = None

    async def embed(self, text: str, **kwargs: Any) -> Any:
        self.last_kwargs = dict(kwargs)
        return np.zeros(384, dtype=np.float32)


class _StubErrEmbedder:
    async def embed(self, text: str, **kwargs: Any) -> Any:
        raise RuntimeError("boom")


async def test_check_embedding_ok() -> None:
    result = await check_embedding(_StubOkEmbedder())
    assert result.error is None
    assert result.dim == 384
    assert result.traceback is None


async def test_check_embedding_error() -> None:
    result = await check_embedding(_StubErrEmbedder())
    assert result.dim is None
    assert result.error is not None
    assert result.error.startswith("RuntimeError:")
    assert "boom" in result.error
    # The full traceback is captured so `ccc doctor` can surface it for debugging.
    assert result.traceback is not None
    assert "Traceback (most recent call last):" in result.traceback
    assert "boom" in result.traceback


async def test_check_embedding_forwards_params() -> None:
    stub = _StubOkEmbedder()
    await check_embedding(stub, {"prompt_name": "passage"})
    assert stub.last_kwargs == {"prompt_name": "passage"}


async def test_check_embedding_no_params_forwards_empty() -> None:
    stub = _StubOkEmbedder()
    await check_embedding(stub)
    assert stub.last_kwargs == {}
