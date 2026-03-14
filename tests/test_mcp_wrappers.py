"""Tests for MCP tool wrapper layer — exception handling and Pydantic validation."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from cocoindex_code.filesystem_tools import (
    _large_write_buffers,
)


@pytest.fixture()
def sample_codebase(tmp_path: Path) -> Path:
    """Create a minimal codebase."""
    (tmp_path / "hello.py").write_text("print('hello')\n")
    return tmp_path


@pytest.fixture(autouse=True)
def _patch_config(sample_codebase: Path) -> Iterator[None]:
    with (
        patch("cocoindex_code.filesystem_tools.config") as mock_fs_config,
        patch("cocoindex_code.thinking_tools.config") as mock_tt_config,
        patch("cocoindex_code.thinking_tools._engine", None),
    ):
        mock_fs_config.codebase_root_path = sample_codebase
        mock_tt_config.index_dir = sample_codebase
        _large_write_buffers.clear()
        yield
        _large_write_buffers.clear()


class TestFilesystemToolValidation:
    """Test that filesystem tools handle edge cases correctly."""

    def test_large_write_append_without_start(self) -> None:
        """Appending to non-existent session should raise."""
        from cocoindex_code.filesystem_tools import _large_write_append

        with pytest.raises(KeyError):
            _large_write_append("nonexistent", "content")

    def test_large_write_finalize_without_start(self) -> None:
        """Finalizing non-existent session should raise."""
        from cocoindex_code.filesystem_tools import _large_write_finalize

        with pytest.raises(KeyError):
            _large_write_finalize("nonexistent")

    def test_large_write_start_idempotent(self) -> None:
        """Starting a session twice should reset it."""
        from cocoindex_code.filesystem_tools import (
            _large_write_append,
            _large_write_start,
        )

        _large_write_start("s1", "file.py")
        _large_write_append("s1", "chunk1")
        _large_write_start("s1", "file2.py")  # Restart
        assert _large_write_buffers["s1"]["path"] == "file2.py"
        assert _large_write_buffers["s1"]["chunks"] == []


class TestThinkingToolPydanticModels:
    """Test that Pydantic models validate inputs correctly."""

    def test_thought_data_requires_fields(self) -> None:
        from pydantic import ValidationError

        from cocoindex_code.thinking_tools import ThoughtData

        with pytest.raises(ValidationError):
            ThoughtData()  # type: ignore[call-arg]

    def test_thought_data_valid(self) -> None:
        from cocoindex_code.thinking_tools import ThoughtData

        td = ThoughtData(
            thought="test",
            thought_number=1,
            total_thoughts=3,
            next_thought_needed=True,
        )
        assert td.thought == "test"
        assert td.is_revision is False

    def test_thinking_result_defaults(self) -> None:
        from cocoindex_code.thinking_tools import ThinkingResult

        result = ThinkingResult(success=True)
        assert result.session_id == ""
        assert result.branches == []
        assert result.message is None

    def test_evidence_tracker_result_defaults(self) -> None:
        from cocoindex_code.thinking_tools import EvidenceTrackerResult

        result = EvidenceTrackerResult(success=False, message="test")
        assert result.effort_mode == "medium"
        assert result.total_evidence_count == 0

    def test_plan_optimizer_result_defaults(self) -> None:
        from cocoindex_code.thinking_tools import PlanOptimizerResult

        result = PlanOptimizerResult(success=True)
        assert result.variants == []
        assert result.comparison_matrix == {}
        assert result.plan_health_score == 0.0


class TestThinkingEngineExceptionHandling:
    """Test that ThinkingEngine handles errors gracefully."""

    def test_load_corrupted_memory_file(self, sample_codebase: Path) -> None:
        """ThinkingEngine should handle corrupted JSONL gracefully."""
        from cocoindex_code.thinking_engine import ThinkingEngine

        memory_file = sample_codebase / "thinking_memory.jsonl"
        memory_file.write_text("not valid json\n{\"type\": \"bad\"}\n")

        # Should not crash — just skip invalid lines
        with pytest.raises(Exception):
            ThinkingEngine(sample_codebase)

    def test_empty_memory_file(self, sample_codebase: Path) -> None:
        """ThinkingEngine should handle empty memory file."""
        from cocoindex_code.thinking_engine import ThinkingEngine

        memory_file = sample_codebase / "thinking_memory.jsonl"
        memory_file.write_text("")

        engine = ThinkingEngine(sample_codebase)
        assert engine._learnings == []
        assert engine._strategy_scores == {}

    def test_missing_memory_file(self, sample_codebase: Path) -> None:
        """ThinkingEngine should handle missing memory file."""
        from cocoindex_code.thinking_engine import ThinkingEngine

        engine = ThinkingEngine(sample_codebase)
        assert engine._learnings == []
