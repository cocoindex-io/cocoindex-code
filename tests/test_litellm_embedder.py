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


@pytest.mark.parametrize(
    ("constructor_prompt", "call_prompt", "expected_input"),
    [
        (None, "title: none | text: ", "title: none | text: fn a()"),
        ("title: none | text: ", None, "title: none | text: fn a()"),
        ("title: none | text: ", "query: ", "query: fn a()"),
    ],
)
@pytest.mark.asyncio
async def test_embed_prepends_prompt_without_sending_it(
    monkeypatch: pytest.MonkeyPatch,
    constructor_prompt: str | None,
    call_prompt: str | None,
    expected_input: str,
) -> None:
    seen: list[tuple[list[str], dict[str, Any]]] = []

    async def fake_aembedding(*, model: str, input: list[str], **kwargs: Any) -> Any:
        assert model == "openai/embeddinggemma-2"
        seen.append((input, kwargs))
        return SimpleNamespace(data=[{"embedding": [float(len(text))]} for text in input])

    monkeypatch.setattr("cocoindex_code.litellm_embedder.litellm.aembedding", fake_aembedding)

    constructor_kwargs = {} if constructor_prompt is None else {"prompt": constructor_prompt}
    embedder = PacedLiteLLMEmbedder("openai/embeddinggemma-2", **constructor_kwargs)
    call_kwargs = {} if call_prompt is None else {"prompt": call_prompt}
    vector = await embedder.embed("fn a()", **call_kwargs)

    assert seen == [([expected_input], {"encoding_format": "float", "drop_params": True})]
    assert vector.tolist() == [float(len(expected_input))]
