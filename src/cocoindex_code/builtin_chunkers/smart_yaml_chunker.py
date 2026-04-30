"""Path-aware YAML chunker for workflows, deploy configs, and compose files."""

from __future__ import annotations

import re
from pathlib import Path

from cocoindex_code.chunking import Chunk, TextPosition
from cocoindex.ops.text import RecursiveSplitter

_ROOT_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:\s+.*)?$")
_INDENTED_KEY_TEMPLATE = r"^\s{{{indent}}}([A-Za-z0-9_.-]+):(?:\s+.*)?$"
_LIST_ITEM_TEMPLATE = r"^\s{{{indent}}}-(?:\s+(.+))?$"
_NAMED_LIST_ITEM_RE = re.compile(r"^\s*-\s+name:\s*(.+?)\s*$")
_KEY_VALUE_RE = re.compile(r"([A-Za-z0-9_.-]+):\s*([^#]+)")

_MAPPING_SECTIONS = {"jobs", "services", "networks", "volumes", "secrets"}
_LIST_SECTIONS = {"steps", "updates"}

_splitter = RecursiveSplitter()
_CHUNK_SIZE = 1000
_MIN_CHUNK_SIZE = 250
_CHUNK_OVERLAP = 150


def _make_pos(line: int) -> TextPosition:
    return TextPosition(byte_offset=0, char_offset=0, line=line, column=0)


def _make_chunk(path: Path, label: str, lines: list[str], start_line: int) -> Chunk | None:
    text = "\n".join(lines).strip()
    if not text:
        return None
    decorated = f"# {path.as_posix()} > {label}\n{text}"
    end_line = start_line + max(len(lines) - 1, 0)
    return Chunk(text=decorated, start=_make_pos(start_line), end=_make_pos(end_line))


def _default_yaml_chunks(content: str) -> list[Chunk]:
    return _splitter.split(
        content,
        chunk_size=_CHUNK_SIZE,
        min_chunk_size=_MIN_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
        language="yaml",
    )


def _rechunk_long_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Split oversized YAML chunks with RecursiveSplitter and preserve line starts."""
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
            language="yaml",
        )
        if not split_chunks:
            expanded.append(chunk)
            continue

        for split in split_chunks:
            start_line = split.start.line + chunk.start.line - 1
            end_line = split.end.line + chunk.start.line - 1
            expanded.append(
                Chunk(
                    text=split.text,
                    start=TextPosition(
                        byte_offset=0, char_offset=0, line=start_line, column=0
                    ),
                    end=TextPosition(
                        byte_offset=0, char_offset=0, line=end_line, column=0
                    ),
                )
            )

    if expanded:
        return expanded

    # Defensive fallback preserves behavior on splitter edge-cases.
    return chunks


def _split_root_sections(lines: list[str]) -> list[tuple[str, int, list[str]]]:
    sections: list[tuple[str, int, list[str]]] = []
    current_label = "header"
    current_start = 1
    current_lines: list[str] = []

    for line_no, line in enumerate(lines, 1):
        if line and not line.startswith((" ", "\t", "-")):
            match = _ROOT_KEY_RE.match(line)
            if match:
                if current_lines:
                    sections.append((current_label, current_start, current_lines))
                current_label = match.group(1)
                current_start = line_no
                current_lines = [line]
                continue
        current_lines.append(line)

    if current_lines:
        sections.append((current_label, current_start, current_lines))

    return sections


def _split_nested_mapping(
    section_name: str, start_line: int, section_lines: list[str], indent: int = 2
) -> list[tuple[str, int, list[str]]]:
    key_re = re.compile(_INDENTED_KEY_TEMPLATE.format(indent=indent))
    chunks: list[tuple[str, int, list[str]]] = []
    prefix: list[str] = [section_lines[0]]
    current_label: str | None = None
    current_start = start_line
    current_lines: list[str] = []

    for offset, line in enumerate(section_lines[1:], 1):
        match = key_re.match(line)
        if match:
            if current_label is not None and current_lines:
                chunks.append(
                    (f"{section_name}.{current_label}", current_start, prefix + current_lines)
                )
            current_label = match.group(1)
            current_start = start_line + offset
            current_lines = [line]
            continue
        if current_label is None:
            prefix.append(line)
        else:
            current_lines.append(line)

    if current_label is not None and current_lines:
        chunks.append((f"{section_name}.{current_label}", current_start, prefix + current_lines))
    return chunks


def _list_item_label(line: str, index: int) -> str:
    named_match = _NAMED_LIST_ITEM_RE.match(line)
    if named_match:
        return named_match.group(1).strip().replace(" ", "-")

    key_match = _KEY_VALUE_RE.search(line)
    if key_match:
        return f"{key_match.group(1)}-{index}"

    return f"item-{index}"


def _split_named_list(
    section_name: str, start_line: int, section_lines: list[str], indent: int = 2
) -> list[tuple[str, int, list[str]]]:
    list_re = re.compile(_LIST_ITEM_TEMPLATE.format(indent=indent))
    chunks: list[tuple[str, int, list[str]]] = []
    prefix: list[str] = [section_lines[0]]
    current_label: str | None = None
    current_start = start_line
    current_lines: list[str] = []
    item_index = 0

    for offset, line in enumerate(section_lines[1:], 1):
        if list_re.match(line):
            if current_label is not None and current_lines:
                chunks.append(
                    (f"{section_name}.{current_label}", current_start, prefix + current_lines)
                )
            item_index += 1
            current_label = _list_item_label(line, item_index)
            current_start = start_line + offset
            current_lines = [line]
            continue
        if current_label is None:
            prefix.append(line)
        else:
            current_lines.append(line)

    if current_label is not None and current_lines:
        chunks.append((f"{section_name}.{current_label}", current_start, prefix + current_lines))
    return chunks


def _chunk_yaml(path: Path, content: str) -> list[Chunk]:
    lines = content.split("\n")
    root_sections = _split_root_sections(lines)
    chunks: list[Chunk] = []

    for section_name, start_line, section_lines in root_sections:
        nested_sections: list[tuple[str, int, list[str]]] = []
        if section_name in _MAPPING_SECTIONS:
            nested_sections = _split_nested_mapping(section_name, start_line, section_lines)
        elif section_name in _LIST_SECTIONS:
            nested_sections = _split_named_list(section_name, start_line, section_lines)

        if nested_sections:
            for label, nested_start, nested_lines in nested_sections:
                chunk = _make_chunk(path, label, nested_lines, nested_start)
                if chunk is not None:
                    chunks.extend(_rechunk_long_chunks([chunk]))
            continue

        chunk = _make_chunk(path, section_name, section_lines, start_line)
        if chunk is not None:
            chunks.extend(_rechunk_long_chunks([chunk]))

    return chunks or _default_yaml_chunks(content)


def yaml_chunker(path: Path, content: str) -> tuple[str | None, list[Chunk]]:
    try:
        return "yaml", _chunk_yaml(path, content)
    except re.error:
        return None, _default_yaml_chunks(content)
