"""Tests for ``provider: custom``: the embedder hook that loads a user factory."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from cocoindex.resources.schema import VectorSchema

from cocoindex_code.embedder_params import validate_params
from cocoindex_code.settings import (
    CUSTOM_PROVIDER,
    EmbeddingSettings,
    _embedding_settings_to_dict,
    _user_settings_from_dict,
)
from cocoindex_code.shared import CustomEmbedder, create_embedder, load_custom_embedder_factory

FACTORY_MODULE = textwrap.dedent(
    """
    import numpy as np
    from cocoindex.resources.schema import VectorSchema

    class FakeEmbedder:
        def __init__(self, settings, indexing_params):
            self.settings = settings
            self.indexing_params = indexing_params

        async def embed(self, text, **kwargs):
            return np.zeros(3, dtype=np.float32)

        async def __coco_vector_schema__(self):
            return VectorSchema(dtype=np.dtype(np.float32), size=3)

    def make(settings, indexing_params):
        return FakeEmbedder(settings, indexing_params)

    def make_wrong(settings, indexing_params):
        return object()

    not_callable = 42
    """
)


@pytest.fixture
def factory_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    name = "custom_embedder_fixture_mod"
    (tmp_path / f"{name}.py").write_text(FACTORY_MODULE)
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)
    yield name
    sys.modules.pop(name, None)


def test_settings_require_an_embedder_for_custom() -> None:
    with pytest.raises(ValueError, match="requires embedding.embedder"):
        EmbeddingSettings(model="m", provider=CUSTOM_PROVIDER)
    with pytest.raises(ValueError, match="requires embedding.embedder"):
        EmbeddingSettings(model="m", provider=CUSTOM_PROVIDER, embedder="no_colon")


def test_settings_reject_embedder_on_other_providers() -> None:
    with pytest.raises(ValueError, match="only used with provider 'custom'"):
        EmbeddingSettings(model="m", provider="litellm", embedder="pkg.mod:make")


def test_settings_round_trip_keeps_embedder() -> None:
    settings = EmbeddingSettings(
        model="voyage-4-large",
        provider=CUSTOM_PROVIDER,
        embedder="pkg.mod:make",
        indexing_params={"input_type": "document"},
    )
    d = _embedding_settings_to_dict(settings)
    assert d["provider"] == "custom" and d["embedder"] == "pkg.mod:make"
    loaded = _user_settings_from_dict({"embedding": d})
    assert loaded.embedding == settings


def test_custom_params_are_not_validated() -> None:
    # anything goes: the custom embedder owns its kwargs
    validate_params(CUSTOM_PROVIDER, {"anything": 1}, {"else": 2})
    with pytest.raises(ValueError, match="unknown key"):
        validate_params("litellm", {"anything": 1}, None)


def test_create_embedder_loads_the_factory(factory_module: str) -> None:
    settings = EmbeddingSettings(
        model="fake-model", provider=CUSTOM_PROVIDER, embedder=f"{factory_module}:make"
    )
    embedder: Any = create_embedder(settings, {"input_type": "document"})
    assert isinstance(embedder, CustomEmbedder)
    assert embedder.settings is settings
    assert embedder.indexing_params == {"input_type": "document"}


def test_create_embedder_rejects_a_factory_without_the_contract(factory_module: str) -> None:
    settings = EmbeddingSettings(
        model="m", provider=CUSTOM_PROVIDER, embedder=f"{factory_module}:make_wrong"
    )
    with pytest.raises(ValueError, match="lacks an async embed"):
        create_embedder(settings)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("nope_module_xyz:make", "cannot import"),
        ("{mod}:missing_attr", "has no attribute"),
        ("{mod}:not_callable", "is not callable"),
        ("{mod}", 'must be "module.path:callable"'),
    ],
)
def test_loader_errors_name_the_spec(factory_module: str, spec: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_custom_embedder_factory(spec.format(mod=factory_module))


async def test_loaded_embedder_embeds_and_reports_schema(factory_module: str) -> None:
    settings = EmbeddingSettings(
        model="m", provider=CUSTOM_PROVIDER, embedder=f"{factory_module}:make"
    )
    embedder: Any = create_embedder(settings)
    vec = await embedder.embed("hello")
    assert vec.shape == (3,) and vec.dtype == np.float32
    schema: VectorSchema = await embedder.__coco_vector_schema__()
    assert schema.size == 3
