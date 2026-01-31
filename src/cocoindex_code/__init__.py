"""CocoIndex Code - MCP server for indexing and querying codebases."""

from .config import Config
from .server import main, mcp

__version__ = "0.1.0"
__all__ = ["Config", "main", "mcp"]
