"""CocoIndex app for indexing codebases."""

from __future__ import annotations

import logging
from pathlib import Path

import cocoindex as coco
from cocoindex.connectors import localfs, sqlite
from cocoindex.connectors.sqlite import Vec0TableDef
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.chunk import Chunk, TextPosition
from cocoindex.resources.id import IdGenerator

from .chunking import CHUNKER_REGISTRY
from .file_walk import build_matcher
from .settings import load_project_settings
from .shared import (
    CODEBASE_DIR,
    EMBEDDER,
    INDEXING_EMBED_PARAMS,
    SQLITE_DB,
    CodeChunk,
)

logger = logging.getLogger(__name__)

# Chunking configuration
CHUNK_SIZE = 1000
MIN_CHUNK_SIZE = 250
CHUNK_OVERLAP = 150

# Chunking splitter (stateless, can be module-level)
splitter = RecursiveSplitter()


def _position_after(start: TextPosition, text: str) -> TextPosition:
    """The position reached by reading *text* from *start*."""
    newlines = text.count("\n")
    if newlines:
        # 1-based column, counting from the character after the last newline.
        column = len(text) - text.rfind("\n")
    else:
        column = start.column + len(text)
    # An oversized chunk is a packed array, base64 or minified code, so it is
    # ASCII in practice — where the byte length is already known and encoding a
    # copy of every piece just to measure it is waste.
    byte_len = len(text) if text.isascii() else len(text.encode("utf-8"))
    return TextPosition(
        byte_offset=start.byte_offset + byte_len,
        char_offset=start.char_offset + len(text),
        line=start.line + newlines,
        column=column,
    )


def cap_chunk_size(
    chunks: list[Chunk],
    limit: int = CHUNK_SIZE,
    path: str | None = None,
) -> list[Chunk]:
    """Cut every chunk down to *limit* characters, keeping positions truthful.

    `chunk_size` is a target the splitter cannot always meet: a line that holds
    no separator it recognises comes back whole, however long it is. A Godot
    `.tscn` stores a packed array as one 61k-character line, and that chunk
    reached the embedder intact — which is fatal, because the embedder pads a
    batch to its longest member and attention is quadratic in that length.
    Measured on that one file: the daemon went from 0.8 GB to 106 GB of private
    commit in three seconds and died with an empty log.

    The default limit is CHUNK_SIZE itself, and not a multiple of it, for the
    same quadratic reason — at 4x that file still peaked at 32 GB, because
    `max_batch_size` is 64 upstream and 64 x 4000 characters is a batch no
    consumer GPU holds. Everything downstream is sized for chunks of that
    order, so the ceiling is simply the promise the pipeline already makes.

    Applied to custom chunkers as well as to the splitter — a chunker that
    returns the whole file as one chunk is a documented use, and it must not be
    able to OOM the daemon either. Chunks already within the limit are returned
    as they came, which is every chunk of every ordinary repository.
    """
    if all(len(chunk.text) <= limit for chunk in chunks):
        return chunks

    capped: list[Chunk] = []
    for chunk in chunks:
        length = len(chunk.text)
        if length <= limit:
            capped.append(chunk)
            continue
        logger.warning(
            "Chunk of %d chars exceeds the %d-char ceiling%s — splitting it; "
            "the source line is longer than the chunker can divide",
            length,
            limit,
            f" in {path}" if path else "",
        )
        # Even pieces rather than limit-sized ones with a remainder: a tail of
        # a few characters would be an embedding of noise, and nothing else in
        # the pipeline emits a chunk below MIN_CHUNK_SIZE.
        pieces = -(-length // limit)
        size = -(-length // pieces)
        start = chunk.start
        for offset in range(0, length, size):
            piece = chunk.text[offset : offset + size]
            end = _position_after(start, piece)
            capped.append(Chunk(text=piece, start=start, end=end))
            start = end
    return capped


@coco.fn(memo=True)
async def process_file(
    file: localfs.File,
    table: sqlite.TableTarget[CodeChunk],
) -> None:
    """Process a single file: chunk, embed, and store."""
    embedder = coco.use_context(EMBEDDER)
    indexing_params = coco.use_context(INDEXING_EMBED_PARAMS)

    try:
        content = await file.read_text()
    except UnicodeDecodeError:
        return

    if not content.strip():
        return

    suffix = file.file_path.path.suffix
    project_root = coco.use_context(CODEBASE_DIR)
    ps = load_project_settings(project_root)
    ext_lang_map = {f".{lo.ext}": lo.lang for lo in ps.language_overrides}
    language = (
        ext_lang_map.get(suffix)
        or detect_code_language(filename=file.file_path.path.name)
        or "text"
    )

    chunker_registry = coco.use_context(CHUNKER_REGISTRY)
    chunker = chunker_registry.get(suffix)
    if chunker is not None:
        language_override, chunks = chunker(Path(file.file_path.path), content)
        if language_override is not None:
            language = language_override
    else:
        chunks = splitter.split(
            content,
            chunk_size=CHUNK_SIZE,
            min_chunk_size=MIN_CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            language=language,
        )

    chunks = cap_chunk_size(chunks, path=file.file_path.path.as_posix())

    id_gen = IdGenerator()

    async def process(chunk: Chunk) -> None:
        table.declare_row(
            row=CodeChunk(
                id=await id_gen.next_id(chunk.text),
                file_path=file.file_path.path.as_posix(),
                language=language,
                content=chunk.text,
                start_line=chunk.start.line,
                end_line=chunk.end.line,
                embedding=await embedder.embed(chunk.text, **indexing_params),
            )
        )

    await coco.map(process, chunks)


@coco.fn
async def indexer_main() -> None:
    """Main indexing function - walks files and processes each."""
    project_root = coco.use_context(CODEBASE_DIR)
    ps = load_project_settings(project_root)

    table = await sqlite.mount_table_target(
        db=SQLITE_DB,
        table_name="code_chunks_vec",
        table_schema=await sqlite.TableSchema.from_class(
            CodeChunk,
            primary_key=["id"],
        ),
        virtual_table_def=Vec0TableDef(
            partition_key_columns=["language"],
            auxiliary_columns=["file_path", "content", "start_line", "end_line"],
        ),
    )

    matcher = build_matcher(
        project_root, ps.include_patterns, ps.exclude_patterns, ps.max_file_size
    )

    files = localfs.walk_dir(
        CODEBASE_DIR,
        recursive=True,
        path_matcher=matcher,
    )

    await coco.mount_each(
        coco.component_subpath(coco.Symbol("process_file")), process_file, files.items(), table
    )
