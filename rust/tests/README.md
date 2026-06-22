# Rust port E2E tests

Shell-driven end-to-end tests for the `ccc` binary (the Rust port).

```bash
cargo build --manifest-path rust/Cargo.toml
bash rust/tests/e2e_cli.sh        # core CLI: init/index/search/status/daemon/doctor/mcp/reset
bash rust/tests/e2e_advanced.sh   # custom config, multi-project, model swap, MCP args, real codebases
```

Each script prints `PASSED: N  FAILED: M` and exits non-zero on failure. Set
`CCC_BIN` to test a different binary (e.g. a release build). These use a local
fastembed model on first run (small download).

Unit tests (sqlite-vec, embedder params/legacy-bridge) run via `cargo test`.

## Parity notes encoded in these tests
- `search` with no index **auto-builds** it (matches Python's daemon).
- A binary/invalid-UTF-8 file with a code extension is **indexed lossily**
  (matches Python's `read_text(errors="replace")`); empty/whitespace files are skipped.
