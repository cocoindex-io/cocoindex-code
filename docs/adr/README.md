# Architecture Decision Records (ADRs)

This directory contains architectural decisions for the CocoIndex-Code fork enhancement project.

## Overview

ADRs document major design decisions, alternatives considered, and rationale for chosen paths. They serve as decision artifacts and help future maintainers understand why certain technical choices were made.

## Decision Log

| Status | Title | Area | Date | Link |
|--------|-------|------|------|------|
| PROPOSED | Remote Postgres Backend | Storage | 2026-04-28 | [postgres-backend.md](postgres-backend.md) |
| PROPOSED | Shared Cache Invalidation | Caching | 2026-04-28 | [cache-invalidation.md](cache-invalidation.md) |

## Active Decisions

### [postgres-backend.md](postgres-backend.md)
**Status:** PROPOSED (awaiting user input)

Addresses multi-repo storage limitations in SQLite. Explores single vs. sharded Postgres topologies, migration strategies, and authentication approaches.

**Key Questions:**
- Single DB with per-repo schemas or multi-instance sharding?
- Dump/restore vs. gradual dual-write migration?
- Connection pooling and auth strategy?

**Recommendation:** Single DB, per-repo schemas, dump/restore migration, env-based auth.

**Next Steps:** User decision → implementation in Phase 6.

---

### [cache-invalidation.md](cache-invalidation.md)
**Status:** PROPOSED (awaiting user input)

Addresses cache staleness in multi-daemon scenarios. Explores TTL, event-driven, and polling invalidation mechanisms.

**Key Questions:**
- TTL vs. event-driven invalidation?
- Repo, file, or symbol-level scope?
- Postgres NOTIFY, file polling, or Redis pub/sub?

**Recommendation:** TTL + repo-level scope + Postgres NOTIFY (with file fallback).

**Next Steps:** User decision → implementation in Phase 6.

---

## Decision Workflow

1. **Identify Decision:** Recognize architectural choice that needs documentation
2. **Write ADR:** Document problem, options, trade-offs, and recommendation
3. **Review:** Share with team for feedback and alternative suggestions
4. **Decide:** User approves recommended path or selects alternative
5. **Implement:** Code implementation follows approved decision
6. **Update Status:** Mark ADR as APPROVED, REJECTED, or SUPERSEDED

## ADR Template

```markdown
# ADR: [Title]

**Status:** PROPOSED | APPROVED | REJECTED | SUPERSEDED  
**Date:** YYYY-MM-DD  
**Decision Pending:** User approval or specific questions

## Problem
[What is the decision context and what problem does it address?]

## Questions to Resolve
### Q1: [Key question]?
- **Option A:** [Description, pros, cons]
- **Option B:** [Description, pros, cons]
- **Option C:** [Description, pros, cons]

## Recommendation
[Recommended path with rationale]

## Next Steps
[What happens next once decision is made]
```

## Related Documentation

- **[FORK_STRATEGY.md](../FORK_STRATEGY.md)** — Fork versioning and maintenance
- **[../../README.md](../../README.md)** — Project overview and quick start

## Contributing

To propose a new architectural decision:

1. Create new file: `adr/NNNN-short-title.md`
2. Fill in template above
3. Submit PR with context and reasoning
4. Discuss with team before implementation

---

**Last Updated:** 2026-04-28  
**Maintainer:** murat-hq
