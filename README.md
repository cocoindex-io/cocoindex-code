# CocoIndex Code

An MCP (Model Context Protocol) server for indexing and querying codebases using [CocoIndex](https://cocoindex.io).

## Features

- **Semantic Code Search**: Find relevant code using natural language queries
- **Incremental Indexing**: Only re-indexes changed files for fast updates
- **Multi-Language Support**: Python, JavaScript/TypeScript, Rust, Go, Java, C/C++, C#, SQL, Shell
- **Flexible Embeddings**: Local SentenceTransformers (default) or 100+ cloud providers via [LiteLLM](https://docs.litellm.ai/docs/embedding/supported_embedding)
- **SQLite Storage**: Portable, no external database required

## Prerequisites

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/) (which provides `uvx`):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Usage with Claude Code

No installation needed — `uvx` runs it directly.

### Default (Local Embeddings)

Uses a local SentenceTransformers model (`sentence-transformers/all-MiniLM-L6-v2`). No API key required:

```bash
claude mcp add cocoindex-code \
  -- uvx --prerelease=explicit --with "cocoindex>=1.0.0a13" cocoindex-code@latest
```

### With a Cloud or Custom Embedding Model

Set `COCOINDEX_CODE_EMBEDDING_MODEL` to any [LiteLLM-supported model](https://docs.litellm.ai/docs/embedding/supported_embedding), along with the provider's API key:

<details>
<summary>Ollama (Local)</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=ollama/nomic-embed-text \
  -- uvx --prerelease=explicit --with "cocoindex>=1.0.0a13" cocoindex-code@latest
```

Set `OLLAMA_API_BASE` if your Ollama server is not at `http://localhost:11434`.

</details>

<details>
<summary>OpenAI</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=text-embedding-3-small \
  -e OPENAI_API_KEY=your-api-key \
  -- uvx --prerelease=explicit --with "cocoindex>=1.0.0a13" cocoindex-code@latest
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
  -- uvx --prerelease=explicit --with "cocoindex>=1.0.0a13" cocoindex-code@latest
```

</details>

<details>
<summary>Gemini</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=gemini/text-embedding-004 \
  -e GEMINI_API_KEY=your-api-key \
  -- uvx --prerelease=explicit --with "cocoindex>=1.0.0a13" cocoindex-code@latest
```

</details>

<details>
<summary>Mistral</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=mistral/mistral-embed \
  -e MISTRAL_API_KEY=your-api-key \
  -- uvx --prerelease=explicit --with "cocoindex>=1.0.0a13" cocoindex-code@latest
```

</details>

<details>
<summary>Voyage (Code-Optimized)</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=voyage/voyage-code-3 \
  -e VOYAGE_API_KEY=your-api-key \
  -- uvx --prerelease=explicit --with "cocoindex>=1.0.0a13" cocoindex-code@latest
```

</details>

<details>
<summary>Cohere</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=cohere/embed-english-v3.0 \
  -e COHERE_API_KEY=your-api-key \
  -- uvx --prerelease=explicit --with "cocoindex>=1.0.0a13" cocoindex-code@latest
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
  -- uvx --prerelease=explicit --with "cocoindex>=1.0.0a13" cocoindex-code@latest
```

</details>

<details>
<summary>Nebius</summary>

```bash
claude mcp add cocoindex-code \
  -e COCOINDEX_CODE_EMBEDDING_MODEL=nebius/BAAI/bge-en-icl \
  -e NEBIUS_API_KEY=your-api-key \
  -- uvx --prerelease=explicit --with "cocoindex>=1.0.0a13" cocoindex-code@latest
```

</details>

Any model supported by LiteLLM works — see the [full list of embedding providers](https://docs.litellm.ai/docs/embedding/supported_embedding).

### Setup `.gitignore`

Add the index directory to your `.gitignore` so it isn't committed:

```bash
echo .cocoindex_code >> .gitignore
```

### Uninstall

Remove the MCP server and delete the local index:

```bash
claude mcp remove cocoindex-code
rm -rf .cocoindex_code
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `COCOINDEX_CODE_ROOT_PATH` | Root path of the codebase | Auto-discovered (see below) |
| `COCOINDEX_CODE_EMBEDDING_MODEL` | Embedding model (see below) | `sbert/sentence-transformers/all-MiniLM-L6-v2` |

### Embedding Model

The `COCOINDEX_CODE_EMBEDDING_MODEL` variable uses a prefix to select the embedding backend:

- **`sbert/`** prefix — uses [SentenceTransformers](https://www.sbert.net/) (runs locally, no API key needed). Example: `sbert/sentence-transformers/all-MiniLM-L6-v2`
- **Otherwise** — uses [LiteLLM](https://docs.litellm.ai/docs/embedding/supported_embedding) (supports 100+ providers). Example: `text-embedding-3-small`

### Root Path Discovery

If `COCOINDEX_CODE_ROOT_PATH` is not set, the codebase root is discovered by:

1. Finding the nearest parent directory containing `.cocoindex_code/`
2. Finding the nearest parent directory containing `.git/`
3. Falling back to the current working directory

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

## Index Storage

The index is stored in `.cocoindex_code/` under your codebase root:

```
your-project/
├── .cocoindex_code/
│   ├── target_sqlite.db  # Vector index (SQLite + sqlite-vec)
│   └── cocoindex.db/     # CocoIndex state
├── src/
│   └── ...
```

Add `.cocoindex_code/` to your `.gitignore`.

## Supported File Types

- **Python**: `.py`, `.pyi`
- **JavaScript**: `.js`, `.jsx`, `.mjs`, `.cjs`
- **TypeScript**: `.ts`, `.tsx`
- **Rust**: `.rs`
- **Go**: `.go`
- **Java**: `.java`
- **C**: `.c`, `.h`
- **C++**: `.cpp`, `.hpp`, `.cc`, `.cxx`, `.hxx`, `.hh`
- **C#**: `.cs`
- **SQL**: `.sql`
- **Shell**: `.sh`, `.bash`, `.zsh`
- **Markdown**: `.md`, `.mdx`
- **Plain Text**: `.txt`, `.rst`

Common generated directories are automatically excluded:

- `__pycache__/`
- `node_modules/`
- `target/`
- `dist/`
- `vendor/` (Go vendored dependencies, matched by domain-based child paths)

## Development

### Local Testing with Claude Code

To test locally without installing the package, use the Claude Code CLI:

```bash
claude mcp add cocoindex-code \
  -- uv run --project /path/to/cocoindex-code cocoindex-code
```

Or add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "cocoindex-code": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/cocoindex-code", "cocoindex-code"]
    }
  }
}
```

### Running Tests

```bash
# Install dev dependencies
uv sync --group dev

# Run tests
uv run pytest tests/ -v

# Run pre-commit hooks
uv run pre-commit run --all-files
```

## License

MIT
