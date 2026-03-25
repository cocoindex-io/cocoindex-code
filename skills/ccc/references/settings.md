# ccc Settings

Configuration lives in two YAML files, both created automatically by `ccc init`.

## User-Level Settings (`~/.cocoindex_code/global_settings.yml`)

Shared across all projects. Controls the embedding model and extra environment variables for the daemon.

```yaml
embedding:
  provider: sentence-transformers   # or "litellm" (default when provider is omitted)
  model: sentence-transformers/all-MiniLM-L6-v2
  device: mps                       # optional: cpu, cuda, mps (auto-detected if omitted)
  min_interval_ms: 300              # optional: pace LiteLLM embedding requests to reduce 429s; defaults to 5 for LiteLLM

envs:                               # extra environment variables for the daemon
  OPENAI_API_KEY: your-key          # only needed if not already in the shell environment
```

### Fields

| Field | Description |
|-------|-------------|
| `embedding.provider` | `sentence-transformers` for local models, `litellm` (or omit) for cloud/remote models |
| `embedding.model` | Model identifier — format depends on provider (see examples below) |
| `embedding.device` | Optional. `cpu`, `cuda`, or `mps`. Auto-detected if omitted. Only relevant for `sentence-transformers`. |
| `embedding.min_interval_ms` | Optional. Minimum delay between LiteLLM embedding requests in milliseconds. Defaults to `5` for LiteLLM and is ignored by `sentence-transformers`. Set explicitly to override the default. |
| `envs` | Key-value map of environment variables injected into the daemon. Use for API keys not already in the shell environment. |

### Embedding Model Examples

**Local (sentence-transformers, no API key needed):**

```yaml
embedding:
  provider: sentence-transformers
  model: sentence-transformers/all-MiniLM-L6-v2    # default, lightweight
```

```yaml
embedding:
  provider: sentence-transformers
  model: nomic-ai/CodeRankEmbed                     # better code retrieval, needs GPU (~1 GB VRAM)
```

**Ollama (local):**

```yaml
embedding:
  model: ollama/nomic-embed-text
```

**OpenAI:**

```yaml
embedding:
  model: text-embedding-3-small
  min_interval_ms: 300
envs:
  OPENAI_API_KEY: your-api-key
```

**Gemini:**

```yaml
embedding:
  model: gemini/gemini-embedding-001
envs:
  GEMINI_API_KEY: your-api-key
```

**Voyage (code-optimized):**

```yaml
embedding:
  model: voyage/voyage-code-3
envs:
  VOYAGE_API_KEY: your-api-key
```

For the full list of supported cloud providers and model identifiers, see [LiteLLM Embedding Models](https://docs.litellm.ai/docs/embedding/supported_embedding).

### Important

Switching embedding models changes vector dimensions — you must re-index after changing the model:

```bash
ccc reset && ccc index
```

## Project-Level Settings (`<project>/.cocoindex_code/settings.yml`)

Per-project. Controls which files to index. Created by `ccc init` and automatically added to `.gitignore`.

```yaml
include_patterns:
  - "**/*.py"
  - "**/*.js"
  - "**/*.ts"
  # ... (sensible defaults for 28+ file types)

exclude_patterns:
  - "**/.*"              # hidden directories
  - "**/__pycache__"
  - "**/node_modules"
  - "**/dist"
  # ...

language_overrides:
  - ext: inc             # treat .inc files as PHP
    lang: php
```

### Fields

| Field | Description |
|-------|-------------|
| `include_patterns` | Glob patterns for files to index. Defaults cover common languages (Python, JS/TS, Rust, Go, Java, C/C++, C#, SQL, Shell, Markdown, PHP, Lua, etc.). |
| `exclude_patterns` | Glob patterns for files/directories to skip. Defaults exclude hidden dirs, `node_modules`, `dist`, `__pycache__`, `vendor`, etc. |
| `language_overrides` | List of `{ext, lang}` pairs to override language detection for specific file extensions. |

### Editing Tips

- To index additional file types, append glob patterns to `include_patterns` (e.g. `"**/*.proto"`).
- To exclude a directory, append to `exclude_patterns` (e.g. `"**/generated"`).
- After editing, run `ccc index` to re-index with the new settings.
