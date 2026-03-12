"""Unit tests for Config loading."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from cocoindex_code.config import Config


class TestConfigDevice:
    """Tests for COCOINDEX_CODE_DEVICE env var handling."""

    def test_none_by_default(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {"COCOINDEX_CODE_ROOT_PATH": str(tmp_path)},
        ):
            os.environ.pop("COCOINDEX_CODE_DEVICE", None)
            config = Config.from_env()
            assert config.device is None

    def test_env_var_overrides_device(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_DEVICE": "cpu",
            },
        ):
            config = Config.from_env()
            assert config.device == "cpu"


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


class TestExtraExtensions:
    """Tests for COCOINDEX_CODE_EXTRA_EXTENSIONS env var."""

    def test_empty_by_default(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {"COCOINDEX_CODE_ROOT_PATH": str(tmp_path)},
        ):
            os.environ.pop("COCOINDEX_CODE_EXTRA_EXTENSIONS", None)
            config = Config.from_env()
            assert config.extra_extensions == {}

    def test_parses_comma_separated(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EXTRA_EXTENSIONS": "rb,yaml,toml",
            },
        ):
            config = Config.from_env()
            assert config.extra_extensions == {".rb": None, ".yaml": None, ".toml": None}

    def test_trims_whitespace(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EXTRA_EXTENSIONS": " rb , yaml , ",
            },
        ):
            config = Config.from_env()
            assert config.extra_extensions == {".rb": None, ".yaml": None}

    def test_empty_string_gives_empty_dict(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EXTRA_EXTENSIONS": "",
            },
        ):
            config = Config.from_env()
            assert config.extra_extensions == {}

    def test_dot_prefix_passed_through(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EXTRA_EXTENSIONS": ".rb,yaml",
            },
        ):
            config = Config.from_env()
            assert config.extra_extensions == {"..rb": None, ".yaml": None}

    def test_parses_lang_mapping(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EXTRA_EXTENSIONS": "inc:php",
            },
        ):
            config = Config.from_env()
            assert config.extra_extensions == {".inc": "php"}

    def test_mixed_with_and_without_lang(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EXTRA_EXTENSIONS": "inc:php,yaml,tpl:html",
            },
        ):
            config = Config.from_env()
            assert config.extra_extensions == {".inc": "php", ".yaml": None, ".tpl": "html"}


class TestExcludedPatterns:
    """Tests for COCOINDEX_CODE_EXCLUDED_PATTERNS env var."""

    def test_empty_by_default(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {"COCOINDEX_CODE_ROOT_PATH": str(tmp_path)},
        ):
            os.environ.pop("COCOINDEX_CODE_EXCLUDED_PATTERNS", None)
            config = Config.from_env()
            assert config.excluded_patterns == []

    def test_parses_json_array(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EXCLUDED_PATTERNS": '["**/migration.sql", "**/*.d.ts"]',
            },
        ):
            config = Config.from_env()
            assert config.excluded_patterns == ["**/migration.sql", "**/*.d.ts"]

    def test_preserves_commas_inside_globs(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EXCLUDED_PATTERNS": '["{**/*.md,**/*.txt}"]',
            },
        ):
            config = Config.from_env()
            assert config.excluded_patterns == ["{**/*.md,**/*.txt}"]

    def test_trims_whitespace_and_ignores_empty_entries(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EXCLUDED_PATTERNS": '[" **/migration.sql ", " ", "**/*.d.ts"]',
            },
        ):
            config = Config.from_env()
            assert config.excluded_patterns == ["**/migration.sql", "**/*.d.ts"]

    def test_empty_string_gives_empty_list(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EXCLUDED_PATTERNS": "",
            },
        ):
            config = Config.from_env()
            assert config.excluded_patterns == []

    def test_rejects_invalid_json(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EXCLUDED_PATTERNS": "**/migration.sql,**/*.d.ts",
            },
        ):
            with pytest.raises(
                ValueError,
                match=(
                    "COCOINDEX_CODE_EXCLUDED_PATTERNS must be a JSON array of strings, "
                    "got invalid JSON"
                ),
            ):
                Config.from_env()

    def test_rejects_valid_json_non_list(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EXCLUDED_PATTERNS": "{}",
            },
        ):
            with pytest.raises(
                ValueError,
                match="COCOINDEX_CODE_EXCLUDED_PATTERNS must be a JSON array of strings",
            ):
                Config.from_env()

    def test_rejects_non_string_entries(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_EXCLUDED_PATTERNS": '["**/*.py", 1]',
            },
        ):
            with pytest.raises(
                ValueError,
                match="COCOINDEX_CODE_EXCLUDED_PATTERNS must be a JSON array of strings",
            ):
                Config.from_env()
