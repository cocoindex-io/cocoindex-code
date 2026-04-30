"""Python chunker — splits by top-level class/function boundaries.

Activates for all .py and .pyi files. Keeps imports as a separate header
chunk so they remain searchable without polluting every function chunk.
Falls back to RecursiveSplitter for files that have no top-level definitions
(e.g. pure config files, __init__.py with only re-exports).
"""

from __future__ import annotations

import re
from pathlib import Path

from cocoindex_code.chunking import Chunk, TextPosition
from cocoindex.ops.text import RecursiveSplitter

_splitter = RecursiveSplitter()
_CHUNK_SIZE = 1000
_MIN_CHUNK_SIZE = 200
_CHUNK_OVERLAP = 100

# Top-level definition: class or def at column 0 (not indented)
_DEF_RE = re.compile(r"^((?:async\s+)?def|class)\s+(\w+)", re.MULTILINE)

# Decorator at column 0 (precedes a top-level def/class)
_DECORATOR_RE = re.compile(r"^@\S+", re.MULTILINE)

# Import lines (to extract header)
_IMPORT_RE = re.compile(r"^(?:import|from)\s+\S+", re.MULTILINE)


def _make_pos(line: int) -> TextPosition:
    return TextPosition(byte_offset=0, char_offset=0, line=line, column=0)


def _paren_balance(line: str) -> int:
    """Net paren depth change ignoring quoted strings.

    Used to fold multi-line decorators like `@dataclass(\\n  frozen=True,\\n)`
    back into the def boundary. A strict tokenizer would be overkill; decorators
    rarely embed brackets in strings, so a simple scan is enough.
    """
    depth = 0
    in_str: str | None = None
    escape = False
    for ch in line:
        if escape:
            escape = False
            continue
        if in_str is not None:
            if ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in ("'", '"'):
            in_str = ch
            continue
        if ch == "#":
            break
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
    return depth


def _split_by_definitions(content: str) -> list[Chunk]:
    lines = content.split("\n")
    n = len(lines)

    boundary_lines: list[int] = []
    i = 0
    while i < n:
        line = lines[i]
        if _DEF_RE.match(line):
            # Walk backwards absorbing decorators (including multi-line forms),
            # blank lines, and comment blocks. We track an "open deficit" — how
            # many `(` are still needed walking back to match a later `)` — so
            # the trailing `)` of a multi-line decorator stays glued to the def.
            start = i
            open_deficit = 0
            while start > 0:
                prev = lines[start - 1]
                stripped = prev.strip()
                delta = _paren_balance(prev)  # forward: opens - closes

                if open_deficit > 0:
                    # Still inside a multi-line decorator's argument list.
                    open_deficit -= delta
                    start -= 1
                    continue

                if stripped.startswith("@"):
                    # Top of a decorator (single-line or the `@name(` opener).
                    open_deficit -= delta
                    start -= 1
                    continue

                if stripped == "" or stripped.startswith("#"):
                    start -= 1
                    continue

                if delta < 0:
                    # A bare `)` (or continuation line) below the decorator —
                    # enter hunting mode so we absorb it and the rest of the block.
                    open_deficit -= delta
                    start -= 1
                    continue

                break
            # Skip leading blank lines
            while start < i and not lines[start].strip():
                start += 1
            boundary_lines.append(start)
        i += 1

    if not boundary_lines:
        return []

    chunks: list[Chunk] = []

    # Header: everything before first definition
    first_def = boundary_lines[0]
    if first_def > 0:
        header = "\n".join(lines[:first_def]).strip()
        if header:
            chunks.append(Chunk(text=header, start=_make_pos(1), end=_make_pos(first_def)))

    # Each definition block extends to the next boundary (or EOF)
    for idx, start_line in enumerate(boundary_lines):
        end_line = boundary_lines[idx + 1] if idx + 1 < len(boundary_lines) else n

        # Trim trailing blank lines
        while end_line > start_line and not lines[end_line - 1].strip():
            end_line -= 1

        block = "\n".join(lines[start_line:end_line]).strip()
        if not block:
            continue

        chunk = Chunk(
            text=block,
            start=_make_pos(start_line + 1),
            end=_make_pos(end_line),
        )

        # Split oversized blocks (e.g. very long classes)
        if len(block) > _CHUNK_SIZE:
            sub = _splitter.split(
                block,
                chunk_size=_CHUNK_SIZE,
                min_chunk_size=_MIN_CHUNK_SIZE,
                chunk_overlap=_CHUNK_OVERLAP,
                language="python",
            )
            if sub:
                for s in sub:
                    adjusted_start = s.start.line + start_line
                    adjusted_end = s.end.line + start_line
                    chunks.append(
                        Chunk(
                            text=s.text,
                            start=_make_pos(adjusted_start),
                            end=_make_pos(adjusted_end),
                        )
                    )
                continue

        chunks.append(chunk)

    return chunks


def python_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    try:
        chunks = _split_by_definitions(content)
        if chunks:
            return "python", chunks
    except Exception:
        pass

    # Fallback: generic splitter
    fallback = _splitter.split(
        content,
        chunk_size=_CHUNK_SIZE,
        min_chunk_size=_MIN_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
        language="python",
    )
    return "python", fallback or [
        Chunk(text=content, start=_make_pos(1), end=_make_pos(content.count("\n") + 1))
    ]
