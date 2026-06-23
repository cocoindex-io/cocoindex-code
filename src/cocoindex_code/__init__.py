"""CocoIndex Code - MCP server for indexing and querying codebases."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

# Identify this application in cocoindex's telemetry payloads. Must be set
# before any `import cocoindex` runs (the value is read once at telemetry
# init time). See cocoindex-io/cocoindex#1992.
os.environ.setdefault("COCOINDEX_APPLICATION_FOR_TRACKING", "cocoindex-code")

from ._version import __version__  # noqa: E402

if TYPE_CHECKING:
    from .server import main as main

__all__ = ["main", "__version__"]


def __getattr__(name: str) -> Any:
    if name == "main":
        from .server import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
