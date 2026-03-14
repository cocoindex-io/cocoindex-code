"""Tests for the large_write tool."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from cocoindex_code.filesystem_tools import (
    _large_write_append,
    _large_write_buffers,
    _large_write_finalize,
    _large_write_start,
)


@pytest.fixture()
def sample_codebase(tmp_path: Path) -> Path:
    """Create a sample codebase."""
    (tmp_path / "src").mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _patch_config(sample_codebase: Path) -> Iterator[None]:
    """Patch config and clear buffers."""
    with patch(
        "cocoindex_code.filesystem_tools.config"
    ) as mock_config:
        mock_config.codebase_root_path = sample_codebase
        _large_write_buffers.clear()
        yield
        _large_write_buffers.clear()


class TestLargeWriteStart:
    def test_creates_session(self) -> None:
        _large_write_start("s1", "test.py")
        assert "s1" in _large_write_buffers
        assert _large_write_buffers["s1"]["path"] == "test.py"
        assert _large_write_buffers["s1"]["chunks"] == []
        assert _large_write_buffers["s1"]["total_bytes"] == 0


class TestLargeWriteAppend:
    def test_append_content(self) -> None:
        _large_write_start("s1", "test.py")
        total = _large_write_append("s1", "hello ")
        assert total == 6
        total = _large_write_append("s1", "world")
        assert total == 11
        assert len(_large_write_buffers["s1"]["chunks"]) == 2

    def test_size_limit(self) -> None:
        _large_write_start("s1", "test.py")
        # Try to append more than 5MB
        big_chunk = "x" * (5 * 1024 * 1024 + 1)
        with pytest.raises(ValueError, match="exceeds max size"):
            _large_write_append("s1", big_chunk)


class TestLargeWriteFinalize:
    def test_writes_file(
        self, sample_codebase: Path,
    ) -> None:
        _large_write_start("s1", "output.py")
        _large_write_append("s1", "def foo():\n")
        _large_write_append("s1", "    pass\n")
        path, written, created = _large_write_finalize("s1")

        assert path == "output.py"
        assert created
        assert written > 0

        out = sample_codebase / "output.py"
        assert out.exists()
        content = out.read_text()
        assert "def foo():" in content
        assert "    pass" in content

    def test_creates_parent_dirs(
        self, sample_codebase: Path,
    ) -> None:
        _large_write_start("s1", "deep/nested/dir/file.py")
        _large_write_append("s1", "content")
        _large_write_finalize("s1")

        out = sample_codebase / "deep" / "nested" / "dir" / "file.py"
        assert out.exists()

    def test_removes_session_after_finalize(self) -> None:
        _large_write_start("s1", "test.py")
        _large_write_append("s1", "content")
        _large_write_finalize("s1")
        assert "s1" not in _large_write_buffers

    def test_overwrites_existing_file(
        self, sample_codebase: Path,
    ) -> None:
        existing = sample_codebase / "existing.py"
        existing.write_text("old content")

        _large_write_start("s1", "existing.py")
        _large_write_append("s1", "new content")
        _, _, created = _large_write_finalize("s1")

        assert not created  # file existed
        assert existing.read_text() == "new content"


class TestLargeWriteWorkflow:
    """End-to-end workflow tests."""

    def test_full_workflow(
        self, sample_codebase: Path,
    ) -> None:
        # Start
        _large_write_start("session_1", "src/big_module.py")

        # Append chunks
        _large_write_append(
            "session_1",
            "# Big Module\n\n",
        )
        _large_write_append(
            "session_1",
            "def func_a():\n    pass\n\n",
        )
        _large_write_append(
            "session_1",
            "def func_b():\n    pass\n",
        )

        # Finalize
        path, written, created = _large_write_finalize("session_1")

        assert path == "src/big_module.py"
        assert created
        out = sample_codebase / "src" / "big_module.py"
        content = out.read_text()
        assert "# Big Module" in content
        assert "func_a" in content
        assert "func_b" in content

    def test_multiple_sessions(
        self, sample_codebase: Path,
    ) -> None:
        _large_write_start("a", "file_a.py")
        _large_write_start("b", "file_b.py")
        _large_write_append("a", "content_a")
        _large_write_append("b", "content_b")
        _large_write_finalize("a")
        _large_write_finalize("b")

        assert (sample_codebase / "file_a.py").read_text() == "content_a"
        assert (sample_codebase / "file_b.py").read_text() == "content_b"


class TestSessionEviction:
    """Test that old sessions are evicted when MAX_SESSIONS is reached."""

    def test_evicts_oldest_when_at_capacity(self) -> None:
        from cocoindex_code.filesystem_tools import MAX_LARGE_WRITE_SESSIONS

        # Fill up to the limit
        for i in range(MAX_LARGE_WRITE_SESSIONS):
            _large_write_start(f"sess_{i}", f"file_{i}.py")
        assert len(_large_write_buffers) == MAX_LARGE_WRITE_SESSIONS

        # Adding one more should evict the oldest
        _large_write_start("overflow", "overflow.py")
        assert len(_large_write_buffers) == MAX_LARGE_WRITE_SESSIONS
        assert "overflow" in _large_write_buffers
        # sess_0 should have been evicted (oldest created_at)
        assert "sess_0" not in _large_write_buffers

    def test_restarting_existing_session_does_not_evict(self) -> None:
        from cocoindex_code.filesystem_tools import MAX_LARGE_WRITE_SESSIONS

        for i in range(MAX_LARGE_WRITE_SESSIONS):
            _large_write_start(f"sess_{i}", f"file_{i}.py")

        # Restarting an existing session should NOT evict anyone
        _large_write_start("sess_0", "updated.py")
        assert len(_large_write_buffers) == MAX_LARGE_WRITE_SESSIONS
        assert _large_write_buffers["sess_0"]["path"] == "updated.py"
