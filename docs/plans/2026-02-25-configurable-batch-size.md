# Configurable Batch Size & Backwards-Compatible Defaults Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expose `COCOINDEX_CODE_BATCH_SIZE` as a configurable env var, move the `config` singleton into `config.py` for clean cross-module access, and revert the default embedding model to the documented `all-MiniLM-L6-v2`.

**Architecture:** Add `batch_size: int` to the `Config` dataclass and instantiate the singleton at the bottom of `config.py`. `embedder.py` imports `config` directly from `.config` to read `config.batch_size` at class-definition time (required because the CocoIndex decorator is evaluated once when the module loads). `shared.py` drops its own `Config.from_env()` call and imports the singleton instead.

**Tech Stack:** Python 3.11, CocoIndex, pytest, ruff, mypy strict.

---

### Task 1: Add `batch_size` to Config and revert the default model

This task is TDD: write the tests first, then update `config.py` to pass them.

**Files:**
- Modify: `tests/test_config.py`
- Modify: `src/cocoindex_code/config.py`

**Step 1: Write the failing tests**

Open `tests/test_config.py`.

Rename the existing test `test_default_model_is_coderank` (line 74) to `test_default_model_is_minilm` and update the assertion:

```python
def test_default_model_is_minilm(self, tmp_path: Path) -> None:
    with patch.dict(
        os.environ,
        {"COCOINDEX_CODE_ROOT_PATH": str(tmp_path)},
    ):
        os.environ.pop("COCOINDEX_CODE_EMBEDDING_MODEL", None)
        config = Config.from_env()
        assert "all-MiniLM-L6-v2" in config.embedding_model
```

Then add a new test class at the bottom of the file:

```python
class TestConfigBatchSize:
    """Tests for COCOINDEX_CODE_BATCH_SIZE env var."""

    def test_default_batch_size_is_16(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {"COCOINDEX_CODE_ROOT_PATH": str(tmp_path)},
        ):
            os.environ.pop("COCOINDEX_CODE_BATCH_SIZE", None)
            config = Config.from_env()
            assert config.batch_size == 16

    def test_batch_size_reads_env_var(self, tmp_path: Path) -> None:
        with patch.dict(
            os.environ,
            {
                "COCOINDEX_CODE_ROOT_PATH": str(tmp_path),
                "COCOINDEX_CODE_BATCH_SIZE": "32",
            },
        ):
            config = Config.from_env()
            assert config.batch_size == 32
```

**Step 2: Run the tests to confirm they fail**

```bash
cd /home/marius/work/claude/cocoindex-code
uv run pytest tests/test_config.py -v -k "minilm or batch_size" 2>&1 | tail -20
```

Expected: 3 FAILs — `test_default_model_is_minilm` (assertion fails, model is CodeRankEmbed), `test_default_batch_size_is_16` (AttributeError: Config has no `batch_size`), `test_batch_size_reads_env_var` (same).

**Step 3: Update `config.py`**

Open `src/cocoindex_code/config.py`.

**3a.** On line 11, revert the default model:

```python
_DEFAULT_MODEL = "sbert/sentence-transformers/all-MiniLM-L6-v2"
```

**3b.** In the `Config` dataclass (starts at line 59), add `batch_size` as the last field:

```python
@dataclass
class Config:
    """Configuration loaded from environment variables."""

    codebase_root_path: Path
    embedding_model: str
    index_dir: Path
    device: str
    trust_remote_code: bool
    batch_size: int
```

**3c.** In `Config.from_env()`, read the new env var. Add this block just before the `return cls(...)` call:

```python
        # Batch size for local embedding model
        batch_size = int(os.environ.get("COCOINDEX_CODE_BATCH_SIZE", "16"))
```

**3d.** Add `batch_size=batch_size` to the `return cls(...)` call:

```python
        return cls(
            codebase_root_path=root,
            embedding_model=embedding_model,
            index_dir=index_dir,
            device=device,
            trust_remote_code=trust_remote_code,
            batch_size=batch_size,
        )
```

**3e.** At the very bottom of `config.py`, after all class definitions, add the singleton:

```python
# Module-level singleton — imported by shared.py and embedder.py
config: Config = Config.from_env()
```

**Step 4: Run the tests to confirm they pass**

```bash
uv run pytest tests/test_config.py -v 2>&1 | tail -20
```

Expected: All tests PASS.

**Step 5: Commit**

```bash
git add tests/test_config.py src/cocoindex_code/config.py
git commit -m "feat: add batch_size to Config and revert default model to all-MiniLM-L6-v2"
```

---

### Task 2: Update `shared.py` to import the config singleton

`shared.py` currently creates its own `config = Config.from_env()`. Now that the singleton lives in `config.py`, remove the local creation.

**Files:**
- Modify: `src/cocoindex_code/shared.py`

There are no unit tests for `shared.py` module-level imports — the change is structural only. Verify with the full test suite after.

**Step 1: Edit `shared.py`**

Open `src/cocoindex_code/shared.py`.

**1a.** Replace line 19:

```python
# Before
from .config import Config
```

```python
# After
from .config import config, Config
```

Wait — check whether `Config` is still used elsewhere in `shared.py` after this change. It is not (only `config` fields are referenced). So the import becomes:

