"""Public API for writing custom chunkers.

Example usage::

    from pathlib import Path
    from cocoindex_code.chunking import Chunk, ChunkerFn, TextPosition

    def my_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
        pos = TextPosition(byte_offset=0, char_offset=0, line=1, column=0)
        return "mylang", [Chunk(text=content, start=pos, end=pos)]
"""

from __future__ import annotations

import hashlib as _hashlib
import inspect as _inspect
import json as _json
import pathlib as _pathlib
from collections.abc import Callable as _Callable
from collections.abc import Mapping as _Mapping
from collections.abc import Sequence as _Sequence

import cocoindex as _coco
from cocoindex.resources.chunk import Chunk, TextPosition

# Callable alias (not Protocol) — consistent with codebase style.
# language_override=None keeps the language detected by detect_code_language.
# path is not resolved (no syscall); call path.resolve() inside the chunker if needed.
ChunkerFn = _Callable[[_pathlib.Path, str], tuple[str | None, list[Chunk]]]

# The registry holds callables, which cocoindex cannot fingerprint, so this context
# does not take part in change detection. chunking_fingerprint() below is passed to
# process_file as a memo argument instead, so a registry change still invalidates
# memoized results for unchanged files.
CHUNKER_REGISTRY = _coco.ContextKey[dict[str, ChunkerFn]]("chunker_registry")


def _chunker_source_digest(fn: ChunkerFn) -> str:
    """Digest of the module file a chunker lives in (its whole source, so a
    helper edit counts too); falls back to the function source or repr."""
    try:
        source_file = _inspect.getsourcefile(fn)
        if source_file:
            text = _pathlib.Path(source_file).read_text(encoding="utf-8", errors="replace")
        else:
            text = _inspect.getsource(fn)
    except (OSError, TypeError):
        text = repr(fn)
    return _hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunking_fingerprint(
    language_overrides: _Sequence[object],
    registry: _Mapping[str, ChunkerFn],
) -> str:
    """Stable digest of everything that decides how an unchanged file is chunked.

    ``process_file`` is memoized on its arguments, but the language overrides
    come from ``settings.yml`` and the chunker registry is an untracked context
    key, so on their own they never invalidate the memo: editing either left
    unchanged files with their old chunks and language. Passing this digest as
    an argument makes a change to ``language_overrides`` (ext -> lang pairs) or
    to the registered chunkers (suffix, callable identity and the source of the
    module defining it) re-process every file on the next ``ccc index``.
    """
    overrides = sorted(
        (str(getattr(lo, "ext", "")), str(getattr(lo, "lang", ""))) for lo in language_overrides
    )
    chunkers = [
        (
            suffix,
            getattr(fn, "__module__", ""),
            getattr(fn, "__qualname__", repr(fn)),
            _chunker_source_digest(fn),
        )
        for suffix, fn in sorted(registry.items())
    ]
    payload = _json.dumps({"language_overrides": overrides, "chunkers": chunkers})
    return _hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["Chunk", "ChunkerFn", "CHUNKER_REGISTRY", "TextPosition", "chunking_fingerprint"]
