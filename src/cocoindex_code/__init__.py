"""CocoIndex Code - MCP server for indexing and querying codebases."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

logging.basicConfig(level=logging.WARNING)

from ._version import __version__  # noqa: E402

if TYPE_CHECKING:
    from .server import main as main

__all__ = ["main", "__version__"]


def __getattr__(name: str) -> Any:
    if name == "main":
        from .server import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
