"""Unit tests for LocalEmbedder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from cocoindex_code.embedder import LocalEmbedder

_SENTENCE_TRANSFORMER = "sentence_transformers.SentenceTransformer"


def _make_mock_model(dim: int = 768) -> MagicMock:
    """Return a mock SentenceTransformer with controllable output."""
    mock = MagicMock()
    mock.get_sentence_embedding_dimension.return_value = dim
    mock.encode.return_value = np.zeros((1, dim), dtype=np.float32)
    return mock


class TestLocalEmbedderInit:
    """Tests for constructor and lazy loading."""

    def test_passes_device_to_sentence_transformer(self) -> None:
        mock_model = _make_mock_model()
        with patch(_SENTENCE_TRANSFORMER, return_value=mock_model) as mock_cls:
            embedder = LocalEmbedder("some-model", device="cuda")
            embedder._get_model()
            mock_cls.assert_called_once_with("some-model", device="cuda", trust_remote_code=False)

    def test_passes_trust_remote_code_to_sentence_transformer(self) -> None:
        mock_model = _make_mock_model()
        with patch(_SENTENCE_TRANSFORMER, return_value=mock_model) as mock_cls:
            embedder = LocalEmbedder("jinaai/model", device="cpu", trust_remote_code=True)
            embedder._get_model()
            mock_cls.assert_called_once_with("jinaai/model", device="cpu", trust_remote_code=True)

    def test_lazy_loads_model_only_once(self) -> None:
        mock_model = _make_mock_model()
        with patch(_SENTENCE_TRANSFORMER, return_value=mock_model) as mock_cls:
            embedder = LocalEmbedder("some-model", device="cpu")
            embedder._get_model()
            embedder._get_model()
            assert mock_cls.call_count == 1


class TestLocalEmbedderPickle:
    """Tests for pickle serialization (required by CocoIndex)."""

    def test_getstate_excludes_model(self) -> None:
        embedder = LocalEmbedder("some-model", device="cuda", trust_remote_code=True)
        state = embedder.__getstate__()
        # Verify the actual SentenceTransformer model object is not in state (only primitives)
        assert "_model" not in state
        assert all(isinstance(v, str | bool) for v in state.values() if v is not None)
        assert state["model_name_or_path"] == "some-model"
        assert state["device"] == "cuda"
        assert state["trust_remote_code"] is True

    def test_setstate_resets_model_to_none(self) -> None:
        embedder = LocalEmbedder("some-model", device="cpu")
        state = embedder.__getstate__()
        new_embedder = LocalEmbedder.__new__(LocalEmbedder)
        new_embedder.__setstate__(state)
        assert new_embedder._model is None
        assert new_embedder._device == "cpu"


class TestLocalEmbedderMemoKey:
    """Tests for CocoIndex memo key uniqueness."""

    def test_different_models_have_different_keys(self) -> None:
        e1 = LocalEmbedder("model-a", device="cpu")
        e2 = LocalEmbedder("model-b", device="cpu")
        assert e1.__coco_memo_key__() != e2.__coco_memo_key__()

    def test_different_devices_have_different_keys(self) -> None:
        e1 = LocalEmbedder("model", device="cpu")
        e2 = LocalEmbedder("model", device="cuda")
        assert e1.__coco_memo_key__() != e2.__coco_memo_key__()

    def test_different_trust_remote_code_have_different_keys(self) -> None:
        e1 = LocalEmbedder("model", device="cpu", trust_remote_code=False)
        e2 = LocalEmbedder("model", device="cpu", trust_remote_code=True)
        assert e1.__coco_memo_key__() != e2.__coco_memo_key__()

    def test_different_normalize_have_different_keys(self) -> None:
        e1 = LocalEmbedder("model", device="cpu", normalize_embeddings=True)
        e2 = LocalEmbedder("model", device="cpu", normalize_embeddings=False)
        assert e1.__coco_memo_key__() != e2.__coco_memo_key__()
