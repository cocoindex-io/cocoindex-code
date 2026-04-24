"""LiteLLM embedder with optional request pacing and rate-limit retries."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import cocoindex as coco
import numpy as np
from cocoindex.ops.litellm import LiteLLMEmbedder, litellm
from numpy.typing import NDArray

litellm.drop_params = True

logger = logging.getLogger(__name__)

_RATE_LIMIT_DELAY_RE = re.compile(r"Please try again in ([0-9.]+)(ms|s)", re.IGNORECASE)
_MAX_RATE_LIMIT_RETRIES = 6


def _get_rate_limit_delay(exc: Exception, attempt: int) -> float | None:
    message = str(exc)
    if "rate limit" not in message.lower():
        return None

    match = _RATE_LIMIT_DELAY_RE.search(message)
    if match is not None:
        value = float(match.group(1))
        unit = match.group(2).lower()
        delay = value / 1000.0 if unit == "ms" else value
    else:
        delay = min(0.5 * (2**attempt), 10.0)

    return min(delay + 0.1, 10.0)


class PacedLiteLLMEmbedder(LiteLLMEmbedder):
    """LiteLLM embedder that serializes requests and paces them when configured."""

    def __init__(self, model: str, *, min_interval_ms: int | None = None, **kwargs: Any) -> None:
        super().__init__(model, **kwargs)
        self._min_request_interval_seconds = max(0.0, float(min_interval_ms or 0) / 1000.0)
        self._request_lock: asyncio.Lock | None = None
        self._next_request_at: float = 0.0

    def _get_request_lock(self) -> asyncio.Lock:
        if self._request_lock is None:
            self._request_lock = asyncio.Lock()
        return self._request_lock

    async def _aembedding_with_rate_limit_retries(
        self, *, model: str, input: list[str], **kwargs: Any
    ) -> Any:
        last_exc: Exception | None = None

        for attempt in range(_MAX_RATE_LIMIT_RETRIES):
            try:
                return await litellm.aembedding(model=model, input=input, **kwargs)
            except Exception as exc:  # noqa: BLE001
                delay = _get_rate_limit_delay(exc, attempt)
                last_exc = exc
                if delay is None or attempt == _MAX_RATE_LIMIT_RETRIES - 1:
                    raise

                logger.warning(
                    "Embedding rate limited for model %s, retrying in %.3fs (attempt %d/%d)",
                    model,
                    delay,
                    attempt + 1,
                    _MAX_RATE_LIMIT_RETRIES,
                )
                await asyncio.sleep(delay)

        assert last_exc is not None
        raise last_exc

    async def run_embedding_request(self, *, input: list[str], **kwargs: Any) -> Any:
        lock = self._get_request_lock()
        async with lock:
            now = time.monotonic()
            if self._next_request_at > now:
                await asyncio.sleep(self._next_request_at - now)

            response = await self._aembedding_with_rate_limit_retries(
                model=self._model,
                input=input,
                encoding_format="float",
                **kwargs,
            )

            now = time.monotonic()
            if self._min_request_interval_seconds > 0:
                self._next_request_at = now + self._min_request_interval_seconds
            else:
                self._next_request_at = now

            return response

    async def _get_dim(self) -> int:
        if self._dim is not None:
            return self._dim
        async with self._get_lock():
            if self._dim is not None:
                return self._dim
            response = await self.run_embedding_request(input=["hello"], **self._kwargs)
            embedding = response.data[0]["embedding"]
            self._dim = len(embedding)
            return self._dim

    @coco.fn.as_async(
        batching=True,
        max_batch_size=64,
        memo=True,
        version=1,
        logic_tracking="self",
    )
    async def embed(
        self,
        texts: list[str],
        input_type: str | None = None,
    ) -> list[NDArray[np.float32]]:
        kwargs = dict(self._kwargs)
        if input_type is not None:
            kwargs["input_type"] = input_type
        response = await self.run_embedding_request(input=texts, **kwargs)
        return [np.array(item["embedding"], dtype=np.float32) for item in response.data]
