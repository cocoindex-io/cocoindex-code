<p align="center">
<img width="2428" alt="cocoindex code" src="https://github.com/user-attachments/assets/d05961b4-0b7b-42ea-834a-59c3c01717ca" />
</p>


<h1 align="center">AST-based semantic code search that just works</h1>

![effect](https://github.com/user-attachments/assets/cb3a4cae-0e1f-49c4-890b-7bb93317ab60)


A lightweight, effective **(AST-based)** semantic code search tool for your codebase. Built on [CocoIndex](https://github.com/cocoindex-io/cocoindex) — a Rust-based ultra performant data transformation engine. Use it from the CLI, or integrate with Claude, Codex, Cursor — any coding agent — via [Skill](#skill-recommended) or [MCP](#mcp-server).

- Instant token saving by 70%.
- **1 min setup** — install and go, zero config needed!

<div align="center">

[![Discord](https://img.shields.io/discord/1314801574169673738?logo=discord&color=5B5BD6&logoColor=white)](https://discord.com/invite/zpA9S2DR7s)
[![GitHub](https://img.shields.io/github/stars/cocoindex-io/cocoindex?color=5B5BD6)](https://github.com/cocoindex-io/cocoindex)
[![Documentation](https://img.shields.io/badge/Documentation-394e79?logo=readthedocs&logoColor=00B9FF)](https://cocoindex.io/docs/getting_started/quickstart)
[![License](https://img.shields.io/badge/license-Apache%202.0-5B5BD6?logoColor=white)](https://opensource.org/licenses/Apache-2.0)
<!--[![PyPI - Downloads](https://img.shields.io/pypi/dm/cocoindex)](https://pypistats.org/packages/cocoindex) -->
[![PyPI Downloads](https://static.pepy.tech/badge/cocoindex/month)](https://pepy.tech/projects/cocoindex)
[![CI](https://github.com/cocoindex-io/cocoindex/actions/workflows/CI.yml/badge.svg?event=push&color=5B5BD6)](https://github.com/cocoindex-io/cocoindex/actions/workflows/CI.yml)
[![release](https://github.com/cocoindex-io/cocoindex/actions/workflows/release.yml/badge.svg?event=push&color=5B5BD6)](https://github.com/cocoindex-io/cocoindex/actions/workflows/release.yml)


🌟 Please help star [CocoIndex](https://github.com/cocoindex-io/cocoindex) if you like this project!

[Deutsch](https://readme-i18n.com/cocoindex-io/cocoindex-code?lang=de) |
[English](https://readme-i18n.com/cocoindex-io/cocoindex-code?lang=en) |
[Español](https://readme-i18n.com/cocoindex-io/cocoindex-code?lang=es) |
[français](https://readme-i18n.com/cocoindex-io/cocoindex-code?lang=fr) |
[日本語](https://readme-i18n.com/cocoindex-io/cocoindex-code?lang=ja) |
[한국어](https://readme-i18n.com/cocoindex-io/cocoindex-code?lang=ko) |
[Português](https://readme-i18n.com/cocoindex-io/cocoindex-code?lang=pt) |
[Русский](https://readme-i18n.com/cocoindex-io/cocoindex-code?lang=ru) |
[中文](https://readme-i18n.com/cocoindex-io/cocoindex-code?lang=zh)

</div>


## Get Started — zero config, let's go!

### Install

Using [pipx](https://pipx.pypa.io/stable/installation/):
```bash
pipx install cocoindex-code       # first install
pipx upgrade cocoindex-code       # upgrade
```

Using [uv](https://docs.astral.sh/uv/getting-started/installation/):
```bash
uv tool install --upgrade cocoindex-code --prerelease explicit --with "cocoindex>=1.0.0a24"
```

The default embedding model runs locally ([sentence-transformers/all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)) — no API key required, completely free.

Next, set up your [coding agent integration](#coding-agent-integration) — or jump to [Manual CLI Usage](#manual-cli-usage) if you prefer direct control.

## Coding Agent Integration

### Skill (Recommended)

Install the `ccc` skill so your coding agent automatically uses semantic search when needed:

```bash
npx skills add cocoindex-io/cocoindex-code
```

That's it — no `ccc init` or `ccc index` needed. The skill teaches the agent to handle initialization, indexing, and searching on its own. It will automatically keep the index up to date as you work.

The agent uses semantic search automatically when it would be helpful. You can also nudge it explicitly — just ask it to search the codebase, e.g. *"find how user sessions are managed"*, or type `/ccc` to invoke the skill directly.

Works with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and other skill-compatible agents.

### MCP Server

Alternatively, use `ccc mcp` to run as an MCP server:

<details>
<summary>Claude Code</summary>

```bash
claude mcp add cocoindex-code -- ccc mcp
```
</details>

<details>
<summary>Codex</summary>

```bash
codex mcp add cocoindex-code -- ccc mcp
```
</details>

<details>
<summary>OpenCode</summary>

```bash
opencode mcp add
```
Enter MCP server name: `cocoindex-code`
Select MCP server type: `local`
Enter command to run: `ccc mcp`

Or use opencode.json:
```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "cocoindex-code": {
      "type": "local",
      "command": [
        "ccc", "mcp"
      ]
    }
  }
}
```
</details>

Once configured, the agent automatically decides when semantic code search is helpful — finding code by description, exploring unfamiliar codebases, fuzzy/conceptual matches, or locating implementations without knowing exact names.

> **Note:** The `cocoindex-code` command (without subcommand) still works as an MCP server for backward compatibility. It auto-creates settings from environment variables on first run.

<details>
<summary>MCP Tool Reference</summary>

When running as an MCP server (`ccc mcp`), the following tool is exposed:

**`search`** — Search the codebase using semantic similarity.

```
search(
    query: str,                          # Natural language query or code snippet
    limit: int = 5,                      # Maximum results (1-100)
    offset: int = 0,                     # Pagination offset
    refresh_index: bool = True,          # Refresh index before querying
    languages: list[str] | None = None,  # Filter by language (e.g. ["python", "typescript"])
    paths: list[str] | None = None,      # Filter by path glob (e.g. ["src/utils/*"])
)
```

Returns matching code chunks with file path, language, code content, line numbers, and similarity score.
</details>

## Manual CLI Usage

You can also use the CLI directly — useful for manual control, running indexing after changing settings, checking status, or searching outside an agent.

```bash
ccc init                                # initialize project (creates settings)
ccc index                               # build the index
ccc search "authentication logic"       # search!
```

The background daemon starts automatically on first use.

> **Tip:** `ccc index` auto-initializes if you haven't run `ccc init` yet, so you can skip straight to indexing.

### CLI Reference

| Command | Description |
|---------|-------------|
| `ccc init` | Initialize a project — creates settings files, adds `.cocoindex_code/` to `.gitignore` |
| `ccc index` | Build or update the index (auto-inits if needed). Shows streaming progress. |
| `ccc search <query>` | Semantic search across the codebase |
| `ccc status` | Show index stats (chunk count, file count, language breakdown) |
| `ccc mcp` | Run as MCP server in stdio mode |
| `ccc doctor` | Run diagnostics — checks settings, daemon, model, file matching, and index health |
| `ccc reset` | Delete index databases. `--all` also removes settings. `-f` skips confirmation. |
| `ccc daemon status` | Show daemon version, uptime, and loaded projects |
| `ccc daemon restart` | Restart the background daemon |
| `ccc daemon stop` | Stop the daemon |

### Search Options

```bash
ccc search database schema                           # basic search
ccc search --lang python --lang markdown schema      # filter by language
ccc search --path 'src/utils/*' query handler        # filter by path
ccc search --offset 10 --limit 5 database schema     # pagination
ccc search --refresh database schema                 # update index first, then search
```

By default, `ccc search` scopes results to your current working directory (relative to the project root). Use `--path` to override.

## Features
- **Semantic Code Search**: Find relevant code using natural language queries when grep doesn't work well, and save tokens immediately.
- **Ultra Performant**: ⚡ Built on top of ultra performant [Rust indexing engine](https://github.com/cocoindex-io/cocoindex). Only re-indexes changed files for fast updates.
- **Multi-Language Support**: Python, JavaScript/TypeScript, Rust, Go, Java, C/C++, C#, SQL, Shell, and more.
- **Embedded**: Portable and just works, no database setup required!
- **Flexible Embeddings**: Local SentenceTransformers by default (free!) or 100+ cloud providers via LiteLLM.

## Configuration

Configuration lives in two YAML files, both created automatically by `ccc init`.

### User Settings (`~/.cocoindex_code/global_settings.yml`)

Shared across all projects. Controls the embedding model and environment variables for the daemon.

```yaml
embedding:
  provider: sentence-transformers                    # or "litellm"
  model: sentence-transformers/all-MiniLM-L6-v2
  device: mps                                        # optional: cpu, cuda, mps (auto-detected if omitted)

envs:                                                # extra environment variables for the daemon
  OPENAI_API_KEY: your-key                           # only needed if not already in your shell environment
```

> **Note:** The daemon inherits your shell environment. If an API key (e.g. `OPENAI_API_KEY`) is already set as an environment variable, you don't need to duplicate it in `envs`. The `envs` field is only for values that aren't in your environment.

### Project Settings (`<project>/.cocoindex_code/settings.yml`)

Per-project. Controls which files to index.

```yaml
include_patterns:
  - "**/*.py"
  - "**/*.js"
  - "**/*.ts"
  - "**/*.rs"
  - "**/*.go"
  # ... (sensible defaults for 28+ file types)

exclude_patterns:
  - "**/.*"                # hidden directories
  - "**/__pycache__"
  - "**/node_modules"
  - "**/dist"
  # ...

language_overrides:
  - ext: inc               # treat .inc files as PHP
    lang: php
```

> `.cocoindex_code/` is automatically added to `.gitignore` during init.

## Embedding Models

By default, a local SentenceTransformers model ([sentence-transformers/all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)) is used — no API key required. To use a different model, edit `~/.cocoindex_code/global_settings.yml`.

> The `envs` entries below are only needed if the key isn't already in your shell environment — the daemon inherits your environment automatically.

<details>
<summary>Ollama (Local)</summary>

```yaml
embedding:
  model: ollama/nomic-embed-text
```

Set `OLLAMA_API_BASE` in `envs:` if your Ollama server is not at `http://localhost:11434`.

</details>

<details>
<summary>OpenAI</summary>

```yaml
embedding:
  model: text-embedding-3-small
envs:
  OPENAI_API_KEY: your-api-key
```

</details>

<details>
<summary>Azure OpenAI</summary>

```yaml
embedding:
  model: azure/your-deployment-name
envs:
  AZURE_API_KEY: your-api-key
  AZURE_API_BASE: https://your-resource.openai.azure.com
  AZURE_API_VERSION: "2024-06-01"
```

</details>

<details>
<summary>Gemini</summary>

```yaml
embedding:
  model: gemini/gemini-embedding-001
envs:
  GEMINI_API_KEY: your-api-key
```

</details>

<details>
<summary>Mistral</summary>

```yaml
embedding:
  model: mistral/mistral-embed
envs:
  MISTRAL_API_KEY: your-api-key
```

</details>

<details>
<summary>Voyage (Code-Optimized)</summary>

```yaml
embedding:
  model: voyage/voyage-code-3
envs:
  VOYAGE_API_KEY: your-api-key
```

</details>

<details>
<summary>Cohere</summary>

```yaml
embedding:
  model: cohere/embed-v4.0
envs:
  COHERE_API_KEY: your-api-key
```

</details>

<details>
<summary>AWS Bedrock</summary>

```yaml
embedding:
  model: bedrock/amazon.titan-embed-text-v2:0
envs:
  AWS_ACCESS_KEY_ID: your-access-key
  AWS_SECRET_ACCESS_KEY: your-secret-key
  AWS_REGION_NAME: us-east-1
```

</details>

<details>
<summary>Nebius</summary>

```yaml
embedding:
  model: nebius/BAAI/bge-en-icl
envs:
  NEBIUS_API_KEY: your-api-key
```

</details>

Any [LiteLLM-supported model](https://docs.litellm.ai/docs/embedding/supported_embedding) works. When using a LiteLLM model, set `provider: litellm` (or omit `provider` — LiteLLM is the default for non-`sentence-transformers` models).

### Local SentenceTransformers Models

Set `provider: sentence-transformers` and use any [SentenceTransformers](https://www.sbert.net/) model (no API key required).

**Example — general purpose text model:**
```yaml
embedding:
  provider: sentence-transformers
  model: nomic-ai/nomic-embed-text-v1.5
```

**GPU-optimised code retrieval:**

[`nomic-ai/CodeRankEmbed`](https://huggingface.co/nomic-ai/CodeRankEmbed) delivers significantly better code retrieval than the default model. It is 137M parameters, requires ~1 GB VRAM, and has an 8192-token context window.

```yaml
embedding:
  provider: sentence-transformers
  model: nomic-ai/CodeRankEmbed
```

**Note:** Switching models requires re-indexing your codebase (`ccc reset && ccc index`) since the vector dimensions differ.

## Supported Languages

| Language | Aliases | File Extensions |
|----------|---------|-----------------|
| c | | `.c` |
| cpp | c++ | `.cpp`, `.cc`, `.cxx`, `.h`, `.hpp` |
| csharp | csharp, cs | `.cs` |
| css | | `.css`, `.scss` |
| dtd | | `.dtd` |
| fortran | f, f90, f95, f03 | `.f`, `.f90`, `.f95`, `.f03` |
| go | golang | `.go` |
| html | | `.html`, `.htm` |
| java | | `.java` |
| javascript | js | `.js` |
| json | | `.json` |
| kotlin | | `.kt`, `.kts` |
| lua | | `.lua` |
| markdown | md | `.md`, `.mdx` |
| pascal | pas, dpr, delphi | `.pas`, `.dpr` |
| php | | `.php` |
| python | | `.py` |
| r | | `.r` |
| ruby | | `.rb` |
| rust | rs | `.rs` |
| scala | | `.scala` |
| solidity | | `.sol` |
| sql | | `.sql` |
| swift | | `.swift` |
| toml | | `.toml` |
| tsx | | `.tsx` |
| typescript | ts | `.ts` |
| xml | | `.xml` |
| yaml | | `.yaml`, `.yml` |

### Custom Database Location

By default, index databases (`cocoindex.db` and `target_sqlite.db`) live alongside settings in `<project>/.cocoindex_code/`. When running in Docker, you may want the databases on the container's native filesystem for performance (LMDB doesn't work well on mounted volumes) while keeping the source code and settings on a mounted volume.

Set `COCOINDEX_CODE_DB_PATH_MAPPING` to remap database locations by path prefix:

```bash
COCOINDEX_CODE_DB_PATH_MAPPING=/workspace=/db-files
```

With this mapping, a project at `/workspace/myrepo` stores its databases in `/db-files/myrepo/` instead of `/workspace/myrepo/.cocoindex_code/`. Settings files remain in the original location.

Multiple mappings are comma-separated and resolved in order (first match wins):

```bash
COCOINDEX_CODE_DB_PATH_MAPPING=/workspace=/db-files,/workspace2=/db-files2
```

Both source and target must be absolute paths. If no mapping matches, the default location is used.

## Troubleshooting

Run `ccc doctor` to diagnose common issues. It checks your settings, daemon health, embedding model, file matching, and index status — all in one command.

### `sqlite3.Connection object has no attribute enable_load_extension`

Some Python installations (e.g. the one pre-installed on macOS) ship with a SQLite library that doesn't enable extensions.

**macOS fix:** Install Python through [Homebrew](https://brew.sh/):

```bash
brew install python3
```

Then re-install cocoindex-code (see [Get Started](#get-started--zero-config-lets-go) for install options):

Using pipx:
```bash
pipx install cocoindex-code       # first install
pipx upgrade cocoindex-code       # upgrade
```

Using uv (install or upgrade):
```bash
uv tool install --upgrade cocoindex-code --prerelease explicit --with "cocoindex>=1.0.0a24"
```

## Legacy: Environment Variables

If you previously configured `cocoindex-code` via environment variables, the `cocoindex-code` MCP command still reads them and auto-migrates to YAML settings on first run. We recommend switching to the YAML settings for new setups.

| Environment Variable | YAML Equivalent |
|---------------------|-----------------|
| `COCOINDEX_CODE_EMBEDDING_MODEL` | `embedding.model` in `global_settings.yml` |
| `COCOINDEX_CODE_DEVICE` | `embedding.device` in `global_settings.yml` |
| `COCOINDEX_CODE_ROOT_PATH` | Run `ccc init` in your project root instead |
| `COCOINDEX_CODE_EXCLUDED_PATTERNS` | `exclude_patterns` in project `settings.yml` |
| `COCOINDEX_CODE_EXTRA_EXTENSIONS` | `include_patterns` + `language_overrides` in project `settings.yml` |

## Large codebase / Enterprise
[CocoIndex](https://github.com/cocoindex-io/cocoindex) is an ultra efficient indexing engine that also works on large codebases at scale for enterprises. In enterprise scenarios it is a lot more efficient to share indexes with teammates when there are large or many repos. We also have advanced features like branch dedupe etc designed for enterprise users.

If you need help with remote setup, please email our maintainer linghua@cocoindex.io, happy to help!

## Contributing

We welcome contributions! Before you start, please install the [pre-commit](https://pre-commit.com/) hooks so that linting, formatting, type checking, and tests run automatically before each commit:

```bash
pip install pre-commit
pre-commit install
```

This catches common issues — trailing whitespace, lint errors (Ruff), type errors (mypy), and test failures — before they reach CI.

For more details, see our [contributing guide](https://cocoindex.io/docs/contributing/guide).

## License

Apache-2.0
