"""CocoIndex Code - MCP server for indexing and querying codebases."""

import logging

logging.basicConfig(level=logging.WARNING)

from .runtime_patches import apply_runtime_patches  # noqa: E402

apply_runtime_patches()

from ._version import __version__  # noqa: E402


def main() -> None:
    from .server import main as _server_main

    _server_main()

__all__ = ["main", "__version__"]
