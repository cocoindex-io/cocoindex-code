# Multi-Repo Support Modules

Documentation for the multi-repo orchestration feature added in Phase 5b of the coco fork.

## Overview

The multi-repo support enables CocoIndex-Code to work with multiple repositories unified under a single index. It includes configuration management, GitHub repository mirroring, and orchestration across multiple local and remote sources.

## Module Architecture

```
config.py              ← Defines repo configuration schema
    ↓
github_auth.py         ← Resolves GitHub API credentials
    ↓
github_mirror.py       ← Mirrors GitHub repos locally
    ↓
multi_repo.py          ← Orchestrates the entire workflow
```

Each module builds on the previous one, with explicit dependencies and no circular imports.

## Modules

### 1. config.py
**Purpose:** Define and validate multi-repo configuration

**Key Classes:**
- `RepoType` — Enum: "local" | "github"
- `RepoConfig` — Single repository configuration entry
  - `id` — Unique repo identifier
  - `type` — Local or GitHub
  - `path` — Local path (for type=local)
  - `repo` — "owner/name" format (for type=github)
  - `branch` — Target branch (default: "main")
  - `include_patterns` / `exclude_patterns` — File filtering
  - `refresh_interval_minutes` — Sync frequency (default: 120)
  - `enabled` — Active/inactive flag
- `GitHubConfig` — GitHub-specific settings (token env var)
- `DeclarationsConfig` — Language extraction settings
- `CodebaseConfig` — Top-level configuration container

**Key Functions:**
- `load_codebase_config(config_path)` — Load and validate YAML config
- `resolve_config_path(config_path)` — Locate config file with smart defaults

**Validation:**
- RepoConfig validates type/field combinations (local repos need path, GitHub repos need repo)
- CodebaseConfig validates no duplicate repo IDs

**Usage Example:**
```python
config = load_codebase_config("~/.cocoindex_code/config.yml")
# config.repos is list[RepoConfig]
# config.github.token_env is env var name
repo = config.repo_by_id("my-repo")
```

---

### 2. github_auth.py
**Purpose:** Resolve GitHub API credentials

**Key Functions:**
- `resolve_github_token(token_env="GITHUB_TOKEN")` — Get GitHub token
  - Tries env var first
  - Falls back to `gh auth token` if available
  - Returns None if not found
- `token_from_gh_cli(timeout_s=12.0)` — Get token from GitHub CLI
  - Cached per-process to avoid spawning `gh` multiple times
- `reset_gh_cli_token_cache()` — Clear cache (for tests)

**Dependencies:** stdlib only + environment

**Behavior:**
- Environment variable takes precedence: `$GITHUB_TOKEN`
- Falls back to GitHub CLI if installed and authenticated
- Caches result to avoid repeated CLI invocations in multi-repo scenarios

**Usage Example:**
```python
token = resolve_github_token("GITHUB_TOKEN")
if token:
    # Authenticated API calls
    mirror = GitHubMirror(..., token=token)
else:
    # Public API (rate-limited)
    mirror = GitHubMirror(...)
```

---

### 3. github_mirror.py
**Purpose:** Mirror GitHub repositories locally using the GitHub Trees API

**Key Classes:**
- `GitHubMirrorResult` — Dataclass capturing sync outcome
  - `repo_id` — Normalized owner-repo
  - `fetched` — Number of files downloaded
  - `skipped` — Number of files already synced
  - `removed` — Number of deleted files cleaned up
  - `bytes_downloaded` — Total network transfer
  - `branch` — Branch synced
  - `rate_limit_remaining` — GitHub API quota
  - `errors` — List of sync failures
  - `success` — Property: no errors?

- `GitHubMirror` — Main mirror class
  - Syncs one repo at one branch
  - Maintains blob SHA manifest for change detection
  - Supports file include/exclude patterns
  - Handles rate limiting and retries
  - Methods:
    - `sync(force=False)` — Download/update repo files
    - `needs_refresh(interval_minutes)` — Check if refresh needed
    - `status()` — Get last sync info
    - `repo_path` — Cache directory
    - `manifest_path` — SHA manifest file

**Implementation:**
- Uses GitHub Trees API (not Git clone) for efficiency
- Stores manifest as JSON with file SHAs for delta detection
- Only downloads changed files on subsequent syncs
- Supports pattern matching (fnmatch) for filtering
- Respects GitHub rate limits and includes backoff

**Usage Example:**
```python
mirror = GitHubMirror(
    owner_repo="owner/repo-name",
    branch="main",
    include_patterns=["**/*.py", "**/*.ts"],
    exclude_patterns=["**/node_modules/**"],
    cache_root=Path.home() / ".cocoindex_code" / "github_cache",
    token=token,
)

result = mirror.sync(force=False)
print(f"Fetched {result.fetched} files ({result.bytes_downloaded} bytes)")
if result.errors:
    print(f"Errors: {result.errors}")

status = mirror.status()
print(f"Last synced at: {status['synced_at']}")
```

