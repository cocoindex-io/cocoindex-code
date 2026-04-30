# Changelog

All notable changes to the coco fork of cocoindex-code are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Ported multi-repo orchestration modules (multi_repo.py, config.py, github_auth.py, github_mirror.py)
- Ported search modules (hybrid_search.py, rg_bounded.py)
- Configuration support for multi-repo setups
- Configurable chunker paths via coco-config.yml

### Fixed
- Fixed identity leaks: renamed config filename from "muth-hq-plus-config.yml" to "coco-config.yml"
- Fixed User-Agent string in GitHub mirroring
- Replaced bare asserts with explicit error handling
- Fixed documentation to reflect current CLI surface

### Changed
- Standardized version scheme to `<BASE-UPSTREAM>+coco.<ENHANCEMENT-NUM>` (e.g., 0.2.31+coco.1)

---

## [0.2.31+coco.0] — Initial Fork Release

### Added
- Ported core analysis engine modules from muth-hq:
  - declarations_graph.py — Cross-repo symbol resolution
  - declarations_db.py — Enhanced schema with migrations and FK constraints
  - change_detection.py — Git diff analysis with risk scoring
  - analytics/ suite — Centrality, communities, flows, knowledge gaps
- MCP tools for declarations graph querying
- Enhanced schema and database migrations

### Details
See docs/FORK_STRATEGY.md for the complete list of ported features and modules.

---

## Versioning

This fork uses **semantic versioning with enhancement tracking**:

```
Version: <BASE-UPSTREAM>+coco.<ENHANCEMENT-NUM>

Examples:
  0.2.31+coco.1  — Base upstream 0.2.31 + first enhancement batch
  0.2.31+coco.2  — Base upstream 0.2.31 + second enhancement batch
  0.3.0+coco.1   — When upstream releases 0.3.0, we resume from +coco.1
```

See docs/FORK_STRATEGY.md for details on version management and upstream tracking.
