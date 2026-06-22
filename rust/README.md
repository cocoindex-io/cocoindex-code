# cocoindex-code (Rust) — AST-based semantic code search

A lightweight, effective **(AST-based)** semantic code search tool for your
codebase — the native-Rust build of [`ccc`](https://github.com/cocoindex-io/cocoindex-code).
Built on [CocoIndex](https://github.com/cocoindex-io/cocoindex), the Rust data
transformation engine. Use it from the CLI, or wire it into Claude Code, Codex,
Cursor — any coding agent — via [Skill](#coding-agent-integration) or
[MCP](#mcp-server).

- Instant token savings — let the agent find code by meaning, not grep.
- **Local embeddings, zero setup** — runs fully offline, no API key required.
- **Incremental** — only re-indexes changed files.

## Features

- **Semantic code search** — find relevant code with natural-language queries
  when grep falls short.
- **Ultra performant** — a single static binary on top of the Rust
  [CocoIndex](https://github.com/cocoindex-io/cocoindex) engine; only changed
  files are re-indexed.
- **Multi-language** — Python, JavaScript/TypeScript, Rust, Go, Java, C/C++, C#,
  SQL, Shell, and more (tree-sitter).
- **Embedded** — a sqlite-vec index file; no database to run.
- **Local embeddings** — sentence-transformers via [fastembed](https://github.com/Anush008/fastembed-rs)
  (ONNX), no API key, no Python.

## Install

The Rust build is compiled from source. It depends on the CocoIndex SDK as a
sibling checkout, so clone both repos side by side:

```bash
git clone https://github.com/cocoindex-io/cocoindex
git clone -b rust https://github.com/cocoindex-io/cocoindex-code

cd cocoindex-code/rust
cargo build --release

# put the binary on your PATH (or use `cargo install --path .`)
install -m 0755 target/release/ccc ~/.local/bin/ccc
ccc --help
```

Embeddings are **local-only** (fastembed/ONNX) — no cloud provider or API key is
required or supported in this build. The default model is
[`BAAI/bge-small-en-v1.5`](https://huggingface.co/BAAI/bge-small-en-v1.5); any
model in fastembed's registry can be selected (see [Configuration](#configuration)).

## Quick start

```bash
ccc init                                # initialize project (creates settings)
ccc index                               # build the index
ccc search "authentication logic"       # search!
```

The background daemon starts automatically on first use and keeps the embedding
model warm.

> **Tip:** `ccc index` auto-initializes if you haven't run `ccc init` yet, so you
> can skip straight to indexing.

## Coding Agent Integration

### Skill

Install the `ccc` skill so your coding agent automatically uses semantic search
when it helps:

```bash
npx skills add cocoindex-io/cocoindex-code
```

The skill teaches the agent to initialize, index, and search on its own, and to
keep the index fresh as you work. Ask it to search the codebase — e.g. *"find how
user sessions are managed"* — or invoke it directly with `/ccc`. Requires the
`ccc` binary on your `PATH` (see [Install](#install)).

### MCP Server

Alternatively, run `ccc` as an MCP server over stdio:

```bash
# Claude Code
claude mcp add cocoindex-code -- ccc mcp

# Codex
codex mcp add cocoindex-code -- ccc mcp
```

Once configured, the agent decides when semantic search is helpful — finding code
by description, exploring unfamiliar code, or locating implementations without
knowing exact names.

<details>
<summary>MCP Tool Reference</summary>

Running as an MCP server (`ccc mcp`) exposes one tool:

**`search`** — search the codebase by semantic similarity.

```
search(
    query: str,                          # natural-language query or code snippet
    limit: int = 5,                      # max results (1–100)
    offset: int = 0,                     # pagination offset
    refresh_index: bool = True,          # refresh the index before querying
    languages: list[str] | None = None,  # filter by language, e.g. ["python","rust"]
    paths: list[str] | None = None,      # filter by path glob, e.g. ["src/utils/*"]
)
```

Returns matching chunks with file path, language, code, line numbers, and a
similarity score.
</details>

## CLI Reference

| Command | Description |
|---------|-------------|
| `ccc init` | Initialize a project — creates settings files, adds `.cocoindex_code/` to `.gitignore` |
| `ccc index` | Build or update the index (auto-inits if needed) |
| `ccc search <query>` | Semantic search across the codebase |
| `ccc status` | Show index stats (chunk count, file count, language breakdown) |
| `ccc mcp` | Run as an MCP server in stdio mode |
| `ccc doctor` | Run diagnostics — settings, daemon, model, file matching, index health (`-v` for detail) |
| `ccc reset` | Delete index databases. `--all` also removes settings. `-f` skips confirmation. |
| `ccc daemon status` | Show daemon version, uptime, and loaded projects |
| `ccc daemon restart` | Restart the background daemon |
| `ccc daemon stop` | Stop the daemon |

### Search options

```bash
ccc search database schema                           # basic search
ccc search --lang python --lang markdown schema      # filter by language
ccc search --path 'src/utils/*' query handler        # filter by path glob
ccc search --offset 10 --limit 5 database schema     # pagination
ccc search --refresh database schema                 # update index first, then search
```

By default `ccc search` scopes results to your current working directory
(relative to the project root). Use `--path` to override.

## Configuration

Configuration lives in two YAML files, both created by `ccc init`.

### User settings (`~/.cocoindex_code/global_settings.yml`)

Shared across all projects — controls the embedding model.

```yaml
embedding:
  provider: sentence-transformers          # local fastembed (the only supported provider)
  model: BAAI/bge-small-en-v1.5            # any model in fastembed's registry

  # Optional asymmetric-retrieval knobs, applied separately to indexing vs query.
  # Accepted key: prompt_name (sentence-transformers).
  # indexing_params:
  #   prompt_name: passage
  # query_params:
  #   prompt_name: query
```

> Set `COCOINDEX_CODE_DIR` to place `global_settings.yml` somewhere other than
> `~/.cocoindex_code/`.

Models are resolved against fastembed's registry by name, then by suffix — so
`sentence-transformers/all-MiniLM-L6-v2` resolves. Cloud / LiteLLM providers are
not part of this build; a `provider: litellm` config loads but fails with a clear
message pointing at the local provider.

### Project settings (`<project>/.cocoindex_code/settings.yml`)

Per-project — controls which files are indexed.

```yaml
include_patterns:
  - "**/*.py"
  - "**/*.ts"
  - "**/*.rs"
  - "**/*.go"
  # ... sensible defaults for 28+ file types

exclude_patterns:
  - "**/.*"               # hidden directories
  - "**/node_modules"
  - "**/dist"
  # ...

language_overrides:
  - ext: inc              # treat .inc files as PHP
    lang: php
```

Include/exclude globs additionally honor nested `.gitignore` files.
`.cocoindex_code/` is added to `.gitignore` during `init`.

## Supported languages

Tree-sitter–based chunking for Python, JavaScript/TypeScript, Rust, Go, Java,
C/C++, C#, Ruby, PHP, Swift, Kotlin, Scala, SQL, Shell, Markdown, and more.
Unrecognized text files are indexed with a generic recursive splitter.

## Differences from the Python build

This native build targets feature parity with the Python `ccc` for day-to-day
use; two things differ today:

- **Embeddings are local-only** (fastembed). There is no LiteLLM / cloud-provider
  option, and the default model is `BAAI/bge-small-en-v1.5`.
- **Custom Python chunkers** (`chunkers:` in project settings) are not supported —
  the config still parses, but the built-in tree-sitter splitter is used.

Index databases are interchangeable: `ccc search` works against an index built by
the Python tool, and vice versa.
