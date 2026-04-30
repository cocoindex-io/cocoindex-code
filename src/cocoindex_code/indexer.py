"""CocoIndex app for indexing codebases."""

from __future__ import annotations

import threading
import time
from typing import Any
from pathlib import Path

import cocoindex as coco
from cocoindex.connectors import localfs, sqlite
from cocoindex.connectors.sqlite import Vec0TableDef
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.chunk import Chunk
from cocoindex.resources.file import FilePathMatcher, PatternFilePathMatcher
from cocoindex.resources.id import IdGenerator

from ._matchers import GitignoreAwareMatcher
from .chunking import CHUNKER_REGISTRY
from .settings import load_gitignore_spec, load_project_settings
from .shared import (
    CODEBASE_DIR,
    EMBEDDER,
    INDEXING_EMBED_PARAMS,
    SQLITE_DB,
    CodeChunk,
    Embedder,
)

# Chunking configuration
CHUNK_SIZE = 1000
MIN_CHUNK_SIZE = 250
CHUNK_OVERLAP = 150

# Chunking splitter (stateless, can be module-level)
splitter = RecursiveSplitter()


class PhaseTimingAccumulator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.files_timed = 0
        self.total_chunk_ms = 0.0
        self.total_embed_ms = 0.0
        self.total_write_ms = 0.0
        self.max_embed_ms = 0.0

    def reset(self) -> None:
        with self._lock:
            self.files_timed = 0
            self.total_chunk_ms = 0.0
            self.total_embed_ms = 0.0
            self.total_write_ms = 0.0
            self.max_embed_ms = 0.0

    def record(self, chunk_ms: float, embed_ms: float, write_ms: float) -> None:
        with self._lock:
            self.files_timed += 1
            self.total_chunk_ms += chunk_ms
            self.total_embed_ms += embed_ms
            self.total_write_ms += write_ms
            if embed_ms > self.max_embed_ms:
                self.max_embed_ms = embed_ms

    def snapshot(self) -> tuple[int, float, float, float, float]:
        with self._lock:
            return (
                self.files_timed,
                self.total_chunk_ms,
                self.total_embed_ms,
                self.total_write_ms,
                self.max_embed_ms,
            )


PHASE_TIMING = coco.ContextKey[PhaseTimingAccumulator]("phase_timing")


@coco.fn
async def process_chunk(
    chunk: Chunk,
    file_path: Path,
    language: str,
    id_gen: IdGenerator,
    embedder: Embedder,
    indexing_params: dict[str, Any],
    table: sqlite.TableTarget[CodeChunk],
) -> None:
    table.declare_row(
        row=CodeChunk(
            id=await id_gen.next_id(chunk.text),
            file_path=file_path.as_posix(),
            language=language,
            content=chunk.text,
            start_line=chunk.start.line,
            end_line=chunk.end.line,
            embedding=await embedder.embed(chunk.text, **indexing_params),
        )
    )


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
    chunk_started_at = time.monotonic()
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
    chunk_ms = (time.monotonic() - chunk_started_at) * 1000.0

    id_gen = IdGenerator()

    embed_started_at = time.monotonic()
    await coco.map(
        process_chunk,
        chunks,
        file.file_path.path,
        language,
        id_gen,
        embedder,
        indexing_params,
        table,
    )
    embed_ms = (time.monotonic() - embed_started_at) * 1000.0

    write_ms = 0.0

    try:
        coco.get_context(PHASE_TIMING).record(chunk_ms, embed_ms, write_ms)
    except Exception:
        pass


@coco.fn
async def indexer_main() -> None:
    """Main indexing function - walks files and processes each."""
    project_root = coco.use_context(CODEBASE_DIR)
    ps = load_project_settings(project_root)
    gitignore_spec = load_gitignore_spec(project_root)

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

    base_matcher = PatternFilePathMatcher(
        included_patterns=ps.include_patterns,
        excluded_patterns=ps.exclude_patterns,
    )
    matcher: FilePathMatcher = GitignoreAwareMatcher(base_matcher, gitignore_spec, project_root)

    files = localfs.walk_dir(
        CODEBASE_DIR,
        recursive=True,
        path_matcher=matcher,
    )

    await coco.mount_each(
        coco.component_subpath(coco.Symbol("process_file")), process_file, files.items(), table
    )
