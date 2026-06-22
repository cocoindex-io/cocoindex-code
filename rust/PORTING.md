# Rust port of cocoindex-code

A from-scratch Rust reimplementation of `cocoindex-code` (the `ccc` CLI), built
on the **CocoIndex Rust SDK** (`cocoindex-io/cocoindex` → `rust/sdk/cocoindex`).
Feature parity with the Python implementation in `../src/cocoindex_code`, which
is kept in the repo as the reference spec.

## Build & run

```bash
cd rust
cargo build       # builds everything (fastembed/ONNX is always on — it's the only embedder)
cargo test        # sqlite-vec (vec0) integration test

./target/debug/ccc init
./target/debug/ccc index
./target/debug/ccc search "vector similarity" --lang rust --limit 10
```

The SDK is a **path dependency** assuming `cocoindex` is checked out as a
sibling (`../../cocoindex`). For distribution this should become a git
dependency on `cocoindex-io/cocoindex` (the `v1` branch).

## Architecture

Like the Python tool, the CLI is a thin **client** that talks to a background
**daemon** over a Unix socket; the daemon keeps the embedding model warm and
caches per-project state. `index`/`search`/`status`/`doctor` are daemon-backed
and auto-spawn the daemon on first use.

- **IPC**: length-prefixed msgpack frames over `daemon.sock` (Rust-to-Rust; not
  wire-compatible with the Python daemon's `multiprocessing.connection`).
- **Embeddings**: **local sentence-transformers (fastembed) only.** Python also
  offers a `litellm` provider for cloud/multi-provider embeddings; there is no
  viable in-process Rust equivalent (the official `LiteLLM-Labs/litellm-rust` is
  a gateway binary, not a library; the community `litellm-rust` crate is alpha
  and only covers OpenAI-compatible embeddings), so the litellm option is
  intentionally not exposed. Existing `provider: litellm` configs parse fine and
  produce a clear error pointing at the local provider.
  - Default model: `BAAI/bge-small-en-v1.5` (Python's
    `Snowflake/snowflake-arctic-embed-xs` isn't in fastembed's registry).
  - Models are limited to fastembed's supported set (resolved by name, then by
    suffix — so `sentence-transformers/all-MiniLM-L6-v2` works).

## Python → Rust module map

| Python (`src/cocoindex_code`) | Rust (`rust/src`) | Status |
|---|---|---|
| `schema.py` / `CodeChunk` | `schema.rs` | ✅ |
| `settings.py` | `settings.rs` | ✅ (container path-mapping env vars deferred) |
| `embedder_params.py` + `embedder_defaults.py` | `embedder_params.rs` | ✅ |
| `litellm_embedder.py` / `shared.create_embedder` | `embedder.rs` | ✅ (local `prompt_name` TODO) |
| `indexer.py` | `indexer.rs` + `walk.rs` | ✅ (nested `.gitignore`, custom chunkers TODO) |
| `query.py` | `query.rs` | ✅ |
| `project.py` | `project.rs` + `daemon.rs` (registry) | ✅ |
| `protocol.py` | `protocol.rs` | ✅ |
| `_daemon_paths.py` | `daemon_paths.rs` | ✅ |
| `daemon.py` | `daemon.rs` | ✅ |
| `client.py` | `client.rs` | ✅ |
| `server.py` (MCP) | `mcp.rs` | ✅ (hand-rolled stdio JSON-RPC) |
| `cli.py` | `main.rs` | ✅ (interactive `init` prompts → flags) |

## CLI commands (parity)

`init`, `index`, `search` (`--lang`/`--path`/`--offset`/`--limit`/`--refresh`),
`status`, `reset` (`--all`/`-f`), `doctor` (`-v`), `mcp`, `daemon status|restart|stop`,
and the hidden `run-daemon`.

## Tested (all green)

- `cargo test`: sqlite-vec `vec0` extension loads + KNN returns correct results.
- **End-to-end (local embeddings)**: `init` → `index` (walk → tree-sitter chunk
  → embed → vec0 upsert) → `search` with `--lang`/`--path` filters → incremental
  re-index correctly skips unchanged files.
- **Daemon-backed lifecycle**: `index` auto-spawns the daemon (loads model once),
  `daemon status`/`restart`/`stop`, graceful shutdown, PID/socket cleanup.
- **MCP**: `initialize` / `tools/list` / `tools/call search` over stdio JSON-RPC.
- **doctor** (global settings, daemon, model checks, project settings, file walk,
  index status), **reset --all**, and post-reset "not initialized" handling.

## Backward compatibility

- **Settings files** (`global_settings.yml`, project `settings.yml`) written by
  the Python tool parse unchanged — same keys, `provider` default (`litellm`),
  `indexing_params`/`query_params` (absent vs empty), `envs`, and the legacy
  `sbert/` model-name prefix (stripped before loading).
- **`provider: litellm`** configs do not crash — they load and return a clear
  "only local embeddings are supported; set `provider: sentence-transformers`"
  error (surfaced through the daemon).
- **Index DB**: the `target_sqlite.db` vec0 schema is identical, so `search`
  works against a Python-built index. The CocoIndex state db (`cocoindex.db`)
  differs across engine builds, so the first `index` re-runs (safe/incremental).
- **`.cocoindex_code/` layout**, paths, and the `.gitignore` entry match Python.

## Parity audit (module-by-module) — fixed

A deep Python-vs-Rust audit drove these fixes (all tested): search/status now
**auto-start load-time indexing and wait** (`ensure_indexing_started`); include/
exclude use the SDK's `PatternFilePathMatcher` for **exact** pattern parity, with
a gitignore-aware wrapper; `init` restores the **"already initialized"** message
and the **parent-marker warning** (`-f` to override); `reset --all` removes the
`.gitignore` entry and prints the settings hint; `doctor` regained the
**daemon-env section**, include/exclude pattern values, the `params:` line, the
traceback hint, and the log line; the client gained **supervised-mode**
(`COCOINDEX_CODE_DAEMON_SUPERVISED`), handshake-warning dedup, and PID-guarded
cleanup; settings gained the empty-file check and absolutized project-root walk;
the MCP tool descriptions match `server.py`.

## Known deltas vs Python (intentional / follow-up)

1. **Embeddings** — local fastembed only; the `litellm` provider is not exposed
   (no viable in-process Rust litellm). Default model differs (see above).
2. **Interactive `init`** — flag-driven (`--model`) instead of questionary prompts.
3. **Custom chunkers** — Python loads `module:callable` chunkers; Rust can't load
   Python callables (config still parses; built-in splitter used).
4. **Legacy `cocoindex-code` entrypoint** + env-var migration
   (`COCOINDEX_CODE_EMBEDDING_MODEL`, …) — not ported (the `ccc` CLI is the
   entry point).
5. local-embedding `prompt_name`, container path-mapping env vars, and live
   index-progress streaming (`IndexProgressUpdate`) — follow-ups.
