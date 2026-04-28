"""Small runtime compatibility shims for upstream CocoIndex dependencies."""

from __future__ import annotations

import warnings


def _patch_litellm_encoding_format() -> None:
    try:
        import litellm
    except ImportError:
        return

    original_aembedding = litellm.aembedding
    original_embedding = litellm.embedding

    async def patched_aembedding(*args: object, **kwargs: object) -> object:
        kwargs.setdefault("encoding_format", "float")
        return await original_aembedding(*args, **kwargs)

    def patched_embedding(*args: object, **kwargs: object) -> object:
        kwargs.setdefault("encoding_format", "float")
        return original_embedding(*args, **kwargs)

    litellm.aembedding = patched_aembedding
    litellm.embedding = patched_embedding


def _patch_cocoindex_vector_schema_serde() -> None:
    try:
        from cocoindex._internal import serde
        from cocoindex.resources import schema
    except ImportError:
        return

    for cls in (
        getattr(schema, "VectorSchema", None),
        getattr(schema, "MultiVectorSchema", None),
    ):
        if cls is None:
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                serde.serialize_by_pickle(cls)
        except Exception:
            pass

    try:
        serde._get_deserialize_fn.cache_clear()
    except Exception:
        pass


def apply_runtime_patches() -> None:
    """Apply compatibility patches before CocoIndex flows are imported."""
    _patch_litellm_encoding_format()
    _patch_cocoindex_vector_schema_serde()