```python
from .config import config
```

**1b.** Delete line 26:

```python
config = Config.from_env()
```

**Step 2: Run the full test suite**

```bash
uv run pytest -v 2>&1 | tail -30
```

Expected: All tests PASS (the module still works; `config` is the same object, just imported).

**Step 3: Commit**

```bash
git add src/cocoindex_code/shared.py
git commit -m "refactor: import config singleton from config.py instead of creating in shared.py"
```

---

### Task 3: Update `embedder.py` to use `config.batch_size`

The `@coco_aio.function(max_batch_size=16)` decorator is evaluated at class-definition time. By importing `config` from `.config` at the top of `embedder.py`, `config.batch_size` is available when Python defines the `LocalEmbedder` class.

**Files:**
- Modify: `src/cocoindex_code/embedder.py`

No new unit tests needed — the existing `TestLocalEmbedderInit` tests already exercise `embed` and `embed_query`. The correctness of the batch size is an integration concern. However, we verify with the full test suite.

**Step 1: Edit `embedder.py`**

Open `src/cocoindex_code/embedder.py`.

**1a.** Add the config import. After the existing imports block (after the `if TYPE_CHECKING:` block), add:

```python
from .config import config as _config
```

Use the alias `_config` to avoid shadowing any local variable named `config`.

**1b.** Replace the `max_batch_size=16` on the `embed` decorator (line 75):

```python
# Before
@coco_aio.function(batching=True, runner=coco.GPU, memo=True, max_batch_size=16)
def embed(self, texts: list[str]) -> list[NDArray[np.float32]]:

# After
@coco_aio.function(batching=True, runner=coco.GPU, memo=True, max_batch_size=_config.batch_size)
def embed(self, texts: list[str]) -> list[NDArray[np.float32]]:
```

**1c.** Replace the `max_batch_size=16` on the `embed_query` decorator (line 86):

```python
# Before
@coco_aio.function(batching=True, runner=coco.GPU, memo=True, max_batch_size=16)
def embed_query(self, texts: list[str]) -> list[NDArray[np.float32]]:

# After
@coco_aio.function(batching=True, runner=coco.GPU, memo=True, max_batch_size=_config.batch_size)
def embed_query(self, texts: list[str]) -> list[NDArray[np.float32]]:
```

**Step 2: Run the full test suite**

```bash
uv run pytest -v 2>&1 | tail -30
```

Expected: All tests PASS.

**Step 3: Run pre-commit checks**

```bash
uv run pre-commit run --all-files 2>&1 | tail -30
```

Expected: All hooks pass. If ruff-format makes whitespace changes, stage and amend or include in next commit.

**Step 4: Commit**

```bash
git add src/cocoindex_code/embedder.py
git commit -m "feat: use config.batch_size in LocalEmbedder decorators (COCOINDEX_CODE_BATCH_SIZE)"
```

---

### Task 4: Update the README

Document the new env var and add CodeRankEmbed as a recommended opt-in for GPU users.

**Files:**
- Modify: `README.md`

**Step 1: Update the configuration table**

Find the configuration table (around line 99–101):

```markdown
| Variable | Description | Default |
|----------|-------------|---------|
| `COCOINDEX_CODE_ROOT_PATH` | Root path of the codebase | Auto-discovered (see below) |
| `COCOINDEX_CODE_EMBEDDING_MODEL` | Embedding model (see below) | `sbert/sentence-transformers/all-MiniLM-L6-v2` |
```

Add the new row:

```markdown
| Variable | Description | Default |
|----------|-------------|---------|
| `COCOINDEX_CODE_ROOT_PATH` | Root path of the codebase | Auto-discovered (see below) |
| `COCOINDEX_CODE_EMBEDDING_MODEL` | Embedding model (see below) | `sbert/sentence-transformers/all-MiniLM-L6-v2` |
| `COCOINDEX_CODE_BATCH_SIZE` | Max batch size for local embedding model | `16` |
```

**Step 2: Add a CodeRankEmbed example under the Embedding model section**

Find the end of the "Embedding model" section (after all the cloud provider examples, before the next `##` heading). Add a new subsection before the LiteLLM full list note:

```markdown
### GPU-optimised local model (recommended for best code search quality)

If you have a GPU, [`nomic-ai/CodeRankEmbed`](https://huggingface.co/nomic-ai/CodeRankEmbed) gives significantly better code retrieval than the default model. It is 137M parameters, requires ~1 GB VRAM, and has an 8192-token context window.

```bash
claude mcp add cocoindex-code \
  -- uvx --prerelease=explicit --with "cocoindex>=1.0.0a18" cocoindex-code@latest \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=sbert/nomic-ai/CodeRankEmbed \
  -e COCOINDEX_CODE_BATCH_SIZE=16
```

> **Note:** Switching models requires re-indexing your codebase (the vector dimensions differ).
```

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add COCOINDEX_CODE_BATCH_SIZE to config table and CodeRankEmbed as GPU opt-in"
```

---

### Verification

After all tasks, run the full suite one final time and push:

```bash
uv run pytest -v
uv run pre-commit run --all-files
git push fork my_fixes
```

Expected: 20+ tests pass, all pre-commit hooks pass, PR updated.
