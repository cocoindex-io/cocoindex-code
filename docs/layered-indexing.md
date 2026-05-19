# Git Layered Indexing

Git layered indexing lets one local daemon reuse work across a root clone and its linked worktrees. Instead of rebuilding a full index for every branch, `ccc` composes three layers:

```text
dirty  >  branch  >  base
```

- `base`: an immutable snapshot of the configured base ref, usually `main` or `master`.
- `branch`: files changed between the branch merge base and branch `HEAD`.
- `dirty`: uncommitted working tree changes.

Search results are merged from highest to lowest layer. A file in a higher layer shadows the same file in lower layers, and deleted files are tombstoned so stale base results do not appear.

## Quick Start

Initialize once from the root clone and choose the shared base ref:

```bash
cd ~/src/github/cocoindex-io/cocoindex-code
ccc init --base main
ccc index
```

Then use linked worktrees normally:

```bash
git worktree add ../cocoindex-code.worktrees/feature-1 -b feature-1 main
cd ../cocoindex-code.worktrees/feature-1
ccc index
ccc search "query planner"
ccc overlay status
```

The linked worktree reuses the base layer and only indexes the branch and dirty deltas.

## Configuration Model

Layered indexing has two kinds of configuration.

Project settings stay checkout-local:

```text
<project>/.cocoindex_code/settings.yml
```

They control include/exclude patterns, language overrides, and chunkers. These settings are part of the index configuration hash, so changing them creates new layer IDs and causes affected layers to rebuild.

Repository overlay policy is stored in daemon state:

```text
$COCOINDEX_CODE_STATE_DIR/daemon.db
```

`ccc init --base <ref>` registers the repository policy. Linked worktrees use the same policy automatically when they resolve to the same logical repository.

Current policy fields are:

```yaml
layers:
  enabled: true
  base_ref: main
  dirty: true
  environment_strategy: per-layer
  branch_ttl: 14d
  dirty_ttl: 24h
```

The current implementation persists the base ref and uses the conservative `per-layer` CocoIndex environment strategy. TTLs are applied to branch and dirty layer manifests so stale layers can be pruned.

## Stable IDs

Layered indexing uses names for display and hashes for storage. Physical paths are mutable metadata only.

Repository ID:

```text
hash(normalized_remote_url, repo_relative_root, index_config_hash)
```

Base layer ID:

```text
hash(repo_id, base_ref_name, base_commit_hash, index_config_hash)
```

Branch layer ID:

```text
hash(repo_id, branch_name, head_commit_hash, merge_base_commit_hash, base_layer_id, index_config_hash)
```

Worktree ID:

```text
hash(repo_id, worktree_name, branch_name)
```

Dirty layer ID:

```text
hash(repo_id, worktree_id, branch_name, head_commit_hash, dirty_snapshot_hash, index_config_hash)
```

This means:

- moving a repository does not change its repository ID
- moving a linked worktree does not change its worktree ID if the worktree name and branch stay the same
- advancing `main` or `master` creates a new base layer because the base commit hash changes
- rebasing or merging a feature branch creates a new branch layer because the head or merge-base hash changes
- editing uncommitted files creates a new dirty layer because the dirty snapshot hash changes

## State Layout

The default native layout is:

```text
$COCOINDEX_CODE_STATE_DIR/
  daemon.db
  repos/
    <repo_id>/
      layers/
        <layer_id>/
          src/
          db/
            cocoindex.db
            target_sqlite.db
```

`daemon.db` stores repository metadata, worktree metadata, layer metadata, manifests, and overlay policy. CocoIndex owns the per-layer indexing state under each layer's `db/` directory.

## Commands

Initialize or update the repository base policy:

```bash
ccc init --base main
```

Build or refresh the current layer stack:

```bash
ccc index
```

Override the base ref for a specific command:

```bash
ccc index --base release/1.2
ccc search --base release/1.2 "migration logic"
ccc overlay status --base release/1.2
```

Inspect layer state:

```bash
ccc overlay status
```

Prune expired branch and dirty layers:

```bash
ccc overlay prune
```

## Linked Worktree Example

```bash
cd ~/src/github/cocoindex-io/cocoindex-code
ccc init --base main
ccc index

git worktree add ../cocoindex-code.worktrees/feature-1 -b feature-1 main
cd ../cocoindex-code.worktrees/feature-1
ccc index
ccc search "daemon socket lifecycle"
```

Expected layer stack in the feature worktree:

```text
dirty:  uncommitted changes in feature-1, if any
branch: diff from merge-base(main, feature-1) to feature-1 HEAD
base:   shared main layer
```

## Docker Notes

In Docker, keep `COCOINDEX_CODE_STATE_DIR` on the container-native persistent volume:

```text
/var/cocoindex/state
```

Keep source code mounted under `/workspace`, and use `COCOINDEX_CODE_HOST_CWD` in `docker exec` wrappers so the daemon resolves the correct root clone or linked worktree.

See [Docker Layered Indexing](./docker-layered-indexing.md) for a complete Docker setup.
