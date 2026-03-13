"""CocoIndex app for indexing codebases."""

import cocoindex as coco
from cocoindex.connectors import localfs, sqlite
from cocoindex.connectors.sqlite import Vec0TableDef
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.chunk import Chunk
from cocoindex.resources.file import PatternFilePathMatcher
from cocoindex.resources.id import IdGenerator

from .config import config
from .shared import CODEBASE_DIR, SQLITE_DB, CodeChunk, embedder

# File patterns for supported languages
DEFAULT_INCLUDED_PATTERNS = [
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
    "**/*.php",  # PHP
    "**/*.lua",  # Lua
]

INCLUDED_PATTERNS = DEFAULT_INCLUDED_PATTERNS + [f"**/*{ext}" for ext in config.extra_extensions]

# Language overrides from extra_extensions (e.g. ".inc" -> "php")
LANGUAGE_OVERRIDES: dict[str, str] = {
    ext: lang for ext, lang in config.extra_extensions.items() if lang is not None
}

DEFAULT_EXCLUDED_PATTERNS = [
    "**/.*",  # Hidden directories
    "**/__pycache__",  # Python cache
    "**/node_modules",  # Node.js dependencies
    "**/target",  # Rust/Maven build output
    "**/build/assets",  # Build asserts directories
    "**/dist",  # Distribution directories
    "**/vendor/*.*/*",  # Go vendor directory (domain-based paths)
    "**/vendor/*",  # PHP vendor directory
    "**/.cocoindex_code",  # Our own index directory
]

EXCLUDED_PATTERNS = DEFAULT_EXCLUDED_PATTERNS + config.excluded_patterns

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
    # Read file content
    try:
        content = await file.read_text()
    except UnicodeDecodeError:
        # Skip binary files
        return

    if not content.strip():
        return

    # Get relative path and detect language
    suffix = file.file_path.path.suffix
    language = (
        LANGUAGE_OVERRIDES.get(suffix)
        or detect_code_language(filename=file.file_path.path.name)
        or "text"
    )

    # Split into chunks
    chunks = splitter.split(
        content,
        chunk_size=CHUNK_SIZE,
        min_chunk_size=MIN_CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        language=language,
    )

    id_gen = IdGenerator()

    async def process(
        chunk: Chunk,
    ) -> None:
        table.declare_row(
            row=CodeChunk(
                id=await id_gen.next_id(chunk.text),
                file_path=str(file.file_path.path),
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
    db = coco.use_context(SQLITE_DB)

    # Declare the table target for storing embeddings
    table = await db.mount_table_target(
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

    # Walk source directory
    files = localfs.walk_dir(
        coco.use_context(CODEBASE_DIR),
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=INCLUDED_PATTERNS,
            excluded_patterns=EXCLUDED_PATTERNS,
        ),
    )

    # Process each file
    with coco.component_subpath(coco.Symbol("process_file")):
        await coco.mount_each(process_file, files.items(), table)
