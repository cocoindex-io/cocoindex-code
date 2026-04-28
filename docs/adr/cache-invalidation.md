# ADR: Shared Cache Invalidation Strategy

**Status:** PROPOSED  
**Date:** 2026-04-28  
**Decision Pending:** User input on invalidation mechanism and scope

## Problem

CocoIndex maintains in-memory caches for search results, symbol lookups, and graph computations:
- `HybridSearch` caches BM25 indexes and ripgrep results
- `DeclarationsGraph` caches cross-repo symbol resolution
- `declarations_db` caches recent queries

These work well for a single daemon, but in multi-daemon or multi-client scenarios, cache staleness becomes problematic:

1. **Scenario A: Multiple Daemons on Same Machine**
   - Daemon 1 indexes `repo_A` → updates local cache
   - Daemon 2 still has old cache for `repo_A`
   - Client queries Daemon 2 → gets stale results

2. **Scenario B: Shared Postgres Backend**
   - Daemon 1 reindexes → Postgres updated
   - Daemon 2's local cache still has old data
   - No mechanism to notify Daemon 2 to invalidate

3. **Scenario C: Multi-Machine Deployment**
   - Machine A (daemon) reindexes `repo_X`
   - Machine B (client) caches `repo_X` search results
   - Machine B unaware of updates → returns stale results

## Questions to Resolve (Before Implementation)

### Q1: Invalidation Mechanism
**A: TTL-based vs. event-driven vs. polling?**

- **Option A: Time-To-Live (TTL) Expiry**
  ```python
  cache[key] = (result, time.time())
  
  def get_cached(key):
      result, ts = cache.get(key)
      age = time.time() - ts
      if age > TTL_SECONDS:
          del cache[key]  # Expired
          return None
      return result
  ```
  - ✅ **Simple:** No coordination needed
  - ✅ **Predictable:** Staleness bounded by TTL
  - ❌ **Latency:** Requires TTL wait for eventual consistency
  - ❌ **Efficiency:** May serve old results unnecessarily
  - **Best for:** Eventual consistency acceptable, simplicity important

- **Option B: Event-Driven Invalidation**
  ```python
  # When file changes detected:
  daemon.on_file_changed(path) →
    invalidate_symbol_cache(path) →
    notify_all_clients(path)
  ```
  - ✅ **Immediate:** Fresh results after every change
  - ✅ **Efficiency:** Only invalidate what changed
  - ❌ **Complex:** Requires event bus, messaging layer
  - ❌ **Coordination:** Multiple daemons must notify each other
  - **Best for:** Real-time accuracy critical, willing to add infrastructure

- **Option C: Polling**
  ```python
  # Clients periodically ask daemon for cache status:
  client.poll_daemon(interval=30s)
    ← daemon.get_cache_version()
  # If version changed, client invalidates local cache
  ```
  - ✅ **Moderate:** Simpler than event-driven
  - ⚠️ **Tradeoff:** Latency = poll interval (30s staleness)
  - ❌ **Load:** Every client polls every N seconds
  - **Best for:** Occasional updates, can tolerate 30–60s staleness

### Q2: Cache Key Scope
**A: Invalidate at repo, file, or symbol level?**

- **Option A: Repo-Level Scope**
  ```python
  cache_version = {
      "owner/repo_A": 1,
      "owner/repo_B": 2,
  }
  # Invalidate entire repo when ANY file changes
  cache_version["owner/repo_A"] += 1
  ```
  - ✅ **Simple:** One version per repo
  - ✅ **Safe:** No accidental stale cross-repo queries
  - ❌ **Inefficient:** Changes to 1 file flush entire repo cache
  - **Best for:** Repos <10K files, simplicity priority

- **Option B: File-Level Scope**
  ```python
  cache_version = {
      "owner/repo_A": {
          "src/main.py": 1,
          "src/utils.py": 2,
      }
  }
  # Invalidate only changed files
  cache_version["owner/repo_A"]["src/main.py"] += 1
  ```
  - ✅ **Efficient:** Granular invalidation
  - ❌ **Complex:** Tracking N files per repo
  - ❌ **Risk:** Cross-file symbol references may cause misses
  - **Best for:** Large monorepos, complex dependency graphs

- **Option C: Symbol-Level Scope**
  ```python
  cache_version = {
      "owner/repo_A": {
          "src/main.py:MyClass": 1,
          "src/main.py:my_func": 2,
      }
  }
  ```
  - ✅ **Ultra-efficient:** Only flush affected symbols
  - ❌ **Very complex:** Symbol tracking overhead
  - ❌ **Fragile:** Cross-file references, re-exports complicate tracking
  - **Best for:** Massive codebases, sophisticated cache management

