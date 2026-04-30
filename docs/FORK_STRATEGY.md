# Fork Strategy & Divergence

This document explains how the coco fork differs from upstream `cocoindex-io/cocoindex-code` and `cocoindex-io/cocoindex`, and how we manage the fork independently.

## What Is This Fork?

The coco fork (`murat-hq/coco`) contains the cocoindex-code package enhanced with production features from muth-hq's cocoindex-plus fork. It is maintained as an **independent fork** — not for upstreaming to the original cocoindex-io projects.

## Ported Features (muth-hq → coco)

### Phases 2-4: Core Analysis Engine ✅
These modules were ported from muth-hq's cocoindex-plus production fork:

| Phase | Module | Purpose |
|-------|--------|---------|
| **2** | declarations_graph.py | Cross-repo symbol resolution (3-tier priority) |
| **2** | declarations_db.py | Enhanced schema + migrations + FK constraints |
| **3** | change_detection.py | Git diff → declarations + risk scoring |
| **4** | analytics/centrality.py | Hub node detection (betweenness centrality) |
| **4** | analytics/communities.py | Leiden clustering for code communities |
| **4** | analytics/flows.py | Entry point detection (FastAPI, HTTP, queues) |
| **4** | analytics/knowledge_gaps.py | Untested hub detection + gap severity |

**Status:** ✅ Complete. All modules ported, tested, and integrated into MCP server.

### Phases 5-6: Search & Operations ✅
These modules have landed in the fork; remaining work is hardening and end-to-end tests.

| Phase | Module | Purpose | Status |
|-------|--------|---------|--------|
| **5** | hybrid_search.py | BM25 FTS + ripgrep fallback | ✅ Landed |
| **5** | rg_bounded.py | Bounded ripgrep wrapper | ✅ Landed (smoke test only) |
| **5** | multi_repo.py | Multi-repo orchestration | ✅ Landed (smoke test only) |
| **5** | setup scripts | bootstrap.sh, setup.sh, doctor.sh | ✅ Landed |
| **6** | config.py | Configuration loading | ✅ Landed |
| **6** | cli.py | CLI tools | ✅ Landed |
| **6** | github_auth.py, github_mirror.py | GitHub mirror + auth | ✅ Landed (smoke test only) |

## Features NOT Ported (Intentional)

### Optional/Specialized
- **declarations.py** — Full extraction engine (1368 lines). Already exists in cocoindex-code; uses our ported graph module.
- **analytics/exporters.py** — GraphML + Obsidian export (visualization tools, not core).
- **mermaid.py** — Diagram generation (optional visualization).
- **scip_ingest.py** — SCIP format support (specialized use case).

### Deferred to Future Phases
- **Remote Postgres** — Requires architecture redesign (currently SQLite-only). Phase 6+.
- **Shared cache layer** — Needs invalidation strategy definition. Phase 6+.
- **LaunchAgent watcher** — macOS-specific daemon. Optional Phase 6.

## Version Scheme

This fork uses **semantic versioning tied to enhancements**:

```
Version: <BASE-UPSTREAM>+coco.<ENHANCEMENT-NUM>

Examples:
  0.2.31+coco.1  — Base upstream 0.2.31 + first enhancement batch
  0.2.31+coco.2  — Base upstream 0.2.31 + second enhancement batch
  0.3.0+coco.1   — When upstream releases 0.3.0, we resume from +coco.1
```

**Rationale:** Clearly separates upstream stability (0.2.31) from coco enhancements (+coco.N).

## Tracking Upstream Changes

If upstream `cocoindex-io/cocoindex-code` releases important updates:

1. **Check upstream releases** regularly: https://github.com/cocoindex-io/cocoindex-code/releases
2. **Review PRs & changes** to see if they conflict with ported features
3. **Update base version** if we want to adopt upstream improvements
4. **Document in changelog** which upstream features we adopt vs. skip

We do **not** automatically rebase on upstream; we cherry-pick features as needed.

## Sync Strategy if Upstream Releases Major Features

If upstream introduces something we want (e.g., improved Postgres support):

