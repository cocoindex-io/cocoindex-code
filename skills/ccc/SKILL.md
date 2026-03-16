---
name: ccc
description: "This skill should be used when code search is needed (whether explicitly requested or as part of completing a task), when indexing the codebase after changes, or when the user asks about ccc, cocoindex-code, or the codebase index. Trigger phrases include 'search the codebase', 'find code related to', 'update the index', 'ccc', 'cocoindex-code'."
---

# ccc - Semantic Code Search & Indexing

`ccc` is the CLI for CocoIndex Code, providing semantic search over the current codebase and index management.

## Prerequisites

The current project must be initialized before `ccc search` or `ccc index` can be used. If either command fails with an error about missing initialization or the tool not being found, refer to [management.md](references/management.md) for installation and initialization instructions.

## Searching the Codebase

To perform a semantic search:

```bash
ccc search <query terms>
```

The query should describe the concept, functionality, or behavior to find, not exact code syntax. For example:

```bash
ccc search database connection pooling
ccc search user authentication flow
ccc search error handling retry logic
```

### Filtering Results

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

### Refreshing the Index Before Search

To ensure the index reflects the latest code changes before searching:

```bash
ccc search --refresh <query terms>
```

This is equivalent to running `ccc index` followed by `ccc search`.

### Working with Search Results

Search results include file paths and line ranges. To explore a result in more detail:

- Use the editor's built-in file reading capabilities (e.g., the `Read` tool) to load the matched file and read lines around the returned range for full context.
- When working in a terminal without a file-reading tool, use `sed -n '<start>,<end>p' <file>` to extract a specific line range.

## Updating the Index

After making code changes, update the index to keep search results current:

```bash
ccc index
```

This blocks until indexing completes, showing progress. If indexing is already in progress, it waits for completion.

Run `ccc index` proactively after significant code changes (new files, refactors, renamed modules) to ensure subsequent searches return accurate results.

## Management & Troubleshooting

For installation, initialization, daemon management, troubleshooting, and cleanup commands, see [management.md](references/management.md).
