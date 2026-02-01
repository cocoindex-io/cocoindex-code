"""CocoIndex app for indexing codebases."""

import asyncio

import cocoindex as coco
from cocoindex.connectors import localfs, sqlite
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.chunk import Chunk
from cocoindex.resources.file import PatternFilePathMatcher
from cocoindex.resources.id import IdGenerator

from .shared import SQLITE_DB, CodeChunk, config, embedder

# File patterns for supported languages
INCLUDED_PATTERNS = [
    "*.py",  # Python
    "*.pyi",  # Python stubs
    "*.js",  # JavaScript
    "*.jsx",  # JavaScript React
    "*.ts",  # TypeScript
    "*.tsx",  # TypeScript React
    "*.mjs",  # JavaScript ES modules
    "*.cjs",  # JavaScript CommonJS
    "*.rs",  # Rust
    "*.go",  # Go
]

EXCLUDED_PATTERNS = [
    ".*/**",  # Hidden directories
    "**/__pycache__/**",  # Python cache
    "**/node_modules/**",  # Node.js dependencies
    "**/target/**",  # Rust/Maven build output
    "**/dist/**",  # Distribution directories
    "**/build/**",  # Build directories
    "**/vendor/**",  # Go vendor directory
    "**/.git/**",  # Git directory
    "**/.cocoindex_code/**",  # Our own index directory
    "*.min.js",  # Minified JavaScript
    "*.min.css",  # Minified CSS
    "*.lock",  # Lock files
    "**/package-lock.json",  # NPM lock
    "**/yarn.lock",  # Yarn lock
    "**/Cargo.lock",  # Cargo lock
    "**/go.sum",  # Go sum
    "**/*.pyc",  # Python bytecode
    "**/*.pyo",  # Python optimized bytecode
    "**/*.so",  # Shared objects
    "**/*.dylib",  # macOS dynamic libraries
    "**/*.dll",  # Windows dynamic libraries
]

# Chunking configuration
CHUNK_SIZE = 1000
MIN_CHUNK_SIZE = 300
CHUNK_OVERLAP = 200

# Chunking splitter (stateless, can be module-level)
splitter = RecursiveSplitter()


@coco.function(memo=True)
async def process_chunk(
    id: int, file_path: str, chunk: Chunk, language: str, table: sqlite.TableTarget
) -> None:
    """Process a single chunk: embed and store."""
    chunk_embedding = await embedder.embed_async(chunk.text)
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


@coco.function(memo=True)
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
            process_chunk(
                id_gen.next_id(chunk.text), str(file.file_path.path), chunk, language, table
            )
            for chunk in chunks
        )
    )


@coco.function
def app_main() -> None:
    """Main indexing function - walks files and processes each."""
    db = coco.use_context(SQLITE_DB)

    # Declare the table target for storing embeddings
    table = coco.mount_run(
        coco.component_subpath("setup", "table"),
        db.declare_table_target,
        table_name="code_chunks",
        table_schema=sqlite.TableSchema(
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
        coco.mount(
            coco.component_subpath("process", str(f.file_path.path)),
            process_file,
            f,
            table,
        )


# Create the app
app = coco.App(
    coco.AppConfig(name="CocoIndexCode"),
    app_main,
)
