"""Tests for MCP background indexing connection isolation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestBgIndexIsolation:
    """Verify _bg_index uses a dedicated DaemonClient, not the shared one."""

    @pytest.mark.asyncio
    async def test_cli_bg_index_creates_own_client(self) -> None:
        """cli._bg_index should call ensure_daemon() for a fresh client."""
        from cocoindex_code.cli import _bg_index

        mock_client = MagicMock()
        mock_client.index = MagicMock()
        mock_client.close = MagicMock()

        with patch("cocoindex_code.client.ensure_daemon", return_value=mock_client) as mock_ensure:
            await _bg_index("/tmp/project")

        mock_ensure.assert_called_once()
        mock_client.index.assert_called_once_with("/tmp/project")
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cli_bg_index_closes_client_on_error(self) -> None:
        """cli._bg_index should close the client even if indexing fails."""
        from cocoindex_code.cli import _bg_index

        mock_client = MagicMock()
        mock_client.index = MagicMock(side_effect=RuntimeError("boom"))
        mock_client.close = MagicMock()

        with patch("cocoindex_code.client.ensure_daemon", return_value=mock_client):
            await _bg_index("/tmp/project")  # should not raise

        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_server_bg_index_creates_own_client(self) -> None:
        """server._bg_index should call ensure_daemon() for a fresh client."""
        from cocoindex_code.server import _bg_index

        mock_client = MagicMock()
        mock_client.index = MagicMock()
        mock_client.close = MagicMock()

        with patch("cocoindex_code.client.ensure_daemon", return_value=mock_client) as mock_ensure:
            await _bg_index("/tmp/project")

        mock_ensure.assert_called_once()
        mock_client.index.assert_called_once_with("/tmp/project")
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_server_bg_index_closes_client_on_error(self) -> None:
        """server._bg_index should close the client even if indexing fails."""
        from cocoindex_code.server import _bg_index

        mock_client = MagicMock()
        mock_client.index = MagicMock(side_effect=RuntimeError("boom"))
        mock_client.close = MagicMock()

        with patch("cocoindex_code.client.ensure_daemon", return_value=mock_client):
            await _bg_index("/tmp/project")  # should not raise

        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_bg_index_does_not_use_shared_client(self) -> None:
        """The shared MCP client must NOT be passed to _bg_index."""
        from cocoindex_code.cli import _bg_index

        shared_client = MagicMock()
        bg_client = MagicMock()
        bg_client.index = MagicMock()
        bg_client.close = MagicMock()

        with patch("cocoindex_code.client.ensure_daemon", return_value=bg_client):
            await _bg_index("/tmp/project")

        # The shared client should never have been called
        shared_client.index.assert_not_called()
        # The bg client should have been used instead
        bg_client.index.assert_called_once()
