"""Public API for writing custom chunkers.

Example usage::

    from pathlib import Path
    from cocoindex_code.chunking import Chunk, ChunkerFn, TextPosition

    def my_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
        pos = TextPosition(byte_offset=0, char_offset=0, line=1, column=0)
        return "mylang", [Chunk(text=content, start=pos, end=pos)]
"""

from __future__ import annotations

import dataclasses as _dataclasses
import pathlib as _pathlib
from collections.abc import Callable as _Callable

import cocoindex as _coco
from cocoindex.resources.chunk import Chunk, TextPosition

# Callable alias (not Protocol) — consistent with codebase style.
# language_override=None keeps the language detected by detect_code_language.
# path is not resolved (no syscall); call path.resolve() inside the chunker if needed.
ChunkerFn = _Callable[[_pathlib.Path, str], tuple[str | None, list[Chunk]]]


@_dataclasses.dataclass(frozen=True)
class LoadedChunker:
    """A chunker the daemon imported from a ``settings.yml`` entry.

    Its memo key is ``(spec, module_sha256)``, never ``fn`` itself: cocoindex
    fingerprints a function by its module and name only, so an edit to its body
    would go unnoticed, and it cannot fingerprint lambdas or closures at all.
    """

    fn: ChunkerFn
    #: The ``"module.path:callable"`` string from ``settings.yml``.
    spec: str
    #: sha256 of the module file named in ``spec``, as it was when imported. Helper
    #: modules that file imports are not covered.
    module_sha256: str

    def __coco_memo_key__(self) -> tuple[str, str]:
        return (self.spec, self.module_sha256)


# Keyed by file suffix (e.g. ".toml"). Adding, removing or changing an entry
# re-processes all files.
CHUNKER_REGISTRY = _coco.ContextKey[dict[str, LoadedChunker]](
    "chunker_registry", detect_change=True
)

__all__ = ["Chunk", "ChunkerFn", "CHUNKER_REGISTRY", "LoadedChunker", "TextPosition"]
