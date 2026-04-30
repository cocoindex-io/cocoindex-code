# Multi-Repo Support

This document explains the multi-repo orchestration layer used by `ccc workspace *`, `ccc repos *`, and the LanceDB multi-repo indexer.

## Overview

Multi-repo support lets CocoIndex Code work with multiple repositories under one unified root. It covers:

- YAML configuration via `coco-config.yml`
- local and GitHub-backed repositories
- GitHub mirroring through the Trees API
- symlink-based unified roots
- workspace indexing and file watching
- shared search and graph analysis across the unified tree

## Module Architecture

```text
config.py              ← repo/workspace configuration schema
    ↓
github_auth.py         ← GitHub credential resolution
    ↓
github_mirror.py       ← GitHub repo mirroring and refresh policy
    ↓
multi_repo.py          ← unified-root orchestration, sync, and workspace flows
```

Each module builds on the previous one, with explicit dependencies and no circular imports.

## Main Modules

### `config.py`

Defines and validates the `coco-config.yml` schema:

- `RepoType` — `local` or `github`
- `RepoConfig` — one repository entry
- `GitHubConfig` — GitHub token environment settings
- `DeclarationsConfig` — declaration extraction settings
- `CodebaseConfig` — top-level config container

Key helpers:

- `load_codebase_config(config_path)` — load and validate YAML config
- `resolve_config_path(config_path)` — locate config file with sensible defaults

### `github_auth.py`

Resolves GitHub API credentials:

- prefers `GITHUB_TOKEN` or the configured token env var
- falls back to `gh auth token` when available
- caches CLI-derived tokens to avoid repeated subprocess calls

### `github_mirror.py`

Mirrors GitHub repositories locally using the Trees API instead of `git clone`.

Key behaviors:

- one cache directory per mirrored repository
- blob-SHA manifest for incremental refresh
- include/exclude pattern filtering before download
- rate-limit tracking and retry/backoff logic

### `multi_repo.py`

Orchestrates the workspace layer.

Responsibilities:

- sync local and GitHub-backed repositories
- create/update the unified symlink tree
- merge include/exclude settings
- drive workspace indexing flows
- expose workspace status for CLI and tooling

Important methods:

- `sync_and_link_repos(repo_ids=None, force=False)`
- `link_repos(repo_ids=None)`
- `run_status()`
- `build_unified_index(...)`
- `incremental_unified_index(...)`

## Configuration Format

Example `coco-config.yml`:

```yaml
repos:
  - id: my-backend
    type: local
    path: /path/to/backend
    include_patterns:
      - "**/*.py"
    exclude_patterns:
      - "**/__pycache__/**"
    enabled: true

  - id: github-client
    type: github
    repo: owner/client-repo
    branch: main
    include_patterns:
      - "**/*.ts"
      - "**/*.tsx"
    refresh_interval_minutes: 60

github:
  token_env: GITHUB_TOKEN

declarations:
  enabled: true
  languages:
    - typescript
    - python
```

Per-repo `settings:` files may further refine include/exclude patterns and language overrides.

## CLI Surface

Workspace-oriented commands:

```bash
ccc config validate --config coco-config.yml
ccc config show --config coco-config.yml
ccc repos sync --config coco-config.yml
ccc repos status --config coco-config.yml
ccc workspace index --config coco-config.yml
ccc workspace watch --config coco-config.yml --daemon start
```

These commands operate on the configured unified workspace rather than a single local repo.

## LanceDB Multi-Repo Mode

`coco-lance` also supports config-driven multi-repo indexing:

```bash
coco-lance --config coco-config.yml --output ~/indices/myproject/lance
```

This mode:

- syncs configured repositories
- creates a unified root under the output directory
- merges include/exclude settings
- writes one LanceDB table per detected language

## Operational Notes

- Local repos are linked directly; GitHub repos are mirrored into a managed cache first.
- The unified root is designed for indexing and search, not as a writable development workspace.
- Workspace watch mode polls indexed files and reruns incremental indexing when tracked content changes.
- GitHub mirroring is optimized for code search/indexing scenarios, not full git history or branch management.
