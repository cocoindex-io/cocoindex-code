"""Public API for writing custom chunkers.

Example usage::

    from pathlib import Path
    from cocoindex_code.chunking import Chunk, ChunkerFn, TextPosition

    def my_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
        pos = TextPosition(byte_offset=0, char_offset=0, line=1, column=0)
        return "mylang", [Chunk(text=content, start=pos, end=pos)]
"""

from __future__ import annotations

import importlib as _importlib
import pathlib as _pathlib
from collections.abc import Callable as _Callable
from collections.abc import Sequence as _Sequence
from typing import Protocol as _Protocol

import cocoindex as _coco
from cocoindex.resources.chunk import Chunk, TextPosition

# Callable alias (not Protocol) — consistent with codebase style.
# language_override=None keeps the language detected by detect_code_language.
# path is not resolved (no syscall); call path.resolve() inside the chunker if needed.
ChunkerFn = _Callable[[_pathlib.Path, str], tuple[str | None, list[Chunk]]]

# tracked=False: callables are not fingerprint-able; daemon restart re-indexes anyway.
CHUNKER_REGISTRY = _coco.ContextKey[dict[str, ChunkerFn]]("chunker_registry")


class _ChunkerMappingLike(_Protocol):
    ext: str
    module: str


def resolve_chunker_registry(
    mappings: _Sequence[_ChunkerMappingLike],
) -> dict[str, ChunkerFn]:
    """Resolve chunker mapping entries to a ``{".ext": fn}`` dict.

    Each ``mapping.module`` must be a ``"module.path:callable"`` string importable
    from the current environment.
    """
    registry: dict[str, ChunkerFn] = {}
    for cm in mappings:
        module_path, _, attr = cm.module.partition(":")
        if not attr:
            raise ValueError(f"chunker module {cm.module!r} must use 'module.path:callable' format")
        try:
            mod = _importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            if "." in module_path or exc.name != module_path:
                raise
            mod = _importlib.import_module(f"cocoindex_code.builtin_chunkers.{module_path}")
        fn = getattr(mod, attr)
        if not callable(fn):
            raise ValueError(f"chunker {cm.module!r}: {attr!r} is not callable")
        registry[f".{cm.ext}"] = fn
    return registry


__all__ = [
    "CHUNKER_REGISTRY",
    "Chunk",
    "ChunkerFn",
    "TextPosition",
    "resolve_chunker_registry",
]
