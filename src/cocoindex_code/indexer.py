"""CocoIndex app for indexing codebases."""

from __future__ import annotations

import cocoindex as coco
from cocoindex.connectors import localfs, sqlite
from cocoindex.connectors.sqlite import Vec0TableDef
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.chunk import Chunk
from cocoindex.resources.file import PatternFilePathMatcher
from cocoindex.resources.id import IdGenerator

from .settings import PROJECT_SETTINGS
from .shared import CODEBASE_DIR, EMBEDDER, SQLITE_DB, CodeChunk

# Chunking configuration
CHUNK_SIZE = 2000
MIN_CHUNK_SIZE = 300
CHUNK_OVERLAP = 200

# Chunking splitter (stateless, can be module-level)
splitter = RecursiveSplitter()


@coco.fn(memo=True)
async def process_file(
    file: localfs.File,
    table: sqlite.TableTarget[CodeChunk],
) -> None:
    """Process a single file: chunk, embed, and store."""
    ps = coco.use_context(PROJECT_SETTINGS)
    embedder = coco.use_context(EMBEDDER)

    try:
        content = await file.read_text()
    except UnicodeDecodeError:
        return

    if not content.strip():
        return

    suffix = file.file_path.path.suffix
    # Check language overrides from project settings
    override_map = {f".{lo.ext}": lo.lang for lo in ps.language_overrides}
    language = (
        override_map.get(suffix)
        or detect_code_language(filename=file.file_path.path.name)
        or "text"
    )

    chunks = splitter.split(
        content,
        chunk_size=CHUNK_SIZE,
        min_chunk_size=MIN_CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        language=language,
    )

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
                embedding=await embedder.embed(chunk.text),
            )
        )

    await coco.map(process, chunks)


@coco.fn
async def indexer_main() -> None:
    """Main indexing function - walks files and processes each."""
    ps = coco.use_context(PROJECT_SETTINGS)

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

    files = localfs.walk_dir(
        CODEBASE_DIR,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=ps.include_patterns,
            excluded_patterns=ps.exclude_patterns,
        ),
    )

    with coco.component_subpath(coco.Symbol("process_file")):
        await coco.mount_each(process_file, files.items(), table)
