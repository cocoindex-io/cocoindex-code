"""Unit tests for Config loading."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from cocoindex_code.config import Config, _detect_device


class TestDetectDevice:
    """Tests for device auto-detection."""

    def test_returns_cuda_when_available(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            # Ensure env var is unset
            os.environ.pop("COCOINDEX_CODE_DEVICE", None)
            with patch("torch.cuda.is_available", return_value=True):
                assert _detect_device() == "cuda"

    def test_returns_cpu_when_cuda_unavailable(self) -> None:
        os.environ.pop("COCOINDEX_CODE_DEVICE", None)
        with patch("torch.cuda.is_available", return_value=False):
            assert _detect_device() == "cpu"

    def test_env_var_overrides_auto_detection(self) -> None:
        with patch.dict(os.environ, {"COCOINDEX_CODE_DEVICE": "cpu"}):
            with patch("torch.cuda.is_available", return_value=True):
                assert _detect_device() == "cpu"

    def test_returns_cpu_when_torch_missing(self) -> None:
        os.environ.pop("COCOINDEX_CODE_DEVICE", None)
        with patch.dict("sys.modules", {"torch": None}):
            assert _detect_device() == "cpu"


class TestConfigTrustRemoteCode:
    """Tests for trust_remote_code auto-detection."""

    def test_true_for_jinaai_models(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EMBEDDING_MODEL": "sbert/jinaai/jina-embeddings-v2-base-code",
            },
        ):
            config = Config.from_env()
            assert config.trust_remote_code is True

    def test_false_for_non_jinaai_models(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EMBEDDING_MODEL": "sbert/sentence-transformers/all-MiniLM-L6-v2",
            },
        ):
            config = Config.from_env()
            assert config.trust_remote_code is False

    def test_default_model_is_jina(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {"COCOINDEX_CODE_ROOT_PATH": str(tmp_path)},
        ):
            os.environ.pop("COCOINDEX_CODE_EMBEDDING_MODEL", None)
            config = Config.from_env()
            assert "jinaai" in config.embedding_model
            assert config.trust_remote_code is True
