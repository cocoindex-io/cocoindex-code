"""Lightweight stubs for cocoindex imports so chunker unit tests run without
needing the full cocoindex runtime / embedding server.

Each chunker imports:
    from cocoindex_code.chunking import Chunk, TextPosition
    from cocoindex.ops.text import RecursiveSplitter

We install minimal stand-ins for those symbols before pytest collects the
test modules. Keep the stubs behaviour-compatible with the upstream shapes
(see https://github.com/cocoindex-io/cocoindex-code/blob/main/src/cocoindex_code/chunking.py).
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class TextPosition:
    byte_offset: int = 0
    char_offset: int = 0
    line: int = 1
    column: int = 0


@dataclass(frozen=True)
class Chunk:
    text: str
    start: TextPosition
    end: TextPosition


class RecursiveSplitter:
    """Trivial splitter that honours chunk_size + overlap.

    Behaviour-compatible enough for our chunkers: returns at least one chunk
    per input, uses the requested `chunk_size` as the upper bound, and preserves
    relative line offsets so rebasing logic in the real chunkers remains exercised.
    """

    def split(
        self,
        content: str,
        *,
        chunk_size: int = 1000,
        min_chunk_size: int = 250,
        chunk_overlap: int = 150,
        language: str | None = None,
    ) -> List[Chunk]:
        if not content:
            return []
        lines = content.split("\n")
        total = len(lines)
        if len(content) <= chunk_size:
            return [Chunk(text=content, start=TextPosition(line=1), end=TextPosition(line=total))]

        chunks: List[Chunk] = []
        step = max(chunk_size // 40 or 1, 5)  # approximate line stride
        current = 0
        while current < total:
            segment_lines = lines[current : current + step]
            text = "\n".join(segment_lines)
            if not text.strip():
                current += step
                continue
            chunks.append(
                Chunk(
                    text=text,
                    start=TextPosition(line=current + 1),
                    end=TextPosition(line=min(current + step, total)),
                )
            )
            # honour overlap roughly
            current += max(step - max(chunk_overlap // 40, 1), 1)
        return chunks


class ContextKey:
    """Small generic-like stub matching cocoindex.ContextKey construction."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __class_getitem__(cls, _item: object) -> type["ContextKey"]:
        return cls


def _install_stubs() -> None:
    """Inject minimal cocoindex modules needed by builtin chunker tests."""
    if "cocoindex.resources.chunk" not in sys.modules:
        coco = types.ModuleType("cocoindex")
        coco.ContextKey = ContextKey  # type: ignore[attr-defined]
        resources = types.ModuleType("cocoindex.resources")
        chunk = types.ModuleType("cocoindex.resources.chunk")
        chunk.Chunk = Chunk  # type: ignore[attr-defined]
        chunk.TextPosition = TextPosition  # type: ignore[attr-defined]
        sys.modules["cocoindex"] = coco
        sys.modules["cocoindex.resources"] = resources
        sys.modules["cocoindex.resources.chunk"] = chunk

    if "cocoindex.ops.text" not in sys.modules:
        coco = sys.modules.get("cocoindex", types.ModuleType("cocoindex"))
        ops = types.ModuleType("cocoindex.ops")
        text = types.ModuleType("cocoindex.ops.text")
        text.RecursiveSplitter = RecursiveSplitter  # type: ignore[attr-defined]
        sys.modules["cocoindex"] = coco
        sys.modules["cocoindex.ops"] = ops
        sys.modules["cocoindex.ops.text"] = text


_install_stubs()


# Ensure the source tree is importable when tests run directly from the repo.
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
