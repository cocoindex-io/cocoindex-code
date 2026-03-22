"""Demo chunker: splits TOML files at top-level [section] boundaries.

Each ``[section]`` header starts a new chunk, keeping the section header
and its key-value pairs together.  This produces semantically coherent units
instead of the arbitrary line-window slices from the default splitter.

Register in ``.cocoindex_code/settings.yml``::

    chunkers:
      - ext: toml
        module: example_toml_chunker:toml_chunker
"""

from __future__ import annotations

import re as _re
from pathlib import Path as _Path

from cocoindex_code.chunking import Chunk, TextPosition

_SECTION_RE = _re.compile(r"^\[(?!\[)")


def _pos(line: int) -> TextPosition:
    return TextPosition(byte_offset=0, char_offset=0, line=line, column=0)


def toml_chunker(path: _Path, content: str) -> tuple[str | None, list[Chunk]]:
    """Split a TOML file at top-level ``[section]`` headers."""
    lines = content.splitlines()
    section_starts = [i for i, ln in enumerate(lines) if _SECTION_RE.match(ln)]

    if not section_starts:
        return "toml", [Chunk(text=content, start=_pos(1), end=_pos(len(lines)))]

    boundaries = section_starts + [len(lines)]
    chunks: list[Chunk] = []
    for start_idx, end_idx in zip(boundaries, boundaries[1:]):
        text = "\n".join(lines[start_idx:end_idx]).strip()
        if text:
            chunks.append(Chunk(text=text, start=_pos(start_idx + 1), end=_pos(end_idx)))
    return "toml", chunks


__all__ = ["toml_chunker"]