---

### 4. multi_repo.py
**Purpose:** Orchestrate unified indexing across multiple repos

**Key Classes:**
- `MultiRepoOrchestrator` — Main orchestrator class
  - Manages multiple mirrors (local + GitHub)
  - Creates unified directory structure
  - Handles symlink orchestration
  - Methods:
    - `sync_github_mirrors(repo_ids=None, force=False)` — Sync specified GitHub repos
    - `create_unified_symlinks()` — Create unified folder structure
    - `symlink_tree()` — Get symlink tree
    - `status()` — Get overall sync status

**Constants:**
- `DEFAULT_UNIFIED_ROOT` — `~/.cocoindex_code/unified_root`
- `DEFAULT_GITHUB_CACHE` — `~/.cocoindex_code/github_cache`

**Architecture:**
- Loads CodebaseConfig from YAML
- Creates GitHubMirror for each GitHub repo
- Maintains symlink structure pointing to real files
- Supports parallel mirroring of multiple repos
- Caches GitHub token to avoid repeated CLI calls

**Usage Example:**
```python
from cocoindex_code.config import load_codebase_config
from cocoindex_code.multi_repo import MultiRepoOrchestrator

# Load config
config, config_path = load_codebase_config()

# Create orchestrator
orchestrator = MultiRepoOrchestrator(
    config=config,
    config_path=config_path,
)

# Sync all GitHub mirrors
results = orchestrator.sync_github_mirrors()
for result in results:
    print(f"{result.repo_id}: {result.fetched} files, "
          f"rate_limit_remaining={result.rate_limit_remaining}")

# Create unified structure
orchestrator.create_unified_symlinks()
print(f"Unified index at: {orchestrator.unified_root}")
```

---

## Configuration File Format

Repos are configured in YAML. Example:

```yaml
repos:
  - id: my-backend
    type: local
    path: /path/to/backend
    branch: main
    include_patterns:
      - "**/*.py"
    exclude_patterns:
      - "**/__pycache__/**"
    refresh_interval_minutes: 120
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

---

## Integration with CocoIndex-Code

### Indexing Unified Repos

```bash
# Index the unified structure
cocoindex-code index --repo-path ~/.cocoindex_code/unified_root

# Or specify via env var
export COCOINDEX_REPO_ROOT=~/.cocoindex_code/unified_root
cocoindex-code index
```

### Search Across Repos

```python
from cocoindex_code.hybrid_search import keyword_search

# Search is repo-aware (finds matches in all synced repos)
results = keyword_search(
    db_path="~/.cocoindex_code/unified_root/index.db",
    query="authenticate",
    limit=10
)
```

---

## Error Handling

### Configuration Errors
- Invalid repo IDs (duplicate, empty)
- Missing required fields (path for local, repo for GitHub)
- Type/field mismatches (local repos shouldn't have repo field)

### Sync Errors
- GitHub API failures (rate limiting, auth, network)
- File system errors (permissions, disk space)
- Pattern matching issues

All captured in `GitHubMirrorResult.errors` and `MultiRepoOrchestrator.status()`.

---

## Performance

Typical performance:

| Operation | Time | Notes |
|-----------|------|-------|
| Load config | <100ms | YAML parsing |
| Resolve token | 100-500ms | gh CLI call if needed |
| Initial GitHub sync | 10-60s | Depends on repo size |
| Incremental sync | 1-10s | Only changed files |
| Create symlinks | <1s | Per-repo overhead |

---

## Testing

Test file: `tests/test_multi_repo.py`

Current coverage:
- Module imports successfully
- Default paths configured correctly
- Orchestrator initializes with empty config

Future tests should cover:
- Config validation (valid/invalid YAML)
- Token resolution (env vs. gh CLI)
- Mirror sync (mock GitHub API)
- Symlink creation
- Error handling (network, permissions)

---

## Roadmap

### Phase 6 (Planned)
- [ ] Postgres backend for shared declarations
- [ ] Cache invalidation strategy
- [ ] Multi-daemon coordination

### Future
- [ ] Go/Rust language extraction
- [ ] Webhook-driven sync (instead of polling)
- [ ] Multi-region mirror replication

---

## Related Documentation

- **[FORK_STRATEGY.md](../../FORK_STRATEGY.md)** — Fork overview
- **[ADRs](../../docs/adr/)** — Design decisions for Postgres and caching
- **[MCP_TOOLS.md](../../MCP_TOOLS.md)** — MCP tool reference

---

**Last Updated:** 2026-04-28  
**Maintainer:** murat-hq
