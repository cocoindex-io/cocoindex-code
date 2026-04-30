"""Prisma schema chunker — one chunk per model/enum/generator/datasource block."""

from __future__ import annotations

import bisect
import re
from pathlib import Path

from cocoindex_code.chunking import Chunk, TextPosition

_BLOCK_START = re.compile(
    r"^(model|enum|type|generator|datasource)\s+(\w+)\s*\{", re.MULTILINE
)


def _build_line_index(content: str) -> list[int]:
    """Return sorted list of newline byte offsets in *content*."""
    return [i for i, c in enumerate(content) if c == "\n"]


def _offset_to_line(offsets: list[int], pos: int) -> int:
    """Convert a character offset to a 1-based line number using a precomputed index."""
    return bisect.bisect_left(offsets, pos) + 1


def _make_pos(offsets: list[int], content: str, offset: int) -> TextPosition:
    if not content:
        return TextPosition(byte_offset=0, char_offset=0, line=1, column=0)
    line = _offset_to_line(offsets, offset)
    col = offset - content.rfind("\n", 0, offset) - 1
    return TextPosition(byte_offset=offset, char_offset=offset, line=line, column=col)


def prisma_chunker(_path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    chunks: list[Chunk] = []
    # Build newline index once — used by all _make_pos calls below.
    nl_offsets = _build_line_index(content)
    lines = content.split("\n")
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line) + 1)

    # Find all top-level block starts with brace tracking
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _BLOCK_START.match(line)
        if not m:
            i += 1
            continue

        # Walk backwards to include preceding comments
        block_start = i
        while block_start > 0 and (
            lines[block_start - 1].startswith("//")
            or lines[block_start - 1].startswith("///")
            or lines[block_start - 1].strip() == ""
        ):
            block_start -= 1
        # Skip leading blank lines
        while block_start < i and lines[block_start].strip() == "":
            block_start += 1

        # Find closing brace (depth tracking)
        depth = 0
        block_end = i
        for j in range(i, len(lines)):
            depth += lines[j].count("{") - lines[j].count("}")
            if depth <= 0:
                block_end = j
                break
        else:
            # Unterminated block: include through EOF so content is not silently dropped.
            block_end = len(lines) - 1

        block_text = "\n".join(lines[block_start : block_end + 1])
        if block_text.strip():
            start_offset = line_offsets[block_start]
            end_offset = max(line_offsets[block_end + 1] - 1, 0)
            chunks.append(
                Chunk(
                    text=block_text,
                    start=_make_pos(nl_offsets, content, start_offset),
                    end=_make_pos(nl_offsets, content, min(end_offset, len(content) - 1)),
                )
            )
        i = block_end + 1

    # Files with only comments or unparsable structure currently index as zero chunks.
    # Fall back to a single-chunk representation to keep the file searchable.
    if not chunks and content.strip():
        chunks = [
            Chunk(
                text=content.strip(),
                start=_make_pos(nl_offsets, content, 0),
                end=_make_pos(nl_offsets, content, max(len(content) - 1, 0)),
            )
        ]

    return "prisma", chunks
