# ccc — semantic code search (Rust)

A lightweight, AST-aware semantic code search engine (the `ccc` CLI) built on the
**CocoIndex Rust SDK**. It walks a codebase, chunks each file with tree-sitter,
embeds the chunks locally, and stores them in a sqlite-vec (`vec0`) table for
fast vector search — from the CLI or over MCP.

## Build & run

```bash
cd rust
cargo build       # fastembed/ONNX is always on — local embeddings are the only backend
cargo test        # sqlite-vec (vec0) integration test

./target/debug/ccc init
./target/debug/ccc index
./target/debug/ccc search "vector similarity" --lang rust --limit 10
```

The SDK is a **path dependency** assuming `cocoindex` is checked out as a sibling
(`../../cocoindex`). For distribution this should become a git dependency on
`cocoindex-io/cocoindex` (the `v1` branch).

## Architecture

The CLI is a thin **client** that talks to a background **daemon** over a Unix
socket; the daemon keeps the embedding model warm and caches per-project state.
`index` / `search` / `status` / `doctor` are daemon-backed and auto-spawn the
daemon on first use.

- **IPC**: length-prefixed msgpack frames over `daemon.sock`.
- **Embeddings**: local sentence-transformers via **fastembed** (ONNX). Default
  model `BAAI/bge-small-en-v1.5`; any model in fastembed's registry works
  (resolved by name, then by suffix, so `sentence-transformers/all-MiniLM-L6-v2`
  resolves).
- **Storage**: a sqlite-vec (`vec0`) virtual table, partitioned by `language`.

## How it uses the CocoIndex Rust SDK

This is the canonical worked example of driving the SDK from Rust. The snippets
below mirror the live source — `rust/src/indexer.rs` is the whole flow in ~230
lines — so treat the cited `file:line` anchors as the source of truth and update
this section whenever those change.

**Crate + features** (`Cargo.toml`): one path/git dependency, feature-gated to
exactly what the tool needs.

```toml
cocoindex = { features = ["text", "sqlite", "fastembed", "fs_live"] }
#   text      -> RecursiveSplitter + detect_code_language (tree-sitter)
#   sqlite    -> sqlite-vec (vec0) table target
#   fastembed -> local sentence-transformers embeddings
#   fs_live   -> live directory watching (daemon)
```

`use cocoindex::prelude::*;` pulls in `Ctx`, `Error`/`Result`, `FileEntry`,
`IdGenerator`, `walk_dir`, and the `mount_each!` macro.

**1. Environment → App → run** — the entry point (`indexer.rs:206`). The
`Environment` owns the incremental-state DB and the dependency-injected
resources; `app.run` executes one declarative pass and returns `RunStats`.

```rust
let app = cocoindex::Environment::builder()
    .db_path(coco_db_path)                 // engine's change-tracking state DB
    .provide_key(&DB, db)                  // inject resources by ContextKey
    .provide_key(&EMBEDDER, embedder.clone())
    .build().await?
    .app("CocoIndexCode").await?;
let stats: RunStats = app.run(move |ctx| app_main(ctx, /* … */)).await?;
```

**2. Context keys = typed DI + change detection** (`indexer.rs:30`). `ContextKey`
values are fetched inside a flow with `ctx.get_key(&KEY)?`. `new_with_state`
attaches a state-id, so changing the underlying resource (e.g. the embedding
model) invalidates everything memoized against it.

```rust
static EMBEDDER: LazyLock<ContextKey<CodeEmbedder>> =
    LazyLock::new(|| ContextKey::new_with_state("embedder", |e| e.state_key()));
```

**3. Memoized functions** — `#[cocoindex::function]` (`indexer.rs:48`). The
arguments are part of the memo fingerprint: an unchanged `(file, model_tag)` is
skipped on the next run. (We thread the embedder's identity through `model_tag`
precisely so a model swap reprocesses every file.)

```rust
#[cocoindex::function]
async fn process_file(ctx: &Ctx, file: FileEntry, model_tag: String)
    -> Result<Vec<CodeChunk>> { /* chunk + embed */ }
```

**4. Sources + fan-out** (`indexer.rs:169`). `walk_dir(...).path_matcher(...)`
yields `(key, FileEntry)` items (`file.key()`, `file.content_str()`);
`mount_each!` mounts the memoized fn once per item.

```rust
let files = walk_dir(root).recursive(true).path_matcher(matcher).items()?;
let rows_by_file =
    mount_each!(files, |file| process_file(ctx, file, model_tag.clone())).await?;
```

**5. Targets = declarative sync** (`indexer.rs:152`). You *declare* the desired
rows; the engine diffs against the previous run and applies the minimal
insert/update/delete. Rows are plain `Serialize` structs (`schema.rs::CodeChunk`).

```rust
let table = sqlite::mount_table_target_with_options(&ctx, &DB, TABLE_NAME, schema, opts).await?;
for row in &rows { table.declare_row(&ctx, row)?; }
```

Schema is built with `TableSchema::new([(name, ColumnDef::new(ty)), …], [pk])`;
sqlite-vec virtual tables via `Vec0TableDef { partition_key_columns, auxiliary_columns }`.

**6. sqlite-vec gotcha** (`db.rs`). The SDK's `sqlite::Database::connect` does
*not* load the `vec0` extension. The tool registers it as a SQLite
auto-extension once, builds its own pool, and hands it to
`sqlite::Database::from_pool(state_id, pool)` — the supported way to use the SDK
sqlite target with `vec0` virtual tables.

**7. Building blocks used from the SDK:** `ops::text::{RecursiveSplitter,
RecursiveChunkConfig, detect_code_language}` for chunking/language detection;
`IdGenerator::new()` + `id_gen.next_id(ctx, &code)` for stable chunk ids;
`RunStats` for the run summary; `Error::engine(..)` to wrap foreign errors into
the SDK error type.

## CLI commands

`init`, `index`, `search` (`--lang` / `--path` / `--offset` / `--limit` / `--refresh`),
`status`, `reset` (`--all` / `-f`), `doctor` (`-v`), `mcp`,
`daemon status|restart|stop`, and the hidden `run-daemon`.

## Configuration

Settings live in `~/.cocoindex_code/global_settings.yml` (embedding model,
provider, indexing/query params) and a per-project `.cocoindex_code/settings.yml`
(include/exclude patterns, language overrides). Include/exclude use the SDK's
`PatternFilePathMatcher`, wrapped to also honor nested `.gitignore` files.
`ccc doctor` prints the resolved configuration and where each value came from.

## Testing

- `cargo test` — the sqlite-vec `vec0` extension loads and KNN returns correct
  results.
- `tests/e2e_cli.sh` / `tests/e2e_advanced.sh` — end-to-end coverage of
  `init` → `index` → `search` (with `--lang`/`--path` filters and incremental
  re-index), daemon lifecycle (auto-spawn, restart, stop, graceful shutdown),
  multi-project serving, model-swap re-index, MCP (`initialize` / `tools/list` /
  `tools/call`), `doctor`, and `reset --all`.

## Limitations / follow-ups

- **Embeddings**: local fastembed only — no cloud / multi-provider backend yet.
- **`init`** is flag-driven (`--model`) rather than interactive prompts.
- **Custom chunkers**: the built-in tree-sitter recursive splitter is used;
  pluggable chunkers are not yet supported.
- **Live index-progress streaming** and container path-mapping env vars are
  follow-ups.