### Q3: Coordination Mechanism
**A: How do daemons/clients exchange invalidation messages?**

- **Option A: Shared Postgres Notifications**
  ```sql
  -- Daemon 1 (after reindex):
  NOTIFY cocoindex_cache, json_build_object('repo', 'owner/repo_A', 'version', 2);
  
  -- Daemon 2 (listening):
  LISTEN cocoindex_cache;
  -- Receives notification → invalidates cache
  ```
  - ✅ **Integrated:** Uses existing Postgres backend
  - ✅ **Scalable:** Supports N daemons
  - ❌ **Postgres-only:** Requires Postgres (not SQLite-only)

- **Option B: Local File-Based Events**
  ```
  ~/.cocoindex_code/cache_version.json:
  { "owner/repo_A": 2, "owner/repo_B": 1 }
  
  Daemon polls file every 5s, compares version
  ```
  - ✅ **Works with SQLite:** No Postgres dependency
  - ✅ **Local:** No network required
  - ❌ **Limited:** Only works on same machine
  - ❌ **Race conditions:** File race between daemons

- **Option C: Redis Cache Invalidation**
  ```python
  # Daemon 1:
  redis.incr(f"cache_version:owner/repo_A")
  
  # Daemon 2:
  pubsub = redis.pubsub()
  pubsub.subscribe("cache_invalidations")
  # Listens for version changes
  ```
  - ✅ **Powerful:** Pub/sub, multi-machine
  - ❌ **Extra Dependency:** Requires Redis server
  - **Best for:** Distributed team deployments

### Q4: Daemon API Integration
**A: How does `daemon.py` expose cache operations?**

Current `daemon.py` serves search & indexing requests. New cache operations:

```python
# In daemon handlers (mcp_handlers.py):
@daemon.handler("get_cache_status")
def get_cache_status(repo_id: str) -> dict:
    return {
        "repo_id": repo_id,
        "version": cache_versions.get(repo_id, 0),
        "hit_rate": cache.stats()["hit_rate"],
        "size_bytes": cache.total_size(),
    }

@daemon.handler("invalidate_cache")
def invalidate_cache(repo_id: str, scope: str = "repo") -> dict:
    # scope: "repo" | "file" | "symbol"
    cache.invalidate(repo_id, scope=scope)
    return {"invalidated": repo_id}
```

- **Decision:** Which operations to expose? TTL or manual invalidation?

## Design Recommendations

### Recommended Path: **Option A (TTL) + Option A (Repo-Level) + Option A (Postgres NOTIFY, fallback to file)**

**Rationale:**
1. **TTL Invalidation:** Simple, works immediately without infrastructure
   - Start with 5–10 minute TTL
   - Upgrade to event-driven later if needed

2. **Repo-Level Scope:** Clear semantics, easy to understand
   - When `repo_A` reindexes, increment version counter
   - All clients using that repo invalidate on next access

3. **Postgres NOTIFY (if Postgres backend adopted) + File Fallback (if SQLite-only)**
   - Postgres: Use `NOTIFY` for immediate invalidation
   - SQLite: Use `~/.cocoindex_code/cache_versions.json` polling

### Phase 6 Implementation (After Postgres ADR Decision)

If both Postgres and cache invalidation approved:
```python
class DeclarationsDB:
    def __init__(self, backend="sqlite"):
        self.backend = backend  # "sqlite" or "postgres"
    
    def on_reindex_complete(self, repo_id: str):
        self.invalidate_cache(repo_id)
        
        if self.backend == "postgres":
            # Notify other daemons via Postgres
            cursor.execute(
                "NOTIFY cocoindex_cache, %s",
                (json.dumps({"repo_id": repo_id, "version": new_version}),)
            )
        else:
            # Write to file, other daemons poll
            versions = json.loads(cache_versions_file.read_text())
            versions[repo_id] = new_version
            cache_versions_file.write_text(json.dumps(versions))
```

## Next Steps

1. **User Decision:**
   - Approve TTL + repo-level + Postgres NOTIFY approach?
   - Or suggest alternatives?

2. **If Approved:**
   - Add `CACHE_TTL_MINUTES` config setting (default: 5)
   - Add `cache_version` counter to `declarations_db`
   - Implement Postgres NOTIFY listener in daemon
   - Write tests for cache invalidation

3. **If Deferred:**
   - Keep in-memory caching as-is
   - Revisit when multi-daemon scenario becomes common

## Open Questions

- Acceptable cache staleness window (5 min? 30 min?)?
- Should clients explicitly invalidate cache or purely passive?
- Do we need cache hit/miss metrics for observability?
- Should cache be shared across processes or per-process isolated?
