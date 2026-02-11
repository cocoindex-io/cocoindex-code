"""CocoIndex app for indexing codebases."""

import asyncio

import cocoindex.asyncio as coco_aio
from cocoindex.connectors import localfs, sqlite
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.chunk import Chunk
from cocoindex.resources.file import PatternFilePathMatcher
from cocoindex.resources.id import IdGenerator

from .shared import SQLITE_DB, CodeChunk, config, embedder

# File patterns for supported languages
INCLUDED_PATTERNS = [
    "**/*.py",  # Python
    "**/*.pyi",  # Python stubs
    "**/*.js",  # JavaScript
    "**/*.jsx",  # JavaScript React
    "**/*.ts",  # TypeScript
    "**/*.tsx",  # TypeScript React
    "**/*.mjs",  # JavaScript ES modules
    "**/*.cjs",  # JavaScript CommonJS
    "**/*.rs",  # Rust
    "**/*.go",  # Go
    "**/*.java",  # Java
    "**/*.c",  # C
    "**/*.h",  # C/C++ headers
    "**/*.cpp",  # C++
    "**/*.hpp",  # C++ headers
    "**/*.cc",  # C++
    "**/*.cxx",  # C++
    "**/*.hxx",  # C++ headers
    "**/*.hh",  # C++ headers
    "**/*.cs",  # C#
    "**/*.sql",  # SQL
    "**/*.sh",  # Shell
    "**/*.bash",  # Bash
    "**/*.zsh",  # Zsh
    "**/*.md",  # Markdown
    "**/*.mdx",  # MDX
    "**/*.txt",  # Plain text
    "**/*.rst",  # reStructuredText
]

EXCLUDED_PATTERNS = [
    "**/.*",  # Hidden directories
    "**/__pycache__",  # Python cache
    "**/node_modules",  # Node.js dependencies
    "**/target",  # Rust/Maven build output
    "**/build/assets",  # Build asserts directories
    "**/dist",  # Distribution directories
    "**/vendor/*.*/*",  # Go vendor directory (domain-based paths)
    "**/.cocoindex_code",  # Our own index directory
]

# Chunking configuration
CHUNK_SIZE = 1000
MIN_CHUNK_SIZE = 300
CHUNK_OVERLAP = 200

# Chunking splitter (stateless, can be module-level)
splitter = RecursiveSplitter()


@coco_aio.function
async def process_chunk(
    file_path: str,
    chunk: Chunk,
    language: str,
    id_gen: IdGenerator,
    table: sqlite.TableTarget,
) -> None:
    """Process a single chunk: embed and store."""
    id, chunk_embedding = await asyncio.gather(
        id_gen.next_id(chunk.text),
        embedder.embed(chunk.text),
    )
    table.declare_row(
        row=CodeChunk(  # type: ignore[arg-type]
            id=id,
            file_path=file_path,
            language=language,
            content=chunk.text,
            start_line=chunk.start.line,
            end_line=chunk.end.line,
            embedding=chunk_embedding,
        )
    )


@coco_aio.function(memo=True)
async def process_file(
    file: localfs.File,
    table: sqlite.TableTarget,
) -> None:
    """Process a single file: chunk, embed, and store."""
    # Read file content
    try:
        content = file.read_text()
    except UnicodeDecodeError:
        # Skip binary files
        return

    if not content.strip():
        return

    # Get relative path and detect language
    language = detect_code_language(filename=file.file_path.path.name) or "text"

    # Split into chunks
    chunks = splitter.split(
        content,
        chunk_size=CHUNK_SIZE,
        min_chunk_size=MIN_CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        language=language,
    )

    id_gen = IdGenerator()
    await asyncio.gather(
        *(
            process_chunk(str(file.file_path.path), chunk, language, id_gen, table)
            for chunk in chunks
        )
    )


@coco_aio.function
async def app_main() -> None:
    """Main indexing function - walks files and processes each."""
    db = coco_aio.use_context(SQLITE_DB)

    # Declare the table target for storing embeddings
    table = await coco_aio.mount_run(
        coco_aio.component_subpath("setup", "table"),
        db.declare_table_target,
        table_name="code_chunks",
        table_schema=await sqlite.TableSchema.from_class(
            CodeChunk,
            primary_key=["id"],
        ),
    ).result()

    # Walk source directory
    files = localfs.walk_dir(
        config.codebase_root_path,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=INCLUDED_PATTERNS,
            excluded_patterns=EXCLUDED_PATTERNS,
        ),
    )

    # Process each file
    for f in files:
        coco_aio.mount(
            coco_aio.component_subpath("process", str(f.file_path.path)),
            process_file,
            f,
            table,
        )


# Create the app
app = coco_aio.App(
    coco_aio.AppConfig(name="CocoIndexCode"),
    app_main,
)
