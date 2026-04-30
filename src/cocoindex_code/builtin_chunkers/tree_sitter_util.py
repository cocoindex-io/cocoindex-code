"""Shared tree-sitter utilities for language chunkers.

Consolidates parser caching, availability checks, and TextPosition computation
to eliminate duplication across smart_go_chunker.py, smart_rust_chunker.py, etc.
"""

from __future__ import annotations

import threading
from typing import Any

# Module-level state: computed once at import time
_TREE_SITTER_AVAILABLE = False
_PARSER_CACHE: dict[str, Any] = {}
_PARSER_LOCK = threading.Lock()

# Probe tree-sitter availability at module load time
try:
    import tree_sitter_languages  # noqa: F401
    _TREE_SITTER_AVAILABLE = True
except Exception:
    _TREE_SITTER_AVAILABLE = False


def tree_sitter_available() -> bool:
    """Return whether tree_sitter_languages is available (computed at import)."""
    return _TREE_SITTER_AVAILABLE


def get_parser(language: str) -> Any:
    """Return a cached tree-sitter Parser for *language*.
    
    Thread-safe; caches per-language. Raises if tree-sitter unavailable.
    """
    with _PARSER_LOCK:
        parser = _PARSER_CACHE.get(language)
        if parser is not None:
            return parser
        if not _TREE_SITTER_AVAILABLE:
            raise RuntimeError("tree_sitter_languages not available")
        
        import tree_sitter_languages
        parser = tree_sitter_languages.get_parser(language)
        _PARSER_CACHE[language] = parser
        return parser


def byte_offset_to_line_col(
    content_bytes: bytes,
    byte_offset: int,
) -> tuple[int, int]:
    """Compute (line, column) from byte offset in content.
    
    Args:
        content_bytes: File content as bytes
        byte_offset: Byte offset to convert
    
    Returns:
        Tuple of (line number, column) where line is 1-indexed
    """
    prefix = content_bytes[:byte_offset]
    line = prefix.count(b"\n") + 1
    last_nl = prefix.rfind(b"\n")
    column = byte_offset - (last_nl + 1) if last_nl != -1 else byte_offset
    return line, column


def build_line_map(content_bytes: bytes) -> dict[int, int]:
    """Pre-compute byte offsets for all line starts (optimization for batch processing).
    
    Returns a dict mapping line_number -> byte_offset_of_line_start.
    This is useful when processing many byte offsets (e.g., chunker output).
    """
    line_map: dict[int, int] = {1: 0}
    line_num = 1
    for i, byte in enumerate(content_bytes):
        if byte == ord(b"\n"):
            line_num += 1
            line_map[line_num] = i + 1
    return line_map
