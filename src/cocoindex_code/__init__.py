"""CocoIndex Code - MCP server for indexing and querying codebases."""

from importlib.metadata import PackageNotFoundError, version

from .config import Config
from .server import main, mcp

try:
    __version__ = version("cocoindex-code")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

__all__ = ["Config", "main", "mcp"]
