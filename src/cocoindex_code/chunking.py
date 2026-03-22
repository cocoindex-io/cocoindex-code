"""Public API for writing custom chunkers.

Example usage::

    from pathlib import Path
    from cocoindex_code.chunking import Chunk, ChunkerFn, TextPosition

    def my_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
        pos = TextPosition(byte_offset=0, char_offset=0, line=1, column=0)
        return "mylang", [Chunk(text=content, start=pos, end=pos)]
"""

from __future__ import annotations

import pathlib as _pathlib
from collections.abc import Callable as _Callable

import cocoindex as _coco
from cocoindex.resources.chunk import Chunk, TextPosition

# Callable alias (not Protocol) — consistent with codebase style.
# language_override=None keeps the language detected by detect_code_language.
# path is not resolved (no syscall); call path.resolve() inside the chunker if needed.
ChunkerFn = _Callable[[_pathlib.Path, str], tuple[str | None, list[Chunk]]]

# tracked=False: callables are not fingerprint-able; daemon restart re-indexes anyway.
CHUNKER_REGISTRY = _coco.ContextKey[dict[str, ChunkerFn]]("chunker_registry", tracked=False)

__all__ = ["Chunk", "ChunkerFn", "CHUNKER_REGISTRY", "TextPosition"]
