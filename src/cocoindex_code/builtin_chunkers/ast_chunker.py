"""Tree-sitter AST chunker — opt-in structural splitting for Python & TypeScript.

Why
---
The regex-based chunkers in this directory are fast and need no extra deps, but
they miss cases that an AST parser handles cleanly:

- TypeScript's ``export default class <Name> { ... }`` with nested class bodies
- Python type-subscripted defaults (``def f(x: Callable[[int], int] = …)``)
- Multi-line signatures split across balanced brackets
- JSDoc / docstrings glued to the wrong symbol

Borrowed patterns
-----------------
- AST-accurate chunking: ``cocoindex-io/cocoindex-code-examples/code_elements_indexing``
  (declaration/reference extraction via tree-sitter).
- Lazy-import + thread-safe availability probe: ``chopratejas/headroom``'s
  ``_tree_sitter_available`` flag and per-language cache under a ``Lock``.

Status
------
**Opt-in.** Not registered in ``muth-hq-settings.yml`` by default because
``tree_sitter_languages`` ships native wheels that are not guaranteed on Python
3.13.x — some build environments need ``pip install tree-sitter`` + an
architecture-specific language grammar.

To enable: ensure the upstream ``cocoindex-code`` package is installed with
declaration extras and add entries like this to
``muth-hq-settings.yml``::

    chunkers:
      - ext: py
        module: "ast_chunker:python_ast_chunker"
      - ext: ts
        module: "ast_chunker:typescript_ast_chunker"
      - ext: tsx
        module: "ast_chunker:typescript_ast_chunker"

The module falls back to the regex chunkers if tree-sitter is unavailable —
users see no regression, just no AST accuracy.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from cocoindex_code.chunking import Chunk, TextPosition
from cocoindex.ops.text import RecursiveSplitter

# Re-use the regex chunkers as the fallback path. They have their own
# oversized-block resplit logic and language-aware splitter integration.
from cocoindex_code.builtin_chunkers.smart_py_chunker import python_chunker as _py_fallback
from cocoindex_code.builtin_chunkers.smart_ts_chunker import typescript_chunker as _ts_fallback

_CHUNK_SIZE_MAX = 2000  # bytes — harder cap than regex chunkers; AST boundaries are precise
_splitter = RecursiveSplitter()

# Cached parsers, keyed by language. Tree-sitter's Parser is not thread-safe,
# so we keep one per language and guard lookups behind a lock.
_PARSER_CACHE: dict[str, Any] = {}
_PARSER_LOCK = threading.Lock()
_TREE_SITTER_AVAILABLE: bool | None = None


def _tree_sitter_available() -> bool:
    """Probe once whether tree_sitter_languages is importable.

    Idempotent; repeat calls are essentially free after the first.
    """
    global _TREE_SITTER_AVAILABLE
    if _TREE_SITTER_AVAILABLE is not None:
        return _TREE_SITTER_AVAILABLE
    try:
        import tree_sitter_languages  # noqa: F401

        _TREE_SITTER_AVAILABLE = True
    except Exception:
        _TREE_SITTER_AVAILABLE = False
    return _TREE_SITTER_AVAILABLE


def _get_parser(language: str) -> Any:
    """Return a cached tree-sitter Parser for *language*."""
    with _PARSER_LOCK:
        parser = _PARSER_CACHE.get(language)
        if parser is not None:
            return parser
        import tree_sitter_languages

        parser = tree_sitter_languages.get_parser(language)
        _PARSER_CACHE[language] = parser
        return parser


def _pos(byte_offset: int, content_bytes: bytes) -> TextPosition:
    # line is 1-based; count newlines up to byte_offset.
    prefix = content_bytes[:byte_offset]
    line = prefix.count(b"\n") + 1
    last_nl = prefix.rfind(b"\n")
    column = byte_offset - (last_nl + 1) if last_nl != -1 else byte_offset
    return TextPosition(
        byte_offset=byte_offset, char_offset=byte_offset, line=line, column=column
    )


# Tree-sitter node types that should become standalone chunks. Kept per-language
# because grammar names differ.
_PYTHON_SPLIT_NODES = {
    "function_definition",
    "async_function_definition",
    "decorated_definition",
    "class_definition",
}

_TYPESCRIPT_SPLIT_NODES = {
    "function_declaration",
    "class_declaration",
    "abstract_class_declaration",
    "method_definition",
    "export_statement",
    "lexical_declaration",
    "interface_declaration",
    "type_alias_declaration",
    "enum_declaration",
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
    parser = _get_parser(language)
    content_bytes = content.encode("utf-8")
    tree = parser.parse(content_bytes)
    spans = _collect_top_level_spans(tree.root_node, allowed_types)

    if not spans:
        return []

    chunks: list[Chunk] = []

    # Header: text before the first top-level declaration (imports, module docstring).
    first_start = spans[0][0]
    if first_start > 0:
        header_bytes = content_bytes[:first_start]
        header = header_bytes.decode("utf-8").strip()
        if header:
            chunks.append(
                Chunk(text=header, start=_pos(0, content_bytes), end=_pos(first_start, content_bytes))
            )

    # One chunk per top-level declaration. Oversized nodes are re-split with
    # RecursiveSplitter (same pattern as smart_ts_chunker._resplit_large) so
    # each sub-chunk stays within the embedding model's context window while
    # preserving as much of the declaration body as possible.
    for start, end in spans:
        block = content_bytes[start:end].decode("utf-8", errors="replace").strip()
        if not block:
            continue
        if len(block) > _CHUNK_SIZE_MAX:
            start_line = _pos(start, content_bytes).line
            sub = _splitter.split(
                block,
                chunk_size=_CHUNK_SIZE_MAX,
                min_chunk_size=_CHUNK_SIZE_MAX // 4,
                chunk_overlap=_CHUNK_SIZE_MAX // 8,
                language=language,
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
            # RecursiveSplitter returned nothing (shouldn't happen) — fall
            # through to emit the block as a single chunk below.
        chunks.append(
            Chunk(
                text=block,
                start=_pos(start, content_bytes),
                end=_pos(end, content_bytes),
            )
        )

    return chunks


def python_ast_chunker(
    path: Path, content: str
) -> tuple[str | None, list[Chunk]]:
    """AST-accurate Python chunker; falls back to regex chunker on failure."""
    if not _tree_sitter_available():
        return _py_fallback(path, content)
    try:
        chunks = _ast_chunks(content, "python", _PYTHON_SPLIT_NODES)
        if chunks:
            return "python", chunks
    except Exception:
        pass
    return _py_fallback(path, content)


def typescript_ast_chunker(
    path: Path, content: str
) -> tuple[str | None, list[Chunk]]:
    """AST-accurate TS/TSX chunker; falls back to regex chunker on failure."""
    if not _tree_sitter_available():
        return _ts_fallback(path, content)
    language = "tsx" if path.suffix.lower() == ".tsx" else "typescript"
    try:
        chunks = _ast_chunks(content, language, _TYPESCRIPT_SPLIT_NODES)
        if chunks:
            return "typescript", chunks
    except Exception:
        pass
    return _ts_fallback(path, content)
