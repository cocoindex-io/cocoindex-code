# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This is an AST-based semantic code search tool built on top of [CocoIndex v1](https://cocoindex.io/docs-v1/llms.txt).


## Build and Test Commands

This project uses [uv](https://docs.astral.sh/uv/) for project management.

```bash
uv sync                                # Install all dev dependencies
uv run pytest tests/                   # Run all tests
uv run pytest tests/test_settings.py  # Run a single test file
uv run pytest tests/ -k test_name     # Run tests matching a name
uv run mypy .                          # Type check Python code
uv run ruff check .                    # Lint
uv run ruff format .                   # Format
uv run prek run --all-files            # Full CI suite (lint, format, mypy, pytest)
```

Docker E2E tests are excluded by default (need `pytest -m docker_e2e` to run them).


## Architecture

### Process Model

The tool runs as a **long-lived background daemon** (`daemon.py`) that accepts connections over a Unix socket (Windows: named pipe). The `ccc` CLI and MCP server communicate with it via short-lived **per-request connections** (`client.py`): connect → handshake → send one request → receive response(s) → close.

The daemon is auto-started by `client.py` when not running. It is restarted automatically when the `global_settings.yml` mtime changes (version bump or settings edit), detected via the `HandshakeResponse.global_settings_mtime_us` field.

### Key Layers

**`protocol.py`** — All IPC message types as `msgspec.Struct` tagged unions, serialized as msgpack. Every request type, response type, and streaming wrapper lives here. The `Request` and `Response` type aliases are the union types used by the decoder.

**`daemon.py`** — The daemon entry point (`run_daemon()`). Holds a `ProjectRegistry` that lazily creates and caches `Project` instances keyed by project root. Each connection is handled by `handle_connection()` running as an asyncio task. Dispatches to project operations or daemon-level operations (`_dispatch()`).

**`project.py`** — `Project` wraps a CocoIndex `Environment` + `App`. Manages an `asyncio.Lock` so only one indexing run is active at a time, and an `asyncio.Event` so search can wait for the initial index pass.

**`indexer.py`** — Defines the CocoIndex app (`process_file`): reads files from LocalFS, detects language, applies custom chunkers or the default `RecursiveSplitter`, embeds chunks, and stores them in SQLite via `sqlite-vec`. This is the `@coco.fn(memo=True)` function that CocoIndex tracks for incremental re-indexing.

**`shared.py`** — CocoIndex `ContextKey` definitions (`EMBEDDER`, `SQLITE_DB`, `CODEBASE_DIR`, `INDEXING_EMBED_PARAMS`, `QUERY_EMBED_PARAMS`), the `CodeChunk` dataclass (SQLite schema), and the `create_embedder()` factory.

**`settings.py`** — YAML config loading for both global (`~/.cocoindex_code/global_settings.yml`) and per-project (`.cocoindex_code/settings.yml`) settings. Also provides all path helpers (`cocoindex_db_path`, `target_sqlite_db_path`, `resolve_db_dir`, etc.) and host-path-mapping logic for Docker.

**`server.py`** — FastMCP wrapper that delegates `search` calls to the daemon client. Also contains the legacy `main()` entry point (`cocoindex-code` command) for backward-compatible env-var-based configuration.

**`cli.py`** — Typer app with `ccc` subcommands (`init`, `index`, `search`, `grep`, `status`, `reset`, `doctor`, `mcp`, `daemon status/restart/stop`). CLI logic only — delegates to `client.py` or `grep.py`.

**`grep.py`** — Structural code search (`ccc grep`) using CocoIndex's `code_match`. Runs entirely locally with no daemon or index. Matches in parallel, streams results per file.

**`chunking.py`** — Public `Chunk` type and the `CHUNKER_REGISTRY` context key. Custom chunkers in `settings.yml` are resolved at daemon startup and injected via this context.

### Embedding Model Flow

The embedder is created once at daemon startup (`create_embedder()` in `shared.py`) and stored in the `ProjectRegistry`. Two param dicts (`indexing_params`, `query_params`) are threaded through CocoIndex context keys so asymmetric models (Cohere, Voyage, nomic-embed) can use different kwargs for document vs. query embedding.

Two install flavors: `[full]` bundles `sentence-transformers` (local inference); slim uses only LiteLLM (cloud APIs). The `PacedLiteLLMEmbedder` in `litellm_embedder.py` adds rate-limiting between requests.

### Settings and Project Discovery

`find_project_root()` walks up from CWD looking for `.cocoindex_code/settings.yml`. Global settings at `~/.cocoindex_code/global_settings.yml` (location overridable via `COCOINDEX_CODE_DIR`). The `COCOINDEX_CODE_DB_PATH_MAPPING` env var redirects database files to a different directory (used in Docker to avoid LMDB on bind mounts).


## Code Conventions

### Internal vs External Modules

We distinguish between **internal modules** (under packages with `_` prefix, e.g. `_internal.*` or `connectors.*._source`) and **external modules** (which users can directly import).

**External modules** (user-facing, e.g. `cocoindex/ops/sentence_transformers.py`):

* Be strict about not leaking implementation details
* Use `__all__` to explicitly list public exports
* Prefix ALL non-public symbols with `_`, including:
  * Standard library imports: `import threading as _threading`, `import typing as _typing`
  * Third-party imports: `import numpy as _np`, `from numpy.typing import NDArray as _NDArray`
  * Internal package imports: `from cocoindex.resources import schema as _schema`
* Exception: `TYPE_CHECKING` imports for type hints don't need prefixing

**Internal modules** (e.g. `cocoindex/_internal/component_ctx.py`):

* Less strict since users shouldn't import these directly
* Standard library and internal imports don't need underscore prefix
* Only prefix symbols that are truly private to the module itself (e.g. `_context_var` for a module-private ContextVar)

### General principles

- **Top-level imports.** Defer to in-function only for a real circular dependency or a heavy import that isn't always needed.
- **Specific types over `Any`.** When a value enters as a weaker form (`str`, `Any`), convert to the strong type at the earliest point. Don't propagate the weak form.
- **`NamedTuple`/small dataclass for multi-value returns.** Access fields by name at call sites.
- **Single source of truth.** When the same value or logic appears in multiple places, consolidate it.
- **Delete dead code and dead config.** When a change makes something unreachable, the code, the tests, and the knobs all go.
- **Honest names.** The name describes what the code does today.

### Testing Guidelines

We prefer end-to-end tests on user-facing APIs, over unit tests on smaller internal functions. With this said, there're cases where unit tests are necessary, e.g. for internal logic with various situations and edge cases, in which case it's usually easier to cover various scenarios with unit tests.

When tests fail, fix the underlying issue. Don't skip, ignore, or exclude to get a green result.
