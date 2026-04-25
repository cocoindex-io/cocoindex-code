"""Validation and resolution of embedder ``indexing_params`` / ``query_params``.

Runtime entry point is :func:`resolve_embedder_params`.  The curated defaults
table lives in :mod:`embedder_defaults` and is used only by ``ccc init`` —
this module does not consult it.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from .embedder_defaults import LEGACY_QUERY_PROMPT_MODELS
from .settings import EmbeddingSettings

__all__ = [
    "EmbedderParams",
    "accepted_kwargs_for",
    "resolve_embedder_params",
    "validate_params",
]


# Accepted kwargs per provider.  Intentionally minimal — we only expose knobs
# that users have reason to tune AND that make sense per-side (indexing vs
# query).  Excluded keys:
#   - ``normalize_embeddings`` (sentence-transformers): query._l2_to_score
#     assumes unit vectors.
#   - ``encoding_format`` (litellm): litellm_embedder hardcodes "float".
_ACCEPTED_KWARGS: dict[str, frozenset[str]] = {
    "sentence-transformers": frozenset({"prompt_name"}),
    "litellm": frozenset({"input_type"}),
}


def accepted_kwargs_for(provider: str) -> frozenset[str]:
    """Return the set of accepted kwarg names for *provider*.

    Raises ``ValueError`` on unknown providers.
    """
    try:
        return _ACCEPTED_KWARGS[provider]
    except KeyError as e:
        raise ValueError(f"Unknown provider: {provider!r}") from e


def validate_params(
    provider: str,
    indexing_params: dict[str, Any] | None,
    query_params: dict[str, Any] | None,
) -> None:
    """Raise ``ValueError`` if either dict contains keys not accepted by *provider*."""
    accepted = accepted_kwargs_for(provider)
    for side, params in (("indexing_params", indexing_params), ("query_params", query_params)):
        if not params:
            continue
        unknown = sorted(set(params) - accepted)
        if unknown:
            raise ValueError(
                f"{side}: unknown key(s) {unknown!r} for provider {provider!r}. "
                f"Accepted keys: {sorted(accepted)!r}."
            )


class EmbedderParams(NamedTuple):
    """Params that will be spread into ``embedder.embed()`` calls at runtime."""

    indexing: dict[str, Any]  # never None; possibly empty
    query: dict[str, Any]  # never None; possibly empty
    used_backward_compat: bool  # True iff the legacy bridge fired


def resolve_embedder_params(settings: EmbeddingSettings) -> EmbedderParams:
    """Resolve the effective embedder params from user settings.

    Whatever the user put in the file, verbatim, with one exception for
    backward compatibility: if neither ``indexing_params`` nor ``query_params``
    is set and the model was previously handled by the hardcoded
    ``_QUERY_PROMPT_MODELS`` path, fill in ``query = {'prompt_name': 'query'}``
    and raise the ``used_backward_compat`` flag so the daemon emits a
    handshake warning.
    """
    indexing: dict[str, Any] = dict(settings.indexing_params) if settings.indexing_params else {}
    query: dict[str, Any] = dict(settings.query_params) if settings.query_params else {}
    used_backward_compat = False

    if (
        settings.indexing_params is None
        and settings.query_params is None
        and settings.provider == "sentence-transformers"
        and settings.model in LEGACY_QUERY_PROMPT_MODELS
    ):
        query = {"prompt_name": "query"}
        used_backward_compat = True

    validate_params(settings.provider, indexing, query)
    return EmbedderParams(indexing=indexing, query=query, used_backward_compat=used_backward_compat)
