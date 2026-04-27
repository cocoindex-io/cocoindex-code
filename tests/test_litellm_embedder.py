"""Unit tests for the paced LiteLLM embedder."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from cocoindex_code.litellm_embedder import PacedLiteLLMEmbedder


@pytest.mark.asyncio
async def test_run_embedding_request_retries_rate_limit_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    attempts = 0

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    async def fake_aembedding(*, model: str, input: list[str], **kwargs: Any) -> Any:
        nonlocal attempts
        attempts += 1
        assert model == "text-embedding-3-small"
        assert input == ["hello"]
        assert kwargs == {"encoding_format": "float", "drop_params": True}
        if attempts == 1:
            raise Exception("Rate limit exceeded. Please try again in 250ms")
        return SimpleNamespace(data=[{"embedding": [1.0, 2.0]}])

    monkeypatch.setattr("cocoindex_code.litellm_embedder.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("cocoindex_code.litellm_embedder.litellm.aembedding", fake_aembedding)

    embedder = PacedLiteLLMEmbedder("text-embedding-3-small")
    response = await embedder.run_embedding_request(input=["hello"])

    assert attempts == 2
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(0.35)
    assert response.data == [{"embedding": [1.0, 2.0]}]


@pytest.mark.asyncio
async def test_run_embedding_request_applies_min_interval_between_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    inputs_seen: list[list[str]] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    async def fake_aembedding(*, model: str, input: list[str], **kwargs: Any) -> Any:
        assert model == "text-embedding-3-small"
        assert kwargs == {"encoding_format": "float", "drop_params": True}
        inputs_seen.append(input)
        return SimpleNamespace(data=[{"embedding": [1.0, 2.0]}])

    monkeypatch.setattr("cocoindex_code.litellm_embedder.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("cocoindex_code.litellm_embedder.litellm.aembedding", fake_aembedding)
    monkeypatch.setattr("cocoindex_code.litellm_embedder.time.monotonic", lambda: 10.0)

    embedder = PacedLiteLLMEmbedder("text-embedding-3-small", min_interval_ms=300)
    embedder._next_request_at = 10.3

    await embedder.run_embedding_request(input=["second"])

    assert inputs_seen == [["second"]]
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(0.3)


@pytest.mark.parametrize(
    "model_name", ["voyage/voyage-code-3", "bedrock/amazon.titan-embed-text-v2:0"]
)
@pytest.mark.asyncio
async def test_run_embedding_request_omits_encoding_format_for_native_providers(
    monkeypatch: pytest.MonkeyPatch, model_name: str
) -> None:
    seen_kwargs: dict[str, Any] = {}

    async def fake_aembedding(*, model: str, input: list[str], **kwargs: Any) -> Any:
        assert model == model_name
        assert input == ["hello"]
        seen_kwargs.update(kwargs)
        return SimpleNamespace(data=[{"embedding": [1.0, 2.0]}])

    monkeypatch.setattr("cocoindex_code.litellm_embedder.litellm.aembedding", fake_aembedding)

    embedder = PacedLiteLLMEmbedder(model_name)
    await embedder.run_embedding_request(input=["hello"])

    assert "encoding_format" not in seen_kwargs
    assert "drop_params" not in seen_kwargs
