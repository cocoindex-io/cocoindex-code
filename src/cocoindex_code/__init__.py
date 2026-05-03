"""CocoIndex Code - MCP server for indexing and querying codebases."""

from ._version import __version__
from .server import main

__all__ = ["main", "__version__"]
