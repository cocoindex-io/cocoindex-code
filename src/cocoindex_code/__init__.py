"""CocoIndex Code - MCP server for indexing and querying codebases."""

import logging

logging.basicConfig(level=logging.WARNING)

from ._version import __version__  # noqa: E402
from .config import Config  # noqa: E402
from .server import main, mcp  # noqa: E402

__all__ = ["Config", "main", "mcp", "__version__"]
