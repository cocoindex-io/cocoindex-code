"""Curated default embedder params for known models.

Consulted only by ``ccc init`` — the table is NOT read at daemon runtime.
The runtime path reads the user's YAML verbatim; the legacy-bridge in
``embedder_params.resolve_embedder_params`` is the only runtime-level fallback
and is scoped to :data:`LEGACY_QUERY_PROMPT_MODELS`.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

__all__ = [
    "DefaultParamsEntry",
    "LEGACY_QUERY_PROMPT_MODELS",
    "lookup_defaults",
]


class DefaultParamsEntry(NamedTuple):
    provider: str  # "sentence-transformers" | "litellm"
    model: str | re.Pattern[str]  # str = exact match; Pattern = regex match
    indexing_params: dict[str, Any]  # may be empty
    query_params: dict[str, Any]  # may be empty


# Models previously hardcoded in shared.py:_QUERY_PROMPT_MODELS.  Retained as
# a frozenset so the legacy-bridge in ``embedder_params`` can recognize
# pre-existing configs that predate this feature.
LEGACY_QUERY_PROMPT_MODELS: frozenset[str] = frozenset(
    {"nomic-ai/nomic-embed-code", "nomic-ai/CodeRankEmbed"}
)


_DEFAULT_PARAMS: list[DefaultParamsEntry] = [
    # --- sentence-transformers ---
    DefaultParamsEntry(
        "sentence-transformers",
        "nomic-ai/CodeRankEmbed",
        {},
        {"prompt_name": "query"},
    ),
    DefaultParamsEntry(
        "sentence-transformers",
        "nomic-ai/nomic-embed-code",
        {},
        {"prompt_name": "query"},
    ),
    DefaultParamsEntry(
        "sentence-transformers",
        "nomic-ai/nomic-embed-text-v1",
        {"prompt_name": "passage"},
        {"prompt_name": "query"},
    ),
    DefaultParamsEntry(
        "sentence-transformers",
        "nomic-ai/nomic-embed-text-v1.5",
        {"prompt_name": "passage"},
        {"prompt_name": "query"},
    ),
    DefaultParamsEntry(
        "sentence-transformers",
        "mixedbread-ai/mxbai-embed-large-v1",
        {},
        {"prompt_name": "query"},
    ),
    DefaultParamsEntry(
        "sentence-transformers",
        re.compile(r"Snowflake/snowflake-arctic-embed-.+"),
        {},
        {"prompt_name": "query"},
    ),
    # --- litellm ---
    DefaultParamsEntry(
        "litellm",
        re.compile(r"cohere/embed-(english|multilingual)(-light)?-v3\.0"),
        {"input_type": "search_document"},
        {"input_type": "search_query"},
    ),
    DefaultParamsEntry(
        "litellm",
        re.compile(r"voyage/.+"),
        {"input_type": "document"},
        {"input_type": "query"},
    ),
    DefaultParamsEntry(
        "litellm",
        re.compile(r"nvidia_nim/nvidia/.+"),
        {"input_type": "passage"},
        {"input_type": "query"},
    ),
    # Gemini embedding models: LiteLLM's Gemini transformation auto-maps
    # `input_type` → `task_type` (RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY work
    # across all Gemini embedding generations).
    DefaultParamsEntry(
        "litellm",
        re.compile(r"gemini/(gemini-embedding|text-embedding|embedding)[-\w.]*"),
        {"input_type": "RETRIEVAL_DOCUMENT"},
        {"input_type": "RETRIEVAL_QUERY"},
    ),
]


def lookup_defaults(
    provider: str, model: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Look up recommended (indexing_params, query_params) for *model*.

    Walks :data:`_DEFAULT_PARAMS` in order; an exact-name entry matches iff
    ``entry.model == model``; a compiled-regex entry matches via
    ``entry.model.fullmatch(model)``.  First match wins.  Returns the pair of
    dicts (each possibly empty) or ``(None, None)`` when no entry matches.
    """
    for entry in _DEFAULT_PARAMS:
        if entry.provider != provider:
            continue
        if isinstance(entry.model, str):
            matched = entry.model == model
        else:
            matched = entry.model.fullmatch(model) is not None
        if matched:
            return dict(entry.indexing_params), dict(entry.query_params)
    return None, None


def _assert_legacy_bridge_invariant() -> None:
    """Each legacy model must have an exact sentence-transformers entry with
    ``query_params == {"prompt_name": "query"}``.  Guarantees users who run
    ``ccc init`` against a legacy model get the same effective behavior the
    runtime legacy-bridge produces.
    """
    for legacy in LEGACY_QUERY_PROMPT_MODELS:
        found = False
        for entry in _DEFAULT_PARAMS:
            if (
                entry.provider == "sentence-transformers"
                and isinstance(entry.model, str)
                and entry.model == legacy
                and entry.query_params == {"prompt_name": "query"}
            ):
                found = True
                break
        if not found:
            raise AssertionError(
                f"Legacy model {legacy!r} has no matching sentence-transformers "
                f"exact-name entry in _DEFAULT_PARAMS with "
                f"query_params={{'prompt_name': 'query'}}"
            )


_assert_legacy_bridge_invariant()
