---
name: ccc
description: "This skill should be used when code search, file/directory summary lookup, or concept-guide lookup is needed (whether explicitly requested or as part of completing a task), when indexing the codebase after changes, or when the user asks about ccc, cocoindex-code, or the codebase index. Trigger phrases include 'search the codebase', 'find code related to', 'describe this file', 'read the concept guide', 'update the index', 'ccc', 'cocoindex-code'."
---

# ccc - Semantic Code Search & Indexing

`ccc` is the CLI for CocoIndex Code, providing semantic search over the current codebase and index management.

## Ownership

The agent owns the `ccc` lifecycle for the current project — initialization, indexing, and searching. Do not ask the user to perform these steps; handle them automatically.

- **Initialization**: If `ccc search` or `ccc index` fails with an initialization error (e.g., "Not in an initialized project directory"), run `ccc init` from the project root directory, then `ccc index` to build the index, then retry the original command.
- **Index freshness**: Keep the index up to date by running `ccc index` (or `ccc search --refresh`) when the index may be stale — e.g., at the start of a session, or after making significant code changes (new files, refactors, renamed modules). There is no need to re-index between consecutive searches if no code was changed in between.
- **Installation**: If `ccc` itself is not found (command not found), refer to [management.md](references/management.md) for installation instructions and inform the user.

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

### Working with Search Results

Search results include file paths and line ranges. To explore a result in more detail:

- Use the editor's built-in file reading capabilities (e.g., the `Read` tool) to load the matched file and read lines around the returned range for full context.
- When working in a terminal without a file-reading tool, use `sed -n '<start>,<end>p' <file>` to extract a specific line range.

### Following Hints in Search Output

Search results are a mixed ranking of code chunks, per-file/dir summaries, and (when configured) curated concept guides — all scored against the same query. Two kinds of hit come with a follow-up command embedded in the output:

- `[summary]` — a file or directory summary. Read with `ccc describe <path>`.
- `[guide]` — a curated concept guide. Read with `ccc guide <slug>`.

When a hit carries one of these tags, follow the hint: the synthesised text is usually a faster read than chasing through individual files. Conversely, do **not** run `ccc describe .` or `ccc guide` proactively as a triage step — let search rank what's relevant and act on what it returns.

## Describing Files and Directories

Per-file and per-directory summaries (when configured for the project) condense each file's public API, contracts, and role into a short markdown block. They are typically faster to consult than reading the source.

```bash
ccc describe src/auth/session.py        # one file
ccc describe src/auth/                  # directory: summary + children tree
ccc describe .                          # project root overview
```

Use `describe` when you already know the path you want; let `ccc search` find paths for you when you don't.

## Concept Guides

Some projects configure cross-cutting concept guides in `.cocoindex_code/guides.yml` — synthesised markdown documents for architectural topics that span many files (e.g. memoization, plugin-SDK boundary, channel routing). Each guide names canonical files, end-to-end flow, and contracts/invariants.

```bash
ccc guide                               # list available guides + descriptions
ccc guide <slug>                        # print one guide
```

Discovery is search-driven: a relevant guide will surface in `ccc search` results tagged `[guide]` with a `ccc guide <slug>` hint. Run `ccc guide` (no args) only when first orienting in an unfamiliar codebase or when the user explicitly asks for the guide list — not as a routine first step.

### Authoring `guides.yml` Interactively

When the user wants to add or improve concept guides, collaborate on the slug list rather than dumping a finished YAML. Good guide candidates are **named subsystems the codebase obviously has** — cross-cutting lifecycles, registration/dispatch protocols, end-to-end data paths. Single-file or symbol-specific topics do not warrant a guide; per-file summaries already cover those.

Recommended flow:

1. **Survey the codebase.** Use `ccc describe .` and a few likely subdirectory summaries to enumerate the project's subsystems and inter-edge boundaries.
2. **Propose candidates.** Suggest 5–10 slugs with one-line descriptions, framed to name the canonical starting file or directory for each topic. Show them to the user as a list.
3. **Iterate.** Ask which to keep, drop, rename, or merge. Surface non-obvious dependencies (`deps:`) so a higher-level guide can cite a lower-level one rather than restate it. Cycles are rejected at load time.
4. **Write the YAML.** Add the agreed entries to `.cocoindex_code/guides.yml` (creating the file if absent). Confirm `defaults.enabled: true` and that the project's summary feature is enabled — guides require summaries.
5. **Generate.** Run `ccc index` to drive the per-guide agent loop and produce `<slug>.md` files under `.cocoindex_code/guides/`. Re-run after editing descriptions to refresh.

Schema:

```yaml
defaults:
  enabled: true                     # disables all guides when false
  model: openai/gpt-5.4-nano        # falls back to summary.model when omitted
  session_budget: 200
  max_logical_depth: 3
  max_turns_per_session: 18

guides:
  - slug: memoization                          # [a-z0-9][a-z0-9-]*
    description: |
      What this guide covers, framed for the reader.
      Name the canonical starting files (e.g. "start with src/cache.py").
    deps: [other-slug]                         # optional; must not cycle
    max_turns_per_session: 28                  # optional per-entry overrides
```

A multi-line description is fine and often clearer than one terse sentence — the description seeds the guide-generation agent's question, so concrete file/directory anchors pay off.

## Settings

To view or edit embedding model configuration, include/exclude patterns, or language overrides, see [settings.md](references/settings.md).

## Management & Troubleshooting

For installation, initialization, daemon management, troubleshooting, and cleanup commands, see [management.md](references/management.md).
