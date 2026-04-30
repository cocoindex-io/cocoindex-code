"""Smart markdown chunker — preserves tables and code blocks for domain docs.

Activates for architecture docs, signal docs, CLAUDE.md files, and .claude/rules/.
Other markdown files use the same header-based split without language override.
"""

from __future__ import annotations

import re
from pathlib import Path

from cocoindex_code.chunking import Chunk, TextPosition
from cocoindex.ops.text import RecursiveSplitter

_DOMAIN_PATHS = (
    "docs/architecture/",
    "docs/architecture\\",
    "docs/signal/",
    "docs/signal\\",
    ".claude/rules/",
    ".claude/rules\\",
)

_DOMAIN_FILENAMES = ("CLAUDE.md", "AGENTS.md")

_HEADER_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
_splitter = RecursiveSplitter()
_CHUNK_SIZE = 1000
_MIN_CHUNK_SIZE = 250
_CHUNK_OVERLAP = 150


def _make_pos(line: int) -> TextPosition:
    return TextPosition(byte_offset=0, char_offset=0, line=line, column=0)


def _is_domain_doc(path: Path) -> bool:
    if path.name in _DOMAIN_FILENAMES:
        return True
    path_str = path.as_posix()
    return any(seg in path_str for seg in _DOMAIN_PATHS)


# Fenced code block opener at column 0-3 (CommonMark). Closed by a matching fence.
_FENCE_RE = re.compile(r"^\s{0,3}(```+|~~~+)")


def _split_by_headers(content: str) -> list[Chunk]:
    """Split markdown by ## and ### headers, keeping tables and code blocks intact."""
    lines = content.split("\n")
    chunks: list[Chunk] = []
    current_lines: list[str] = []
    current_start = 1
    fence: str | None = None  # open fence marker prefix (``` or ~~~), else None

    for i, line in enumerate(lines, 1):
        # Track fence state first — `##` inside a code block is content, not a header.
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)[:3]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None

        is_split_header = False
        if fence is None:
            header_match = _HEADER_RE.match(line)
            is_split_header = bool(
                header_match and len(header_match.group(1)) in {2, 3}
            )

        if is_split_header and current_lines:
            text = "\n".join(current_lines).strip()
            if text:
                chunks.append(
                    Chunk(text=text, start=_make_pos(current_start), end=_make_pos(i - 1))
                )
            current_lines = [line]
            current_start = i
        else:
            current_lines.append(line)

    # Final section
    if current_lines:
        text = "\n".join(current_lines).strip()
        if text:
            chunks.append(
                Chunk(text=text, start=_make_pos(current_start), end=_make_pos(len(lines)))
            )

    # Post-process: keep headers readable but allow recursive fallback for large chunks.
    expanded: list[Chunk] = []
    for chunk in chunks:
        if len(chunk.text) <= _CHUNK_SIZE:
            expanded.append(chunk)
            continue

        split_chunks = _splitter.split(
            chunk.text,
            chunk_size=_CHUNK_SIZE,
            min_chunk_size=_MIN_CHUNK_SIZE,
            chunk_overlap=_CHUNK_OVERLAP,
            language="markdown",
        )
        if not split_chunks:
            expanded.append(chunk)
            continue

        # Rebase recursive chunk positions to account for chunk start line in source.
        for split in split_chunks:
            start_line = split.start.line + chunk.start.line - 1
            end_line = split.end.line + chunk.start.line - 1
            expanded.append(
                Chunk(
                    text=split.text,
                    start=_make_pos(start_line),
                    end=_make_pos(end_line),
                )
            )

    # Merge tiny chunks (<100 chars) with previous
    merged: list[Chunk] = []
    for chunk in expanded:
        if merged and len(chunk.text) < 100:
            prev = merged[-1]
            merged[-1] = Chunk(
                text=prev.text + "\n\n" + chunk.text,
                start=prev.start,
                end=chunk.end,
            )
        else:
            merged.append(chunk)

    return merged


def markdown_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    if not _is_domain_doc(path):
        # Non-domain markdown — return empty list signals no custom chunking.
        # The cocoindex_code indexer requires chunks, so we must produce them.
        # Use a simple header-based split as a reasonable default.
        return None, _split_by_headers(content)

    return "markdown", _split_by_headers(content)
