# ADR: Remote Postgres Backend for Shared Index Storage

**Status:** PROPOSED  
**Date:** 2026-04-28  
**Decision Pending:** User input on topology and migration strategy

## Problem

Currently, CocoIndex-Code uses SQLite exclusively for declarations storage (`declarations_db.py`). This design works well for single-repo indexing but presents challenges in multi-repo and production scenarios:

1. **SQLite Limitations for Multi-Repo:**
   - Each daemon instance maintains separate SQLite file (~100 MB per repo)
   - Multiple daemons indexing overlapping repos duplicate declarations data
   - No shared query interface across machines or cloud deployments

2. **Production Deployment:**
   - Cloud deployments require managed databases (AWS RDS, Google Cloud SQL, Supabase, etc.)
   - SQLite files not suitable for cloud storage (network latency, locking issues)
   - Horizontal scaling requires distributed cache coherence strategy

3. **Multi-Daemon Scenarios:**
   - If multiple coco daemons run on same machine → duplicate index overhead
   - If multiple teams run daemons → repo declarations reindexed independently
   - No way to share findings across team instances

## Questions to Resolve (Before Implementation)

### Q1: Storage Topology
**A: Single shared Postgres DB vs. per-repo schemas in one instance?**

- **Option A: Single DB, Per-Repo Schemas**
  ```sql
  CREATE SCHEMA repo_1_main;
  CREATE SCHEMA repo_1_dev;
  CREATE SCHEMA repo_2_main;
  ```
  - ✅ Clear isolation, easier multi-tenancy
  - ✅ Straightforward migration (dump SQLite, restore to schema)
  - ✅ Can attach/detach repos dynamically
  - ❌ Complex schema management as repos scale

- **Option B: Single DB, Repo ID as FK (Shared Schema)**
  ```sql
  CREATE TABLE declarations (
    id INTEGER PRIMARY KEY,
    repo_id TEXT,  -- repo-owner/repo-name
    ...
  );
  CREATE INDEX idx_repo_id ON declarations(repo_id);
  ```
  - ✅ Simpler schema (no schema switching)
  - ✅ Can query across repos in single statement
  - ❌ Tighter coupling (risk of accidental cross-repo queries)
  - ❌ Performance: More indexes, larger tables

- **Option C: Multiple Postgres Instances**
  ```
  Primary: repo_1, repo_2, repo_3
  Replica: Mirror of Primary
  ```
  - ✅ Easy sharding if repos grow beyond one instance
  - ❌ Operational complexity (backup, migration, HA)
  - ❌ Overkill for <10 repos

### Q2: Migration Strategy
**A: Path from existing SQLite files to Postgres?**

- **Option A: Dump & Restore**
  ```bash
  sqlite3 ~/.cocoindex_code/declarations.db ".dump" | psql -h postgres.example.com
  ```
  - ✅ One-time migration, clean break
  - ❌ Must stop all indexing during migration

- **Option B: Gradual Dual-Write**
  ```python
  # New code writes to BOTH SQLite and Postgres
  db.insert_declaration(...)  # SQLite
  pg.insert_declaration(...) # Postgres
  # After verification, drop SQLite writes
  ```
  - ✅ Can run old/new daemons in parallel
  - ❌ Complex temporary state (data divergence risk)

- **Option C: Fresh Reindex to Postgres**
  ```bash
  coco reindex --output=postgres --connection=postgresql://...
  ```
  - ✅ Cleanest, no legacy data
  - ❌ Requires reindexing all repos (slower)

### Q3: Connection & Auth Surface
**A: How do daemons connect to shared Postgres?**

- **Option A: Connection String from Env**
  ```bash
  export COCOINDEX_POSTGRES_URL="postgresql://user:password@host/coco_index"
  coco daemon
  ```
  - ✅ Simple, familiar (12-factor)
  - ❌ Secrets in environment (rotation harder)

- **Option B: Secrets Manager (AWS Secrets Manager, Vault, etc.)**
  ```python
  token = client.get_secret("coco-postgres-credentials")
  conn_str = f"postgresql://{token['user']}:{token['password']}@{token['host']}/..."
  ```
  - ✅ Secrets rotated without daemon restart
  - ❌ Operational complexity, vendor lock-in

- **Option C: Hybrid (Env + Local File Fallback)**
  ```python
  url = os.getenv("COCOINDEX_POSTGRES_URL")
  if not url:
      url = Path("~/.cocoindex_code/postgres_url").read_text()
  ```
  - ✅ Flexibility, gradual migration
  - ❌ Multiple sources of truth

### Q4: Connection Pooling
**A: How many connections? Timeouts? Retry strategy?**

- **Default Recommendation:**
  ```python
  pool_size = 5  # Per daemon
  max_overflow = 10
  timeout = 30s
  retry_strategy = exponential_backoff(base=1s, max=10s, attempts=3)
  ```

- **Considerations:**
  - Each daemon needs 1–2 persistent connections
  - Bursts during reindex may need pool overflow
  - Cloud databases have connection limits per user

## Design Recommendations

### Recommended Path: **Option A + Option A + Option A**

1. **Topology:** Single Postgres DB with per-repo schemas
   - Simplest migration path
   - Clear isolation for access control
   - Straightforward to add/remove repos

2. **Migration:** Dump & restore (one-time, clean)
   - Stop indexing briefly
   - `sqlite3 declarations.db ".dump" | psql`
   - Verify data, then retire SQLite

3. **Auth:** Connection string from env + optional Secrets Manager
   - Start with `COCOINDEX_POSTGRES_URL` env var
   - Add Secrets Manager support as add-on

## Next Steps

1. **User Decision:**
   - Approve recommended path or suggest alternatives?
   - Timeline for implementation (Phase 6 or later)?

2. **If Approved:**
   - Create `declarations_db.py` Postgres adapter layer (keep SQLite layer too)
   - Write migration script (SQLite → Postgres)
   - Add tests for Postgres backend
   - Document setup & connection requirements

3. **If Deferred:**
   - Keep SQLite-only until multi-daemon use case becomes critical
   - Revisit when cloud deployment is prioritized

## Open Questions

- Will users deploy on single machine or across multiple machines/clouds?
- Do we need multi-region failover or is single region OK?
- What backup/HA strategy (RDS automated backups, manual snapshots, etc.)?
