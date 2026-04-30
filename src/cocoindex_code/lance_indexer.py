"""
LanceDB indexer — multi-language / multi-repo semantic search index.

Mode A — single language:
  coco-lance <directory> --lang {swift,python,go,rust,javascript} [--output <path>]

Mode B — config-driven multi-language / multi-repo:
  coco-lance --config <coco-config.yml> --output <path>
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any, NamedTuple, TypedDict

import cocoindex as coco
from cocoindex.connectors import lancedb, localfs
from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.chunk import Chunk
from cocoindex.resources.file import FileLike, FilePathMatcher, PatternFilePathMatcher
from cocoindex.resources.id import IdGenerator
from numpy.typing import NDArray
from pathspec import GitIgnoreSpec

from ._matchers import GitignoreAwareMatcher
from .chunking import CHUNKER_REGISTRY, ChunkerFn, resolve_chunker_registry
from .config import load_codebase_config
from .multi_repo import MultiRepoOrchestrator
from .settings import ChunkerMapping

__all__ = ["main"]


class _LangPreset(TypedDict):
    extensions: list[str]
    table: str


LANGUAGE_CONFIG: dict[str, _LangPreset] = {
    "swift":      {"extensions": ["**/*.swift"], "table": "swift_index"},
    "python":     {"extensions": ["**/*.py", "**/*.pyi"], "table": "python_index"},
    "go":         {"extensions": ["**/*.go"], "table": "go_index"},
    "rust":       {"extensions": ["**/*.rs"], "table": "rust_index"},
    "javascript": {
        "extensions": ["**/*.js", "**/*.ts", "**/*.jsx", "**/*.tsx", "**/*.mjs", "**/*.cjs"],
        "table": "typescript_index",
    },
}

EXT_TO_LANGUAGE: dict[str, str] = {
    "py": "python",  "pyi": "python",
    "js": "typescript", "jsx": "typescript",
    "ts": "typescript", "tsx": "typescript",
    "mjs": "typescript", "cjs": "typescript",
    "rs": "rust",
    "go": "go",
    "swift": "swift",
    "kt": "kotlin",  "kts": "kotlin",
    "java": "java",
    "scala": "scala",
    "c": "c", "h": "c", "cpp": "c", "hpp": "c",
    "cc": "c", "cxx": "c", "hxx": "c", "hh": "c",
    "cs": "csharp",
    "rb": "ruby",
    "php": "php",
    "lua": "lua",
    "sh": "shell", "bash": "shell", "zsh": "shell",
    "sql": "sql",
    "prisma": "prisma",
    "md": "markdown", "mdx": "markdown",
    "html": "html", "htm": "html",
    "css": "css", "scss": "css",
    "json": "data", "yaml": "data", "yml": "data", "toml": "data", "xml": "data",
    "r": "r",
    "sol": "solidity",
}

UNIVERSAL_EXCLUDES: list[str] = [
    "**/.*",
    "**/__pycache__/**",
    "**/node_modules/**",
    "**/venv/**", "**/.venv/**",
    "**/dist/**", "**/build/**",
    "**/target/**", "**/.build/**",
    "**/.next/**", "**/out/**", "**/.turbo/**",
    "**/.cache/**",
    "**/*_pb2.py", "**/*_pb2.pyi", "**/*.pb.go", "**/*.pb.ts",
    "**/*.generated.*", "**/*.snapshot.json",
    "**/*.wasm", "**/*.so", "**/*.dylib",
    "**/.repo/**", "**/worktrees/**", "**/.git/**",
    "**/.cocoindex_code/**",
    "**/Pods/**", "**/DerivedData/**", "**/SourcePackages/**", "**/checkouts/**",
    "**/*.egg-info/**", "**/.pytest_cache/**",
]

EMBED_MODEL = "Snowflake/snowflake-arctic-embed-xs"
LANCE_DB = coco.ContextKey[lancedb.LanceAsyncConnection]("coco_lance_db")
EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder]("lance_embedder", detect_change=True)

_splitter = RecursiveSplitter()


@dataclass
class CodeEmbedding:
    id: int
    filename: str
    language: str
    code: str
    embedding: Annotated[NDArray[Any], EMBEDDER]
    start_line: int
    end_line: int


@dataclass(frozen=True)
class LangGroup:
    language: str
    table: str
    included_patterns: tuple[str, ...]
    excluded_patterns: tuple[str, ...]


class _ModeBResult(NamedTuple):
    sourcedir: pathlib.Path
    groups: list[LangGroup]
    chunker_mappings: list[ChunkerMapping]


class _MergedSettings(TypedDict):
    include_patterns: list[str]
    exclude_patterns: list[str]
    language_overrides: list[dict[str, str]]
    chunkers: list[dict[str, str]]


@coco.fn
async def process_chunk(
    chunk: Chunk,
    filename: pathlib.PurePath,
    language: str,
    id_gen: IdGenerator,
    table: lancedb.TableTarget[CodeEmbedding],
) -> None:
    table.declare_row(
        row=CodeEmbedding(
            id=await id_gen.next_id(chunk.text),
            filename=str(filename),
            language=language,
            code=chunk.text,
            embedding=await coco.use_context(EMBEDDER).embed(chunk.text),
            start_line=chunk.start.line,
            end_line=chunk.end.line,
        ),
    )


@coco.fn(memo=True)
async def process_file(
    file: FileLike[Any],
    table: lancedb.TableTarget[CodeEmbedding],
    chunk_size: int,
    chunk_overlap: int,
) -> None:
    text = await file.read_text()
    suffix = file.file_path.path.suffix
    language: str = detect_code_language(filename=str(file.file_path.path.name)) or "text"

    chunker_registry = coco.use_context(CHUNKER_REGISTRY)
    chunker = chunker_registry.get(suffix)
    if chunker is not None:
        language_override, chunks = chunker(pathlib.Path(file.file_path.path), text)
        if language_override is not None:
            language = language_override
    else:
        chunks = _splitter.split(
            text,
            chunk_size=chunk_size,
            min_chunk_size=max(1, chunk_size // 4),
            chunk_overlap=chunk_overlap,
            language=language,
        )
    id_gen = IdGenerator()
    await coco.map(process_chunk, chunks, file.file_path.path, language, id_gen, table)


def _load_root_gitignore(sourcedir: pathlib.Path) -> GitIgnoreSpec | None:
    gitignore_path = sourcedir / ".gitignore"
    if not gitignore_path.is_file():
        return None
    try:
        lines = gitignore_path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    return GitIgnoreSpec.from_lines(lines) if lines else None


@coco.fn
async def app_main(
    sourcedir: pathlib.Path,
    included_patterns: tuple[str, ...],
    excluded_patterns: tuple[str, ...],
    table_name: str,
    chunk_size: int,
    chunk_overlap: int,
) -> None:
    target_table = await lancedb.mount_table_target(
        LANCE_DB,
        table_name=table_name,
        table_schema=await lancedb.TableSchema.from_class(
            CodeEmbedding, primary_key=["id"]
        ),
    )
    base_matcher = PatternFilePathMatcher(
        included_patterns=list(included_patterns),
        excluded_patterns=list(excluded_patterns),
    )
    matcher: FilePathMatcher = GitignoreAwareMatcher(
        base_matcher, _load_root_gitignore(sourcedir), sourcedir
    )
    files = localfs.walk_dir(sourcedir, recursive=True, path_matcher=matcher)
    await coco.mount_each(
        process_file, files.items(), target_table, chunk_size, chunk_overlap
    )


def _ext_from_pattern(pattern: str) -> str | None:
    suffix = pathlib.PurePosixPath(pattern).suffix
    return suffix.lstrip(".") if suffix else None


def _mode_b_groups(
    merged: _MergedSettings,
    extra_lang_overrides: dict[str, str],
) -> list[LangGroup]:
    lang_map = (
        {**EXT_TO_LANGUAGE, **extra_lang_overrides} if extra_lang_overrides else EXT_TO_LANGUAGE
    )

    by_lang: dict[str, list[str]] = {}
    skipped = 0

    for pattern in merged["include_patterns"]:
        ext = _ext_from_pattern(pattern)
        if ext is None:
            continue
        lang = lang_map.get(ext)
        if lang is None:
            skipped += 1
            continue
        by_lang.setdefault(lang, []).append(pattern)

    if skipped:
        print(f"  [warn] {skipped} patterns skipped (unknown extension)", file=sys.stderr)

    excludes = tuple(dict.fromkeys(UNIVERSAL_EXCLUDES + merged["exclude_patterns"]))

    return [
        LangGroup(
            language=lang,
            table=f"{lang}_index",
            included_patterns=tuple(patterns),
            excluded_patterns=excludes,
        )
        for lang, patterns in sorted(by_lang.items())
    ]


def _build_mode_b(config_path: pathlib.Path, output: pathlib.Path) -> _ModeBResult:
    cfg, resolved_path = load_codebase_config(config_path)

    orchestrator = MultiRepoOrchestrator(
        config=cfg,
        config_path=resolved_path,
        unified_root=output.parent / "unified",
        github_cache=output.parent / "gh_cache",
        repo_root_hint=resolved_path.parent,
    )

    print("Syncing repos...")
    orchestrator.sync_and_link_repos()

    raw_merged = orchestrator.merged_settings()
    merged = _MergedSettings(
        include_patterns=raw_merged.get("include_patterns", []),
        exclude_patterns=raw_merged.get("exclude_patterns", []),
        language_overrides=raw_merged.get("language_overrides", []),
        chunkers=raw_merged.get("chunkers", []),
    )

    extra_overrides: dict[str, str] = {
        item["ext"]: item["lang"]
        for item in merged["language_overrides"]
        if "ext" in item and "lang" in item
    }

    chunker_mappings: list[ChunkerMapping] = [
        ChunkerMapping(ext=item["ext"], module=item["module"])
        for item in merged["chunkers"]
        if "ext" in item and "module" in item
    ]

    return _ModeBResult(
        sourcedir=orchestrator.unified_root,
        groups=_mode_b_groups(merged, extra_overrides),
        chunker_mappings=chunker_mappings,
    )


def _run_groups(
    groups: list[LangGroup],
    sourcedir: pathlib.Path,
    output_db: pathlib.Path,
    embed_model: str,
    chunk_size: int,
    chunk_overlap: int,
    chunker_registry: dict[str, ChunkerFn],
) -> None:
    if not groups:
        print("No language groups — nothing to index.", file=sys.stderr)
        return

    print(f"Languages: {', '.join(g.language for g in groups)}")

    @coco.lifespan
    async def lifespan(builder: coco.EnvironmentBuilder) -> AsyncIterator[None]:
        conn = await lancedb.connect_async(str(output_db))
        builder.provide(LANCE_DB, conn)
        builder.provide(EMBEDDER, SentenceTransformerEmbedder(embed_model))
        builder.provide(CHUNKER_REGISTRY, chunker_registry)
        yield

    for group in groups:
        print(f"\n[{group.language}] → {group.table} ({len(group.included_patterns)} patterns)")
        app = coco.App(
            coco.AppConfig(name=f"CocoLance_{group.table}"),
            app_main,
            sourcedir=sourcedir,
            included_patterns=group.included_patterns,
            excluded_patterns=group.excluded_patterns,
            table_name=group.table,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        app.update_blocking(report_to_stdout=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Index a codebase into LanceDB for semantic search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Mode A — single language:
  coco-lance /path/to/ios-app --lang swift
  coco-lance /path/to/backend --lang python --output ~/indices/backend.db

Mode B — multi-language / multi-repo:
  coco-lance --config /path/to/coco-config.yml --output ~/indices/lance
        """,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("directory", nargs="?", type=pathlib.Path, help="(Mode A) root directory")
    mode.add_argument(
        "--config", type=pathlib.Path, metavar="YAML", help="(Mode B) coco-config.yml"
    )

    parser.add_argument("--lang", choices=list(LANGUAGE_CONFIG.keys()), help="(Mode A) language")
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("./lancedb_data"),
        help="LanceDB output path (default: ./lancedb_data)",
    )
    parser.add_argument("--model", default=EMBED_MODEL, help="Embedding model")
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--chunk-overlap", type=int, default=300)

    args = parser.parse_args()

    if args.directory is not None and args.lang is None:
        parser.error("Mode A requires --lang")

    args.output = args.output.expanduser().resolve()
    args.output.mkdir(parents=True, exist_ok=True)

    if not os.environ.get("COCOINDEX_DB"):
        os.environ["COCOINDEX_DB"] = str(args.output.parent / ".coco_state")

    # Extract typed locals — argparse Namespace attributes are untyped.
    config: pathlib.Path | None = args.config
    directory: pathlib.Path | None = args.directory
    lang: str | None = args.lang

    chunker_mappings: list[ChunkerMapping] = []
    if config is not None:
        if not config.exists():
            print(f"Config not found: {config}", file=sys.stderr)
            sys.exit(1)
        result = _build_mode_b(config, args.output)
        sourcedir, groups = result.sourcedir, result.groups
        chunker_mappings = result.chunker_mappings
    else:
        assert directory is not None  # guaranteed: mutually_exclusive_group + required=True
        assert lang is not None       # guaranteed: parser.error() exits if lang is missing
        if not directory.exists():
            print(f"Directory not found: {directory}", file=sys.stderr)
            sys.exit(1)
        preset = LANGUAGE_CONFIG[lang]
        sourcedir = directory
        groups = [LangGroup(
            language=lang,
            table=preset["table"],
            included_patterns=tuple(preset["extensions"]),
            excluded_patterns=tuple(UNIVERSAL_EXCLUDES),
        )]

    try:
        chunker_registry = resolve_chunker_registry(chunker_mappings)
        _run_groups(
            groups=groups,
            sourcedir=sourcedir,
            output_db=args.output,
            embed_model=args.model,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            chunker_registry=chunker_registry,
        )
    except KeyboardInterrupt:
        print("\nIndexing cancelled", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
