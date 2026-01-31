# CocoIndex Code

An MCP (Model Context Protocol) server for indexing and querying codebases using [CocoIndex](https://cocoindex.io).

## Features

- **Semantic Code Search**: Find relevant code using natural language queries
- **Incremental Indexing**: Only re-indexes changed files for fast updates
- **Multi-Language Support**: Python, JavaScript/TypeScript, Rust, Go
- **Vector Embeddings**: Uses sentence-transformers for semantic similarity
- **SQLite Storage**: Portable, no external database required

## Installation

```bash
pip install cocoindex-code
```

Or with uv:

```bash
uv pip install cocoindex-code
```

## Usage with Claude Code

Add to your Claude Code MCP configuration (`.claude/mcp_config.json`):

```json
{
  "mcpServers": {
    "codebase": {
      "command": "cocoindex-code",
      "env": {
        "COCOINDEX_CODE_ROOT_PATH": "/path/to/your/codebase"
      }
    }
  }
}
```

Or without explicit path (auto-discovers from current directory):

```json
{
  "mcpServers": {
    "codebase": {
      "command": "cocoindex-code"
    }
  }
}
```

## Configuration

Environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `COCOINDEX_CODE_ROOT_PATH` | Root path of the codebase | Auto-discovered (see below) |
| `COCOINDEX_CODE_EMBEDDING_MODEL` | Embedding model to use | `sentence-transformers/all-MiniLM-L6-v2` |

### Root Path Discovery

If `COCOINDEX_CODE_ROOT_PATH` is not set, the codebase root is discovered by:

1. Finding the nearest parent directory containing `.cocoindex_code/`
2. Finding the nearest parent directory containing `.git/`
3. Falling back to the current working directory

## MCP Tools

### `update_index`

Updates the codebase index to reflect the latest content. Run this before querying if you've made changes.

```
update_index()
```

### `query`

Search the codebase using semantic similarity.

```
query(
    query: str,        # Natural language query or code snippet
    limit: int = 10,   # Maximum results (1-100)
    offset: int = 0    # Pagination offset
)
```

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
│   ├── index.db        # Vector index (SQLite + sqlite-vec)
│   └── cocoindex.db    # CocoIndex state
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

Common generated directories are automatically excluded:
- `__pycache__/`
- `node_modules/`
- `target/`
- `dist/`
- `build/`
- `.git/`

## Development

```bash
# Clone the repository
git clone https://github.com/cocoindex-io/cocoindex-code.git
cd cocoindex-code

# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest
```

## License

MIT
