"""Unit tests for Config loading."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

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
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COCOINDEX_CODE_DEVICE", None)
            with patch("torch.cuda.is_available", return_value=False):
                assert _detect_device() == "cpu"

    def test_env_var_overrides_auto_detection(self) -> None:
        with patch.dict(os.environ, {"COCOINDEX_CODE_DEVICE": "cpu"}):
            with patch("torch.cuda.is_available", return_value=True):
                assert _detect_device() == "cpu"

    def test_returns_cpu_when_torch_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COCOINDEX_CODE_DEVICE", None)
            with patch.dict("sys.modules", {"torch": None}):
                assert _detect_device() == "cpu"


class TestConfigTrustRemoteCode:
    """Tests for trust_remote_code env var control."""

    def test_false_by_default(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {"COCOINDEX_CODE_ROOT_PATH": str(tmp_path)},
        ):
            os.environ.pop("COCOINDEX_CODE_TRUST_REMOTE_CODE", None)
            config = Config.from_env()
            assert config.trust_remote_code is False

    def test_true_when_env_var_set_to_true(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_TRUST_REMOTE_CODE": "true",
            },
        ):
            config = Config.from_env()
            assert config.trust_remote_code is True

    def test_true_when_env_var_set_to_1(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_TRUST_REMOTE_CODE": "1",
            },
        ):
            config = Config.from_env()
            assert config.trust_remote_code is True

    def test_default_model_is_minilm(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {"COCOINDEX_CODE_ROOT_PATH": str(tmp_path)},
        ):
            os.environ.pop("COCOINDEX_CODE_EMBEDDING_MODEL", None)
            config = Config.from_env()
            assert "all-MiniLM-L6-v2" in config.embedding_model


class TestConfigBatchSize:
    """Tests for COCOINDEX_CODE_BATCH_SIZE env var."""

    def test_default_batch_size_is_16(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {"COCOINDEX_CODE_ROOT_PATH": str(tmp_path)},
        ):
            os.environ.pop("COCOINDEX_CODE_BATCH_SIZE", None)
            config = Config.from_env()
            assert config.batch_size == 16

    def test_batch_size_reads_env_var(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_BATCH_SIZE": "32",
            },
        ):
            config = Config.from_env()
            assert config.batch_size == 32