1. Fork from latest upstream
2. Apply our ported modules on top
3. Resolve conflicts manually (should be rare; our code is isolated)
4. Test thoroughly
5. Update version to new base: `0.3.0+coco.1`

## How to Identify Fork Changes

### Ported Modules (from muth-hq)
All in `/src/cocoindex_code/`:
```
declarations_graph.py
declarations_db.py
change_detection.py
analytics/centrality.py
analytics/communities.py
analytics/flows.py
analytics/knowledge_gaps.py
hybrid_search.py
rg_bounded.py
multi_repo.py
config.py
cli.py
github_auth.py
github_mirror.py
```

### Enhanced/Extended Modules
- `mcp_handlers.py` — Added 5-6 new tools (get_impact_radius, detect_changes, get_architecture, get_knowledge_gaps, detect_flows)
- `server.py` — Registered new MCP tools

### Tests
```
tests/test_declarations_graph.py
tests/test_declarations_db_migrations.py
tests/test_change_detection.py
tests/test_analytics.py
```

## Maintenance Responsibilities

**Current Fork Maintainer:** murat-hq

**Responsibilities:**
- Keep ported modules working and tested
- Update documentation when new features land
- Monitor upstream for breaking changes
- Coordinate with muth-hq if cocoindex-plus changes
- Release new coco versions when features are ready

## Contributing to This Fork

If you want to improve the fork:

1. **Port a feature from muth-hq?** Create a PR with:
   - Module ported as-is (preserve muth-hq authors)
   - Tests added/updated
   - Documentation updated
   - Version bump (coco.N)

2. **Fix a bug?** Create a PR with:
   - Minimal changes (no scope creep)
   - Tests added for regression
   - Reference the issue

3. **Update docs?** Just submit; no version bump needed.

## Q&A

**Q: Can this fork go back to cocoindex-io/cocoindex-code?**  
A: Only if the upstream maintainers want these features. For now, it's a stable independent fork for muth-hq's production use.

**Q: What if upstream makes incompatible changes?**  
A: We fork from the base version and stay on that version until we decide to upgrade. Low risk because upstream rarely changes APIs.

**Q: How do I know which features are new vs. upstream?**  
A: Check the module list above. If it's not listed, it's upstream. If it's listed and marked "Ported," it's from muth-hq. Enhanced modules are documented in the section above.

**Q: What about cocoindex (the CLI)?**  
A: The cocoindex fork is separate. This is purely for cocoindex-code enhancements. Coordinate separately if needed.

## Release & Versioning

### Version Scheme

This fork uses **semantic versioning tied to enhancements**, independent of upstream:

```
Version: <BASE-UPSTREAM>+coco.<ENHANCEMENT-NUM>

Examples:
  0.2.31+coco.1  — Base upstream 0.2.31 + first enhancement batch
  0.2.31+coco.2  — Base upstream 0.2.31 + second enhancement batch
  0.3.0+coco.1   — When upstream releases 0.3.0, we resume from +coco.1
```

**Rationale:** Clearly separates upstream stability (0.2.31) from coco enhancements (+coco.N).

### Release Process
1. **Ensure tests pass:** `cd cocoindex-code && uv run pytest` (all green)
2. **Update CHANGELOG.md:** Document new features, bug fixes, ported modules
3. **Tag release:**
   ```bash
   git tag v0.2.31+coco.N -m "Release v0.2.31+coco.N: [feature summary]"
   ```
4. **Push tag:**
   ```bash
   git push origin v0.2.31+coco.N
   ```
5. **Build & deploy** artifacts (CI/CD pipeline TBD)

### Compatibility Notes
- Multi-repo extension is **additive**; single-repo mode remains fully compatible
- Minimal breaking changes from upstream cocoindex expected
- Users can safely upgrade within a `0.2.31+coco.*` series (patch compatibility)

### Tracking Upstream
- Monitor https://github.com/cocoindex-io/cocoindex-code/releases
- When upstream updates major/minor version, evaluate:
  - Do we adopt the upstream version as our new base?
  - Do we cherry-pick specific fixes?
  - Do we stay on current base?
- Update this doc and `CHANGELOG.md` with decision
