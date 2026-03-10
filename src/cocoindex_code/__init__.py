"""CocoIndex Code - MCP server for indexing and querying codebases."""

import logging

logging.basicConfig(level=logging.WARNING)

from .config import Config  # noqa: E402
from .server import main, mcp  # noqa: E402

__version__ = "0.1.0"
__all__ = ["Config", "main", "mcp"]
