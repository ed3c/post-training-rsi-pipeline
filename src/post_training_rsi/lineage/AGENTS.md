# AGENTS.md — `src/post_training_rsi/lineage/`

This file narrows the repository and source-level Agent contracts for lineage persistence. Read, in order:

1. [`../../../AGENTS.md`](../../../AGENTS.md)
2. [`../AGENTS.md`](../AGENTS.md)
3. [`../../../docs/control-plane-contracts.md`](../../../docs/control-plane-contracts.md)
4. [`../../../docs/lineage-runtime.md`](../../../docs/lineage-runtime.md)
5. [`../../../docs/state-machine.md`](../../../docs/state-machine.md)
6. the exact tests covering the store being changed

## Ownership

This directory owns durable, local, provider-neutral evidence persistence:

```text
schema-v1 control records
Checkpoint metadata + LineageManifest + artifact hash
accepted Peak pointer and immutable Peak history
quarantine/reject/rollback markers
integrity lookup and replay validation
```

It does **not** own:

- score thresholds, promotion policy, or rollback policy;
- provider SDK calls, model training, serving, or evaluation;
- approval authority;
- CLI composition;
- remote DVC/lakeFS/MLflow configuration.

A store may verify that a committed Decision is internally consistent with a requested write. It must never invent a Decision or decide that a Candidate is good enough.

## Persistence invariants

1. Immutable IDs are content-addressed by exact canonical bytes. Exact retries succeed; different content under the same ID fails.
2. A control record is committed only when a transaction marker references it. Orphan files from interrupted attempts are not committed evidence.
3. Transaction markers are written last.
4. Checkpoint bundles stage all files and become visible through one atomic directory rename.
5. Peak updates use compare-and-swap against the caller’s expected previous Checkpoint.
6. Peak score increases strictly and Peak iteration never moves backwards.
7. A Peak pointer requires a committed `PROMOTE` Decision targeting the same Checkpoint and a matching Checkpoint bundle.
8. Quarantine markers require a committed `QUARANTINE`, `REJECT`, or `ROLLBACK` Decision for the same subject and evidence.
9. Hashes, Run IDs, iteration numbers, Decision IDs, subjects, and parent links are verified on every load.
10. Symlinks are rejected for artifact hashing. Paths are derived from validated IDs; callers do not choose storage paths.
11. Locks fail closed. A stale lock is a human-owned recovery operation; code must not guess that it is safe to remove.
12. No destructive overwrite or silent repair. Corruption raises `LineageIntegrityError`.

## Commit order

### Control transaction

```text
validate records and dependencies
  → write immutable record files
  → write transaction manifest last
  → reload and verify hashes/schema
```

### Checkpoint bundle

```text
verify committed control transaction
  → hash artifact
  → stage checkpoint.json
  → stage lineage_manifest.json
  → stage bundle_manifest.json last
  → atomic directory rename
  → reload and verify
```

### Peak compare-and-swap

```text
load and verify current Peak
  → compare expected previous Checkpoint
  → require monotonic iteration and strict score increase
  → verify PROMOTE Decision + Checkpoint bundle
  → write immutable history record
  → atomically replace peak_checkpoint.json
  → reload and verify
```

## Required tests

Every persistence change needs deterministic, no-network tests for applicable paths:

```text
round trip | exact idempotent retry | conflicting retry
uncommitted dependency | orphan record | hash tampering
filename/payload mismatch | stale compare-and-swap
non-PROMOTE Peak attempt | non-monotonic Peak attempt
Checkpoint self-parent | missing transaction | artifact mutation
quarantine subject/action/evidence mismatch
lock timeout | interrupted staging semantics
```

Do not weaken these tests to make a malformed artifact load successfully. Fail closed and preserve forensic evidence.
