# Configurable Batch Size & Backwards-Compatible Defaults Design

**Date:** 2026-02-25

## Goal

Make `max_batch_size` configurable via env var, restore the documented default embedding model (`all-MiniLM-L6-v2`), and refactor the `config` singleton to live in `config.py` for clean cross-module access.

## Context

The current PR (`my_fixes`) introduced `nomic-ai/CodeRankEmbed` as the default embedding model and hardcoded `max_batch_size=16` in `LocalEmbedder`. Both decisions break backwards compatibility:

- Changing the default from `sbert/sentence-transformers/all-MiniLM-L6-v2` (documented in README) to `CodeRankEmbed` (768-dim vs 384-dim) silently invalidates existing user indexes.
- `max_batch_size=16` is appropriate for 8192-token GPU models but unnecessarily conservative for lightweight models on CPU.

CodeRankEmbed remains valuable for users with capable GPUs — it just shouldn't be the default.

## Architecture

### Config singleton relocation

Currently `config = Config.from_env()` is created in `shared.py`. This prevents other modules (e.g. `embedder.py`) from accessing it without circular imports.

**Change:** Move the singleton to the bottom of `config.py`:

```python
# config.py (bottom)
config: Config = Config.from_env()
```

All other modules that need the config import it from `.config`:

```python
from .config import config
```

This gives `embedder.py` clean access to `config.batch_size` at class-definition time, which is required because `@coco_aio.function(max_batch_size=...)` is a class-level decorator evaluated when the module is first imported.

### Config changes

Add `batch_size: int` to the `Config` dataclass, read from `COCOINDEX_CODE_BATCH_SIZE` (default: `16`).

Revert `_DEFAULT_MODEL` to `sbert/sentence-transformers/all-MiniLM-L6-v2`.

### embedder.py changes

`embedder.py` imports `config` from `.config` and passes `config.batch_size` to both `embed()` and `embed_query()` decorators:

```python
from .config import config

class LocalEmbedder:
    @coco_aio.function(batching=True, runner=coco.GPU, memo=True, max_batch_size=config.batch_size)
    def embed(self, texts: list[str]) -> list[NDArray[np.float32]]:
        ...

    @coco_aio.function(batching=True, runner=coco.GPU, memo=True, max_batch_size=config.batch_size)
    def embed_query(self, texts: list[str]) -> list[NDArray[np.float32]]:
        ...
```

### shared.py changes

Remove `config = Config.from_env()` (now lives in `config.py`). Import `config` instead:

```python
from .config import config
```

### README changes

- Add `COCOINDEX_CODE_BATCH_SIZE` to the configuration table (default: `16`)
- Add a "GPU-optimised local model" section showing CodeRankEmbed as an opt-in example
- Keep documented default as `sbert/sentence-transformers/all-MiniLM-L6-v2`

## New environment variable

| Variable | Description | Default |
|---|---|---|
| `COCOINDEX_CODE_BATCH_SIZE` | Max batch size for local embedding model | `16` |

## Backwards compatibility

- Default model unchanged (`all-MiniLM-L6-v2`) → existing indexes continue to work
- `COCOINDEX_CODE_BATCH_SIZE` is opt-in; default of `16` is safe for all model sizes
- No changes to public MCP tool interface
- `shared.py` and `query.py` external behaviour identical

## Testing

- `test_config.py`: add test that `Config.from_env()` reads `COCOINDEX_CODE_BATCH_SIZE` and defaults to `16`
- `test_config.py`: revert `test_default_model_is_coderank` → `test_default_model_is_miniLM`
- `test_embedder.py`: verify `LocalEmbedder` still works (existing tests cover this)

## Files touched

| File | Change |
|---|---|
| `src/cocoindex_code/config.py` | Add `batch_size` field; revert default model; add `config` singleton at bottom |
| `src/cocoindex_code/shared.py` | Remove `config = Config.from_env()`; import `config` from `.config` |
| `src/cocoindex_code/embedder.py` | Import `config` from `.config`; use `config.batch_size` in decorators |
| `tests/test_config.py` | Add batch size test; revert default model test |
| `README.md` | Add `COCOINDEX_CODE_BATCH_SIZE` to config table; add CodeRankEmbed opt-in example |
