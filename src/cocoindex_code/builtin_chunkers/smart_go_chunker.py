"""Go chunker — splits by top-level function/method/type boundaries via tree-sitter.

Activates for all .go files. Uses tree-sitter-go to identify package-level
declarations (functions, types, methods, interfaces). Falls back to
RecursiveSplitter for files that fail parsing.
"""

from __future__ import annotations

from typing import Any
from pathlib import Path

from cocoindex_code.chunking import Chunk, TextPosition
from cocoindex.ops.text import RecursiveSplitter
from cocoindex_code.builtin_chunkers.tree_sitter_util import tree_sitter_available, get_parser, byte_offset_to_line_col

_CHUNK_SIZE = 1500
_MIN_CHUNK_SIZE = 300
_CHUNK_OVERLAP = 100

_splitter = RecursiveSplitter()


def _pos(byte_offset: int, content_bytes: bytes) -> TextPosition:
    """Compute TextPosition from byte offset."""
    line, column = byte_offset_to_line_col(content_bytes, byte_offset)
    return TextPosition(
        byte_offset=byte_offset, char_offset=byte_offset, line=line, column=column
    )


# Top-level declarations to chunk on
_GO_SPLIT_NODES = {
    "function_declaration",
    "method_declaration",
    "type_declaration",
    "const_declaration",
    "var_declaration",
    "interface_type",
}


def _collect_top_level_spans(
    tree_root: Any, allowed_types: set[str]
) -> list[tuple[int, int]]:
    """Walk the tree's top-level children and return (start, end) byte ranges."""
    spans: list[tuple[int, int]] = []
    for child in tree_root.children:
        if child.type in allowed_types:
            spans.append((child.start_byte, child.end_byte))
    return spans


def _ast_chunks(
    content: str, language: str, allowed_types: set[str]
) -> list[Chunk]:
    """Parse content with tree-sitter and extract declaration chunks."""
    parser = _get_parser(language)
    content_bytes = content.encode("utf-8")
    tree = parser.parse(content_bytes)
    spans = _collect_top_level_spans(tree.root_node, allowed_types)

    if not spans:
        return []

    chunks: list[Chunk] = []

    # Header: text before the first top-level declaration (package decl, imports).
    first_start = spans[0][0]
    if first_start > 0:
        header_bytes = content_bytes[:first_start]
        header = header_bytes.decode("utf-8").strip()
        if header:
            chunks.append(
                Chunk(text=header, start=_pos(0, content_bytes), end=_pos(first_start, content_bytes))
            )

    # One chunk per top-level declaration; oversized nodes are re-split.
    for start, end in spans:
        block = content_bytes[start:end].decode("utf-8", errors="replace").strip()
        if not block:
            continue
        if len(block) > _CHUNK_SIZE:
            start_line = _pos(start, content_bytes).line
            sub = _splitter.split(
                block,
                chunk_size=_CHUNK_SIZE,
                min_chunk_size=_MIN_CHUNK_SIZE,
                chunk_overlap=_CHUNK_OVERLAP,
                language="go",
            )
            if sub:
                for s in sub:
                    chunks.append(
                        Chunk(
                            text=s.text,
                            start=TextPosition(
                                byte_offset=s.start.byte_offset,
                                char_offset=s.start.char_offset,
                                line=s.start.line + start_line - 1,
                                column=s.start.column,
                            ),
                            end=TextPosition(
                                byte_offset=s.end.byte_offset,
                                char_offset=s.end.char_offset,
                                line=s.end.line + start_line - 1,
                                column=s.end.column,
                            ),
                        )
                    )
                continue

        chunks.append(
            Chunk(
                text=block,
                start=_pos(start, content_bytes),
                end=_pos(end, content_bytes),
            )
        )

    return chunks


def go_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    """Go chunker using tree-sitter; falls back to generic splitter on failure."""
    if not tree_sitter_available():
        # Tree-sitter unavailable; use generic splitter
        fallback = _splitter.split(
            content,
            chunk_size=_CHUNK_SIZE,
            min_chunk_size=_MIN_CHUNK_SIZE,
            chunk_overlap=_CHUNK_OVERLAP,
            language="go",
        )
        if fallback:
            return "go", fallback
        # Last-resort: single chunk
        content_bytes = content.encode("utf-8")
        return "go", [
            Chunk(
                text=content,
                start=_pos(0, content_bytes),
                end=_pos(len(content_bytes), content_bytes),
            )
        ]
    
    try:
        chunks = _ast_chunks(content, "go", _GO_SPLIT_NODES)
        if chunks:
            return "go", chunks
    except Exception as _exc:
        import traceback; traceback.print_exc()
    
    # Fallback to generic splitter
    fallback = _splitter.split(
        content,
        chunk_size=_CHUNK_SIZE,
        min_chunk_size=_MIN_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
        language="go",
    )
    if fallback:
        return "go", fallback
    
    # Last-resort: single chunk
    content_bytes = content.encode("utf-8")
    return "go", [
        Chunk(
            text=content,
            start=_pos(0, content_bytes),
            end=_pos(len(content_bytes), content_bytes),
        )
    ]
