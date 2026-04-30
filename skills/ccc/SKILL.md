---
name: ccc
description: "This skill should be used when code search is needed (whether explicitly requested or as part of completing a task), when indexing the codebase after changes, or when the user asks about ccc, cocoindex-code, or the codebase index. Trigger phrases include 'search the codebase', 'find code related to', 'update the index', 'ccc', 'cocoindex-code'."
---

# ccc - Codebase Search & Intelligence

`ccc` is the CLI for CocoIndex Code. It provides semantic search, hybrid retrieval, graph-aware inspection, context lookup, and index management for the current codebase.

`cgrep` is the shell-native companion command. Use it first for quick local search, then escalate to `ccc codebase *` when the task needs MCP-native graph, symbol, workflow, or context tools.

## Ownership

The agent owns the `ccc` lifecycle for the current project — initialization, indexing, and searching. Do not ask the user to perform these steps; handle them automatically.

- **Initialization**: If `ccc search` or `ccc index` fails with an initialization error (e.g., "Not in an initialized project directory"), run `ccc init` from the project root directory, then `ccc index` to build the index, then retry the original command.
- **Index freshness**: Keep the index up to date by running `ccc index` (or `ccc search --refresh`) when the index may be stale — e.g., at the start of a session, or after making significant code changes (new files, refactors, renamed modules). There is no need to re-index between consecutive searches if no code was changed in between.
- **Installation**: If `ccc` itself is not found (command not found), refer to [management.md](references/management.md) for installation instructions and inform the user.

## Primary Commands

- **Fast local search**

```bash
cgrep <natural language query>
cgrep watch
```

Examples:

```bash
cgrep where user sessions are managed
cgrep request validation src/api -m 5 -c
```

- **Search by meaning**

```bash
ccc search <query terms>
```

The query should describe the concept, functionality, or behavior to find, not exact code syntax. For example:

```bash
ccc search database connection pooling
ccc search user authentication flow
ccc search error handling retry logic
```

- **Search with richer retrieval modes**

```bash
ccc codebase search <query terms> --mode hybrid
ccc codebase search <query terms> --mode keyword
ccc codebase search <query terms> --mode grep
```

- **Review and debugging workflows**

```bash
ccc codebase workflow review --ref-spec HEAD~3..HEAD
ccc codebase workflow debug --query "timeout when saving settings"
ccc codebase workflow onboard
```

- **Graph and symbol inspection**

```bash
ccc codebase symbol SessionStore
ccc codebase impact SessionStore
ccc codebase flow handle_request
ccc codebase graph visualize --format html --output graph.html
```

### Filtering Search Results

- **By language** (`--lang`, repeatable): restrict results to specific languages.

  ```bash
  ccc search --lang python --lang markdown database schema
  ```

- **By path** (`--path`): restrict results to a glob pattern relative to project root. If omitted, defaults to the current working directory (only results under that subdirectory are returned).

  ```bash
  ccc search --path 'src/api/*' request validation
  ```

### Pagination

Results default to the first page. To retrieve additional results:

```bash
ccc search --offset 5 --limit 5 database schema
```

If all returned results look relevant, use `--offset` to fetch the next page — there are likely more useful matches beyond the first page.

### Working with Search Results

Search results include file paths and line ranges. To explore a result in more detail:

- Use the editor's built-in file reading capabilities (e.g., the `Read` tool) to load the matched file and read lines around the returned range for full context.
- When working in a terminal without a file-reading tool, use `sed -n '<start>,<end>p' <file>` to extract a specific line range.

## When to Use `codebase_*`

Prefer `ccc search` for quick semantic lookup. Prefer `ccc codebase *` when you need:

- hybrid vector + keyword retrieval
- symbol relationships
- file or symbol blast radius
- forward flow tracing
- review/debug/onboarding bundles
- non-code context from docs and project metadata

## Settings

To view or edit embedding model configuration, include/exclude patterns, or language overrides, see [settings.md](references/settings.md).

## Management & Troubleshooting

For installation, initialization, daemon management, troubleshooting, and cleanup commands, see [management.md](references/management.md).
