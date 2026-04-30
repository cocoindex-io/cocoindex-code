"""Smart TypeScript/JavaScript chunker — splits by top-level exports.

Previously only activated for Zod schema files under shared/packages/types/src/.
That left most of the codebase (services, apps, workers) without structural
chunking. We now apply export-aware splitting to every .ts/.tsx/.js/.jsx/.mjs/
.cjs file and fall back to RecursiveSplitter only for files with no detectable
top-level exports.

Oversized exported blocks (> _CHUNK_SIZE) are re-split with RecursiveSplitter to
keep embeddings well-scoped for retrieval.
"""

from __future__ import annotations

import re
from pathlib import Path

from cocoindex_code.chunking import Chunk, TextPosition
from cocoindex.ops.text import RecursiveSplitter

_splitter = RecursiveSplitter()
_CHUNK_SIZE = 1000
_MIN_CHUNK_SIZE = 250
_CHUNK_OVERLAP = 150

# Match a broad set of top-level export forms. Captured groups are diagnostic
# only — we only need the line offset for chunk boundaries.
#
# Handles:
#   export const X = ...
#   export let/var X = ...
#   export type X = ...
#   export interface X ...
#   export enum X ...
#   export function X(...) / export async function
#   export class X / export abstract class
#   export default function X?(...)
#   export default class X?
#   export { X, Y }  /  export { X } from '...'
#   export * from '...'
_EXPORT_RE = re.compile(
    r"""^
    export
    (?:\s+default)?
    \s+
    (?:
        (?:async\s+)?function\*?\s*\w*
      | (?:abstract\s+)?class\s+\w+
      | (?:const|let|var)\s+\w+
      | (?:type|interface|enum|namespace|module)\s+\w+
      | \{
      | \*
    )
    """,
    re.MULTILINE | re.VERBOSE,
)


def _make_pos(line: int) -> TextPosition:
    return TextPosition(byte_offset=0, char_offset=0, line=line, column=0)


def _resplit_large(block_text: str, start_line: int, language: str) -> list[Chunk]:
    """Re-split an oversized block with RecursiveSplitter, rebasing line offsets."""
    sub = _splitter.split(
        block_text,
        chunk_size=_CHUNK_SIZE,
        min_chunk_size=_MIN_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
        language=language,
    )
    if not sub:
        return []
    return [
        Chunk(
            text=s.text,
            start=_make_pos(s.start.line + start_line - 1),
            end=_make_pos(s.end.line + start_line - 1),
        )
        for s in sub
    ]


def _chunk_by_exports(content: str, language: str) -> list[Chunk]:
    """Split TypeScript/JavaScript by top-level export declarations."""
    lines = content.split("\n")
    if not lines:
        return []

    export_lines: list[int] = [
        i for i, line in enumerate(lines) if _EXPORT_RE.match(line)
    ]
    if not export_lines:
        return []

    chunks: list[Chunk] = []

    # Header: imports + preamble before the first export.
    first_export = export_lines[0]
    if first_export > 0:
        header = "\n".join(lines[:first_export]).strip()
        if header:
            chunks.append(
                Chunk(text=header, start=_make_pos(1), end=_make_pos(first_export))
            )

    for idx, start in enumerate(export_lines):
        end = export_lines[idx + 1] if idx + 1 < len(export_lines) else len(lines)
        while end > start and lines[end - 1].strip() == "":
            end -= 1

        # Pull in preceding JSDoc/line comments + blanks so each exported symbol
        # carries its doc block into the embedding.
        # The walk is capped at 20 lines to avoid O(N) traversal on dense
        # comment blocks or files with no blank-line separators between exports.
        comment_start = start
        while comment_start > max(0, start - 20):
            prev = lines[comment_start - 1].strip()
            if (
                prev.startswith("//")
                or prev.startswith("*")
                or prev.startswith("/*")
                or prev.startswith("/**")
                or prev.endswith("*/")
                or prev == ""
            ):
                comment_start -= 1
            else:
                break
        while comment_start < start and lines[comment_start].strip() == "":
            comment_start += 1

        text = "\n".join(lines[comment_start:end]).strip()
        if not text:
            continue

        if len(text) > _CHUNK_SIZE:
            resplit = _resplit_large(text, comment_start + 1, language)
            if resplit:
                chunks.extend(resplit)
                continue

        chunks.append(
            Chunk(text=text, start=_make_pos(comment_start + 1), end=_make_pos(end))
        )

    return chunks


def _language_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".ts", ".tsx"}:
        return "typescript"
    return "javascript"


def _chunk(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    language = _language_for(path)
    exports = _chunk_by_exports(content, language)
    if exports:
        return language, exports

    fallback = _splitter.split(
        content,
        chunk_size=_CHUNK_SIZE,
        min_chunk_size=_MIN_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
        language=language,
    )
    return None, fallback


def typescript_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    return _chunk(path, content)


def javascript_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    return _chunk(path, content)
