"""Cell-aware Jupyter notebook chunker for CocoIndex flow (G12).

Each notebook cell becomes one chunk.  Metadata (cell type, execution count)
is prepended as a comment block so the embedding captures context.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from cocoindex_code.chunking import Chunk, TextPosition
except ImportError:
    # Graceful degradation when not running inside the ccc flow environment.
    class TextPosition:  # type: ignore[no-redef]
        def __init__(self, *, byte_offset: int = 0, char_offset: int = 0, line: int = 1, column: int = 0) -> None:
            self.byte_offset = byte_offset
            self.char_offset = char_offset
            self.line = line
            self.column = column

    class Chunk:  # type: ignore[no-redef]
        def __init__(self, *, text: str, start: Any, end: Any) -> None:
            self.text = text
            self.start = start
            self.end = end


def notebook_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    """Chunk a Jupyter notebook (.ipynb) by cell.

    Returns language tag ``"jupyter"`` and one Chunk per non-empty cell.
    Empty cells and cells whose source is only whitespace are skipped.
    """
    try:
        nb: dict[str, Any] = json.loads(content)
    except json.JSONDecodeError:
        return None, []

    cells: list[Any] = nb.get("cells", [])
    chunks: list[Chunk] = []
    byte_offset = 0
    char_offset = 0
    line = 1

    for i, cell in enumerate(cells):
        cell_type: str = str(cell.get("cell_type", "code"))
        source_parts = cell.get("source", [])
        if isinstance(source_parts, list):
            source = "".join(source_parts)
        else:
            source = str(source_parts)

        stripped = source.strip()
        if not stripped:
            continue

        execution_count = cell.get("execution_count") or ""
        header = f"# Cell {i + 1} [{cell_type}]"
        if execution_count:
            header += f" In[{execution_count}]"
        text = f"{header}\n{stripped}"

        start_pos = TextPosition(
            byte_offset=byte_offset,
            char_offset=char_offset,
            line=line,
            column=0,
        )
        text_bytes = text.encode("utf-8", errors="replace")
        byte_offset += len(text_bytes)
        char_offset += len(text)
        line += text.count("\n") + 1

        end_pos = TextPosition(
            byte_offset=byte_offset,
            char_offset=char_offset,
            line=line,
            column=0,
        )
        chunks.append(Chunk(text=text, start=start_pos, end=end_pos))

    return "jupyter", chunks
