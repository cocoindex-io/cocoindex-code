"""Workspace JSON chunker — one chunk per service/repo/infrastructure section.

Only activates for `workspace.json`. Other JSON files use RecursiveSplitter.

Line numbers are derived from the source text (not the re-serialized output)
so that search results land on the right row in an editor.
"""

from __future__ import annotations

import bisect
import json
import re
from pathlib import Path

from cocoindex_code.chunking import Chunk, TextPosition
from cocoindex.ops.text import RecursiveSplitter

_splitter = RecursiveSplitter()
_CHUNK_SIZE = 1000
_MIN_CHUNK_SIZE = 250
_CHUNK_OVERLAP = 150


def _build_line_index(content: str) -> list[int]:
    """Return sorted list of newline character offsets in *content*."""
    return [i for i, c in enumerate(content) if c == "\n"]


def _offset_to_line(nl_offsets: list[int], pos: int) -> int:
    """Convert a character offset to a 1-based line number using a precomputed index."""
    return bisect.bisect_left(nl_offsets, pos) + 1


def _pos(line: int) -> TextPosition:
    return TextPosition(byte_offset=0, char_offset=0, line=max(line, 1), column=0)


def _top_key_line(nl_offsets: list[int], content: str, key: str) -> int:
    """1-based line of `"<key>":` at indent <= 4 (practical "top level")."""
    pattern = re.compile(rf'^\s{{0,4}}"{re.escape(key)}"\s*:', re.MULTILINE)
    match = pattern.search(content)
    if not match:
        return 1
    return _offset_to_line(nl_offsets, match.start())


def _labelled_line(nl_offsets: list[int], content: str, parent_key: str, item_name: str) -> int:
    """Line of an item inside a repositories/services array by its name/id."""
    parent = re.compile(rf'"{re.escape(parent_key)}"\s*:')
    pm = parent.search(content)
    start = pm.end() if pm else 0
    anchor = re.compile(rf'"(?:name|id)"\s*:\s*"{re.escape(item_name)}"')
    match = anchor.search(content, start)
    if not match:
        return 1
    return _offset_to_line(nl_offsets, match.start())


def _text_chunk(path: Path, label: str, text: str, line: int) -> Chunk:
    decorated = f"// {path.name} > {label}\n{text}"
    line_span = text.count("\n")
    return Chunk(text=decorated, start=_pos(line), end=_pos(line + line_span))


def _chunk_workspace(path: Path, content: str) -> list[Chunk]:
    data = json.loads(content)
    # Build newline index once — shared by all line-lookup calls below.
    nl_offsets = _build_line_index(content)
    chunks: list[Chunk] = []

    for key in ("github", "infrastructure"):
        if key not in data:
            continue
        section = data[key]
        if key == "infrastructure" and isinstance(section, dict):
            for subkey, subval in section.items():
                text = json.dumps({subkey: subval}, indent=2)
                line = _top_key_line(nl_offsets, content, subkey)
                chunks.append(_text_chunk(path, f"infrastructure.{subkey}", text, line))
        else:
            text = json.dumps({key: section}, indent=2)
            line = _top_key_line(nl_offsets, content, key)
            chunks.append(_text_chunk(path, key, text, line))

    for repo in data.get("repositories", []) or []:
        name = repo.get("name") or repo.get("id") or "unknown"
        text = json.dumps(repo, indent=2)
        line = _labelled_line(nl_offsets, content, "repositories", name)
        chunks.append(_text_chunk(path, f"repositories.{name}", text, line))

    for svc in data.get("services", []) or []:
        name = svc.get("name") or svc.get("id") or "unknown"
        text = json.dumps(svc, indent=2)
        line = _labelled_line(nl_offsets, content, "services", name)
        chunks.append(_text_chunk(path, f"services.{name}", text, line))

    return chunks


def workspace_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    if path.name != "workspace.json":
        chunks = _splitter.split(
            content,
            chunk_size=_CHUNK_SIZE,
            min_chunk_size=_MIN_CHUNK_SIZE,
            chunk_overlap=_CHUNK_OVERLAP,
            language="json",
        )
        return None, chunks

    try:
        return "json", _chunk_workspace(path, content)
    except (json.JSONDecodeError, KeyError, TypeError):
        chunks = _splitter.split(
            content,
            chunk_size=_CHUNK_SIZE,
            min_chunk_size=_MIN_CHUNK_SIZE,
            chunk_overlap=_CHUNK_OVERLAP,
            language="json",
        )
        return None, chunks
