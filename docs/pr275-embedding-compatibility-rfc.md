# Shared snapshot embedding compatibility (RFC)

A member must verify a snapshot's embedding settings before embedding a query.
Matching vector dimensions do not establish that the vectors share a coordinate
space. With synthetic four-dimensional vectors, a query for `fibonacci` can
return `parse csv` with score `1.0` and no SQLite error if the query embedder uses
a different space. This reproduces a failure mode, not a reported production
incident or a comparison of two real embedding models.

## Existing metadata

`EmbeddingSettings` and `resolve_embedder_params()` describe the current daemon's
configuration. They do not identify the settings used to build a snapshot.
`code_chunks_vec` records vectors and content; sqlite-vec's `_info` table records
the extension's creation version. Neither records the embedding provider, model,
or per-call parameters. CocoIndex's memo and target tracking state is in
`cocoindex.db`, which members must not open or receive.

## Proposed contract

Keep a single application-owned row inside the SQLite snapshot:

```sql
CREATE TABLE ccc_snapshot_metadata (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    contract TEXT NOT NULL,
    ready INTEGER NOT NULL CHECK (ready IN (0, 1))
);
```

`contract` is canonical JSON containing format version `1`, provider, model,
and resolved `indexing_params` and `query_params`. Resolution includes the legacy
query-prompt bridge. The existing Sentence Transformers `sbert/` alias is
normalized; device, pacing, and MPS memory settings are excluded.

Indexing and query parameters may differ for asymmetric models. Each side is
compared with the corresponding settings recorded by the leader.

A member checks this row before embedding, using the same read-only SQLite
connection used for the vector query. Missing, incompatible, unsupported, or
unready metadata causes an error. Snapshot replacement during embedding cannot
switch the query to a different file; the next request opens the new snapshot.
Members never create or migrate metadata.

The leader clears readiness before each update and sets it only after a completed
update with zero component errors. Exceptions, cancellation, incomplete updates,
and component errors leave the snapshot unready. A successful retry under the
same contract can restore readiness. Metadata travels with the existing SQLite
backup and atomic replacement workflow; there is no sidecar file.

The daemon advertises `member-embedding-contract-v1` in addition to
`member-readonly-v1`. Member clients require both, preventing a same-version
older daemon from bypassing compatibility checks. Leader handshakes are unchanged.

## Existing indices

| State | Leader behavior | Member behavior |
| --- | --- | --- |
| Newly created index | Record provenance and readiness | Allow when settings match and ready |
| Existing index without metadata | Continue searching and updating | Require a leader-local rebuild and republication |
| Contract changes | Continue existing operations; remove provenance and log a warning | Require a rebuild |
| Settings revert after a contract change | Continue existing operations | Still require a rebuild; mixed vectors cannot be ruled out |
| Failed update under the same contract | Allow retries | Require successful completion and republication |

An incremental update cannot establish the origin of existing vectors. The
implementation therefore does not attach current settings to an unlabelled
index or relabel it after a configuration change. It never automatically resets
or deletes an index. To migrate, reset and rebuild on the leader's local
filesystem, then publish a completed backup. Do not reset the published share.

## Decisions for maintainers

- Whether this table, canonical JSON, version, and readiness flag should become
  the snapshot contract, or whether CocoIndex should expose official metadata.
- Whether member searches should reject legacy snapshots without an opt-out.
  This implementation rejects them while preserving leader operations.
- Whether a query-only parameter change should have an explicit recertification
  path. The current policy conservatively requires a rebuild for any contract
  change, even when stored document vectors are unaffected.
- Whether model revision, endpoint, deployment identity, or library version should
  be included. Configuration equality currently cannot detect changed weights or
  a changed deployment behind the same model string. Credentials and arbitrary
  environment variables are not stored.
- Whether publication should become a first-class command with writer-version
  checks. Only metadata-aware leaders may update labelled databases: an older
  writer may leave stale metadata behind. Rebuild after a writer downgrade.

This retains the PR's one-writer, completed-backup assumption. Concurrent external
writers, SMB replacement semantics, and writer-version fencing are outside this
change and still need validation or design work.

## Validation

Tests use actual SQLite/vec0 queries and CocoIndex indexing with small stub
embedders. Relevant dependencies match `uv.lock`, including CocoIndex `1.0.18`
and sqlite-vec `0.1.6`; dependency definitions and the lockfile are unchanged.

- 87 focused tests passed across snapshot metadata, query filters, project
  lifecycle, parameter forwarding, client, protocol, and MCP server behavior.
- 3 existing member/readonly daemon tests passed.
- Ruff lint, changed-file formatting, and `mypy src/ tests/` passed.
- An additional run, before the last two tests were added, passed 293 tests and
  failed 5: four daemon idle integration tests could not create Unix sockets
  (`PermissionError: [Errno 1] Operation not permitted`), and one test required
  the uninstalled optional `sentence-transformers` package. That run excluded
  `test_daemon.py`, `test_e2e.py`, and `test_e2e_daemon.py`.

Full socket/model end-to-end tests and real SMB validation remain outstanding.

### Rebase validation

Rebased onto upstream `b883be0b5d762c9e2d7d82afdedeade5df21d5be`.
The resolution preserves both `SessionStart` and `PostToolUse` hooks, the user
settings directory guard, MCP root notes, and upstream embedding/chunking
reprocessing behavior. Both hooks are tested for member rejection, normal leader
indexing, and rejection of the user settings directory as a project.

The affected query, snapshot, project, CLI, settings, server, client, protocol,
chunker, and reindex suites passed 225 tests. Two upstream client socket cleanup
tests failed at Unix socket creation with `PermissionError: [Errno 1] Operation
not permitted`; they were not skipped or treated as passing. The existing
member/readonly daemon tests passed another 3 tests. Ruff and mypy passed
(53 source files). The full socket/model end-to-end suite remains unverified.
