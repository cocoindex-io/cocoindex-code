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

## Query-only changes: policy recommendation

Keep the conservative rebuild policy for this PR. A query-only change need not
alter document vectors, but the current metadata cannot safely express a new
query approval while retaining the original document provenance. Limited,
explicit recertification is a reasonable follow-up only with the safeguards
below. This recommendation does not change runtime behavior or replace the
existing compatibility checks and regression tests.

### What the current code establishes

This review is based on head `b607638fd4bc174cf3f528564cac724edb490505`.

- `embedding_contract()` in `snapshot_metadata.py` compares canonical resolved
  settings, including the legacy prompt bridge and `sbert/` normalization. A
  textual settings edit that resolves to the same contract needs no rebuild.
- `process_file()` in `indexer.py` embeds documents with `INDEXING_EMBED_PARAMS`;
  `query_codebase()` in `query.py` uses `query_params`. Query parameters are not
  document embedding inputs. However, `create_embedder()` in `shared.py` also
  gives LiteLLM the indexing parameters as constructor defaults.
  `PacedLiteLLMEmbedder.embed()` overlays a non-`None` query `input_type` on those
  defaults. An absent or `None` query value can therefore inherit the document
  default. Classify changes using effective provider calls, not dictionary names
  alone. For Sentence Transformers, a `prompt_name` also depends on the model's
  prompt definitions and default prompt behavior.
- `Project._run_index_inner()` calls `begin_snapshot_update()` before
  `_app.update()`. A mismatch deletes the metadata row before any update result
  is known. `finish_snapshot_update()` only marks an existing matching row
  ready; it cannot restore deleted provenance. Even a no-op update, a successful
  update, or a settings revert does not recertify that database.
- `tests/test_reindex_on_embed_params_change.py` verifies reprocessing unchanged
  files when indexing parameters change. That is useful incremental behavior,
  not a proof that every persisted vector belongs to a single new generation
  after failures, interruptions, or an untracked backend change.
- `tests/test_snapshot_metadata.py` already covers query-parameter rejection,
  irreversible invalidation after a contract change, and failed-update retries.
  Keep those expectations for this PR.

| Policy | Benefit | Cost or safety requirement |
| --- | --- | --- |
| Rebuild on any resolved contract change | Simple fail-closed member rule; no relabelling of unknown vectors | Re-embeds documents even for a safe query-only change |
| Explicit query recertification | Reuses a known document generation | Requires durable provenance, provider-specific compatibility evidence, exclusive writer coordination, and atomic approval |
| Ignore query parameters during comparison | Avoids rebuilds and approval work | Reject: matching document settings alone does not establish that a query prompt or task is compatible |

### Conditions for a future recertification path

Recertification must approve a query against an existing document generation,
not claim that the documents were produced by the new full contract. A future
metadata version should separate immutable document provenance from query
approval. Record the document generation ID and document embedding identity,
the originally resolved document parameters, and the approved resolved query
contract with its compatibility-policy version and approval evidence. Preserve
the original provenance and prior approval history rather than overwriting it.
An approval must reference the exact document generation it certifies.

All of the following are required:

1. Start with a supported, ready, labelled leader-local generation whose writer
   history is trusted. Reject missing/deleted metadata, an unready generation,
   an older writer, or any interval with unknown writes. The current v1 row
   alone cannot prove stable backend identity retrospectively; a new metadata
   version must not manufacture stronger provenance from it. Rebuild if that
   evidence is unavailable.
2. Prove document embedding identity is unchanged: provider, immutable model
   revision/artifact identity, endpoint/deployment identity, effective document
   parameters, and relevant tokenizer, prompt, preprocessing, normalization and
   adapter behavior. Compare resolved semantics, including defaults and legacy
   resolution. A same-dimension vector or unchanged model string is insufficient.
3. Permit only a provider/model-specific, documented compatible query task or
   prompt for that exact document identity. The current allowlist accepts
   parameter *names* (`prompt_name` or `input_type`), not semantic compatibility
   of their values. Successful API calls and dimension probes do not supply
   that evidence. Retrieval checks can assess quality but cannot prove common
   provenance or exclude a mixed index. Unknown values or mutable identities
   must fail closed; an operator acknowledgement alone is insufficient.
4. Stop indexing and coordinate recertification with the same writer exclusion
   and publication discipline. The current `_index_lock` serializes one Project
   instance only; it is not cross-process fencing. Under exclusive ownership,
   atomically compare the expected generation, provenance and readiness, then
   append the query approval without writing vectors or invoking incremental
   indexing. On a race or failure, retain the previous valid approval or leave
   the candidate unpublished; never mark partially updated vectors ready.
5. Publish a completed SQLite backup with provenance and approvals from one
   consistent database state, then atomically replace the published snapshot.
   Members must validate the exact generation/query approval on the same
   read-only handle used for search. A new schema/capability must prevent an
   older reader or writer from silently bypassing these rules.

This must happen before the current mismatch path destroys provenance. Do not
add a switch that simply rewrites `contract` or sets `ready=1` after
`begin_snapshot_update()` has rejected the index. Do not recover an invalidated
index by copying metadata from an older backup onto its current vectors. A
trusted intact backup may be used as a separate starting generation, subject to
the same identity checks; it does not certify subsequent local writes.

### Model changes and mixed-index risk

An explicit provider, model, or document-parameter change continues to require
a clean leader-local reset/rebuild and republication. Incremental memoization
can retain old rows while changed files acquire new vectors; a failed transition
can leave both spaces present. Reverting the settings cannot undo those writes.
The current permanent invalidation correctly prevents member approval of that
state, even when the vectors have identical dimensions.

A mutable model alias, changed endpoint routing, changed model weights, or a
library/prompt change can produce the same mixture without changing the v1
contract. The current policy does **not** detect this: the same-contract update
may complete and mark it ready. Rebuilding with an unpinned backend also cannot
guarantee a single space if the backend changes during the build. Pin and verify
identity throughout the generation and at query time; if a service cannot expose
an immutable identity, document the operational trust assumption and do not offer
strong automatic recertification for it. Endpoint identity alone is not a model
revision, and library version alone is not proof of unchanged behavior. Store
non-secret identity information only, never credentials or arbitrary environment.

The rebuild policy therefore remains a configuration-level guard under the
one-writer assumption, not cryptographic model attestation. Adding the missing
identity and generation controls has priority over an optimization that waives
query-only rebuilds. A future implementation should add focused tests for
approval without vector writes, inherited defaults, unsupported query tasks,
unknown provenance, interrupted updates, generation races, backend identity
changes, and backup/reader capability behavior. Those are future requirements,
not tests claimed to pass in this documentation-only review.

## Decisions for maintainers

- Whether this table, canonical JSON, version, and readiness flag should become
  the snapshot contract, or whether CocoIndex should expose official metadata.
- Whether member searches should reject legacy snapshots without an opt-out.
  This implementation rejects them while preserving leader operations.
- Whether to accept the recommendation above: retain rebuilds in this PR and
  consider explicit query approval only after provenance and identity support.
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
