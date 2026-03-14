"""Tests for server.py CLI argument parsing."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestMainArgumentParsing:
    """Test that main() parses CLI arguments correctly."""

    def test_serve_is_default(self) -> None:
        """When no command is given, 'serve' is the default."""
        with (
            patch("sys.argv", ["cocoindex-code"]),
            patch(
                "cocoindex_code.server.asyncio.run",
            ) as mock_run,
        ):
            from cocoindex_code.server import main

            main()
            mock_run.assert_called_once()
            # The call should be to _async_serve()
            call_args = mock_run.call_args
            coro = call_args[0][0]
            assert coro is not None

    def test_serve_command(self) -> None:
        """Explicit 'serve' command should call _async_serve."""
        with (
            patch("sys.argv", ["cocoindex-code", "serve"]),
            patch(
                "cocoindex_code.server.asyncio.run",
            ) as mock_run,
        ):
            from cocoindex_code.server import main

            main()
            mock_run.assert_called_once()

    def test_index_command(self) -> None:
        """'index' command should call _async_index."""
        with (
            patch("sys.argv", ["cocoindex-code", "index"]),
            patch(
                "cocoindex_code.server.asyncio.run",
            ) as mock_run,
        ):
            from cocoindex_code.server import main

            main()
            mock_run.assert_called_once()


class TestPrintIndexStats:
    """Test _print_index_stats with mocked database."""

    @pytest.mark.asyncio
    async def test_no_database(self, tmp_path: object) -> None:
        """When no index DB exists, print message."""
        with patch(
            "cocoindex_code.server.config"
        ) as mock_config:
            from pathlib import Path

            mock_config.target_sqlite_db_path = Path("/nonexistent/db.sqlite")
            from cocoindex_code.server import _print_index_stats

            # Should not crash, just print "No index database found."
            await _print_index_stats()


class TestSearchResultModel:
    """Test SearchResultModel Pydantic model."""

    def test_default_values(self) -> None:
        from cocoindex_code.server import SearchResultModel

        result = SearchResultModel(success=True)
        assert result.results == []
        assert result.total_returned == 0
        assert result.offset == 0
        assert result.message is None

    def test_with_results(self) -> None:
        from cocoindex_code.server import CodeChunkResult, SearchResultModel

        chunk = CodeChunkResult(
            file_path="test.py",
            language="python",
            content="print('hello')",
            start_line=1,
            end_line=1,
            score=0.95,
        )
        result = SearchResultModel(
            success=True,
            results=[chunk],
            total_returned=1,
        )
        assert len(result.results) == 1
        assert result.results[0].file_path == "test.py"

    def test_error_result(self) -> None:
        from cocoindex_code.server import SearchResultModel

        result = SearchResultModel(
            success=False,
            message="Index not found",
        )
        assert result.success is False
        assert result.message == "Index not found"
