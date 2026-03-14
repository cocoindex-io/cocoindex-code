"""CocoIndex Code - MCP server for indexing and querying codebases."""

import logging

logging.basicConfig(level=logging.WARNING)

from ._version import __version__  # noqa: E402
from .server import main  # noqa: E402

__all__ = ["main", "__version__"]
