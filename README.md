<p align="center">
<img width="2428" alt="cocoindex code" src="https://github.com/user-attachments/assets/d05961b4-0b7b-42ea-834a-59c3c01717ca" />
</p>


<h1 align="center">light weight MCP for code that just works </h1>

![effect](https://github.com/user-attachments/assets/cb3a4cae-0e1f-49c4-890b-7bb93317ab60)




A super light-weight, effective embedded MCP **(AST-based)** that understand and searches your codebase that just works! Using [CocoIndex](https://github.com/cocoindex-io/cocoindex) - an Rust-based ultra performant data transformation engine. No blackbox. Works for Claude, Codex, Cursor - any coding agent.

- Instant token saving by 70%.
- **1 min setup** - Just claude/codex mcp add works!

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


## Get Started - zero config, let's go!!

Requires Python 3 (`pip3` comes pre-installed with Python).

```bash
pip3 install -U cocoindex-code
```

### Claude
```bash
claude mcp add cocoindex-code -- cocoindex-code
```

### Codex
```bash
codex mcp add cocoindex-code -- cocoindex-code
```

### OpenCode
```bash
opencode mcp add
```
Enter MCP server name: `cocoindex-code`
Select MCP server type: `local`
Enter command to run: `cocoindex-code`

Or use opencode.json:
```
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "cocoindex-code": {
      "type": "local",
      "command": [
        "cocoindex-code"
      ]
    }
  }
}
```

Optionally, you can run `cocoindex-code index` to create or update the index. Without running it, the MCP server will automatically build and keep the index up-to-date in the background.

## Features
- **Semantic Code Search**: Find relevant code using natural language queries when grep doesn't work well, and save tokens immediately.
- **Ultra Performant to code changes**:⚡ Built on top of ultra performant [Rust indexing engine](https://github.com/cocoindex-io/cocoindex/edit/main/README.md). Only re-indexes changed files for fast updates.
- **Multi-Language Support**: Python, JavaScript/TypeScript, Rust, Go, Java, C/C++, C#, SQL, Shell
- **Embedded**: Portable and just works, no database setup required!
- **Flexible Embeddings**: By default, no API key required with Local SentenceTransformers - totally free!  You can customize 100+ cloud providers.


## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `COCOINDEX_CODE_ROOT_PATH` | Root path of the codebase | Auto-discovered (see below) |
| `COCOINDEX_CODE_EMBEDDING_MODEL` | Embedding model (see below) | `sbert/sentence-transformers/all-MiniLM-L6-v2` |
| `COCOINDEX_CODE_BATCH_SIZE` | Max batch size for local embedding model | `16` |


### Root Path Discovery

If `COCOINDEX_CODE_ROOT_PATH` is not set, the codebase root is discovered by:

1. Finding the nearest parent directory containing `.cocoindex_code/`
2. Finding the nearest parent directory containing `.git/`
3. Falling back to the current working directory

### Embedding model
By default - this project use a local SentenceTransformers model (`sentence-transformers/all-MiniLM-L6-v2`). No API key required and completely free!

Use a code specific embedding model can achieve better semantic understanding for your results, this project supports all models on Ollama and 100+ cloud providers.

Set `COCOINDEX_CODE_EMBEDDING_MODEL` to any [LiteLLM-supported model](https://docs.litellm.ai/docs/embedding/supported_embedding), along with the provider's API key:

<details>
<summary>Ollama (Local)</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=ollama/nomic-embed-text \
  -- cocoindex-code
```

Set `OLLAMA_API_BASE` if your Ollama server is not at `http://localhost:11434`.

</details>

<details>
<summary>OpenAI</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=text-embedding-3-small \
  -e OPENAI_API_KEY=your-api-key \
  -- cocoindex-code
```

</details>

<details>
<summary>Azure OpenAI</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=azure/your-deployment-name \
  -e AZURE_API_KEY=your-api-key \
  -e AZURE_API_BASE=https://your-resource.openai.azure.com \
  -e AZURE_API_VERSION=2024-06-01 \
  -- cocoindex-code
```

</details>

<details>
<summary>Gemini</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=gemini/text-embedding-004 \
  -e GEMINI_API_KEY=your-api-key \
  -- cocoindex-code
```

</details>

<details>
<summary>Mistral</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=mistral/mistral-embed \
  -e MISTRAL_API_KEY=your-api-key \
  -- cocoindex-code
```

</details>

<details>
<summary>Voyage (Code-Optimized)</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=voyage/voyage-code-3 \
  -e VOYAGE_API_KEY=your-api-key \
  -- cocoindex-code
```

</details>

<details>
<summary>Cohere</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=cohere/embed-english-v3.0 \
  -e COHERE_API_KEY=your-api-key \
  -- cocoindex-code
```

</details>

<details>
<summary>AWS Bedrock</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=bedrock/amazon.titan-embed-text-v2:0 \
  -e AWS_ACCESS_KEY_ID=your-access-key \
  -e AWS_SECRET_ACCESS_KEY=your-secret-key \
  -e AWS_REGION_NAME=us-east-1 \
  -- cocoindex-code
```

</details>

<details>
<summary>Nebius</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=nebius/BAAI/bge-en-icl \
  -e NEBIUS_API_KEY=your-api-key \
  -- cocoindex-code
```

</details>

Any model supported by LiteLLM works — see the [full list of embedding providers](https://docs.litellm.ai/docs/embedding/supported_embedding).

### GPU-optimised local model

If you have a GPU, [`nomic-ai/CodeRankEmbed`](https://huggingface.co/nomic-ai/CodeRankEmbed) delivers significantly better code retrieval than the default model. It is 137M parameters, requires ~1 GB VRAM, and has an 8192-token context window.

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=sbert/nomic-ai/CodeRankEmbed \
  -e COCOINDEX_CODE_BATCH_SIZE=16 \
  -- cocoindex-code
```

> **Note:** Switching models requires re-indexing your codebase (the vector dimensions differ).

## MCP Tools

### `search`

Search the codebase using semantic similarity.

```
search(
    query: str,               # Natural language query or code snippet
    limit: int = 10,          # Maximum results (1-100)
    offset: int = 0,          # Pagination offset
    refresh_index: bool = True  # Refresh index before querying
)
```

The `refresh_index` parameter controls whether the index is refreshed before searching:

- `True` (default): Refreshes the index to include any recent changes
- `False`: Skip refresh for faster consecutive queries

Returns matching code chunks with:

- File path
- Language
- Code content
- Line numbers (start/end)
- Similarity score


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

Common generated directories are automatically excluded:

- `__pycache__/`
- `node_modules/`
- `target/`
- `dist/`
- `vendor/` (Go vendored dependencies, matched by domain-based child paths)

## Troubleshooting

### `sqlite3.Connection object has no attribute enable_load_extension`

Some Python installations (e.g. the one pre-installed on macOS) ship with a SQLite library that doesn't enable extensions.

**macOS fix:** Install Python through [Homebrew](https://brew.sh/):

```bash
brew install python3
```

Then re-install cocoindex-code with the Homebrew Python:

```bash
pip3 install -U cocoindex-code
```

## Large codebase / Enterprise
[CocoIndex](https://github.com/cocoindex-io/cocoindex) is an ultra effecient indexing engine that also works on large codebase at scale on XXX G for enterprises. In enterprise scenarios it is a lot more effecient to do index share with teammates when there are large repo or many repos. We also have advanced features like branch dedupe etc designed for enterprise users.

If you need help with remote setup, please email our maintainer linghua@cocoindex.io, happy to help!!

## License

Apache-2.0
