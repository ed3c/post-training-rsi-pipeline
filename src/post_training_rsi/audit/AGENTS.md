# AGENTS.md — `src/post_training_rsi/audit/`

Read the repository root contracts, `src/post_training_rsi/AGENTS.md`, `docs/coevolution-convergence.md`, and `docs/architecture-manifest.json` before editing this package.

## Purpose

This directory owns read-only integrity verification for the durable local Co-Evolution evidence graph and status views used by human recovery procedures.

It may inspect:

```text
Co-Evolution Run revisions
latest control transaction and StateSnapshot
all committed control transactions and record hashes
Peak pointer and Checkpoint bundles
active Harness pointer and Harness snapshots
Trace Dataset bundles and exact accepted SHA-256
approval samples/Requests/Decisions
quarantine/rollback markers
retained lock files
```

It may write only the explicit audit report:

```text
reports/coevolution-audit.json
```

## Forbidden responsibilities

This package must not:

- mutate Run, Peak, Harness, approval, Checkpoint, Dataset, or quarantine state;
- delete or rename retained lock files;
- decide whether a lock is stale from timestamp alone;
- rewrite a hash, ID, pointer, transaction, manifest, or evidence record;
- synthesize missing evidence;
- approve a Dataset, Harness, or Checkpoint;
- retry a provider, training job, deployment, or benchmark;
- promote, roll back, slim, or resume the State Machine;
- claim that a WARN/FAIL has been repaired;
- weaken a component policy or persistence invariant;
- perform Git, Git Town, cloud, GPU, or production endpoint mutations.

Recovery actions remain human-owned and must be documented, not executed automatically.

## Status semantics

```text
PASS  verified facts agree
WARN  evidence is incomplete or needs human investigation, but no proven conflict
FAIL  a required identity, hash, transaction, pointer, or artifact is invalid/missing
```

`--strict` promotes the overall result from WARN to FAIL. It does not rewrite individual check records.

CLI exit codes:

```text
0  PASS, or WARN in non-strict mode
2  FAIL, or WARN in strict mode
```

## Read-only invariants

- `coevolve-status` does not write any file.
- `coevolve-audit` may update only `reports/coevolution-audit.json`.
- Constructors that create missing directories must not be used to disguise absent evidence.
- A non-local artifact URI is WARN unless a configured verifier can read its bytes.
- An orphan immutable record is WARN, never auto-deleted.
- A retained lock is WARN, never auto-deleted.
- Tamper, pointer mismatch, missing transaction dependency, or exact-byte hash mismatch is FAIL.
- A pending Run approval must bind the exact immutable Request ID and SHA-256.
- A Decision that exists while the Run is still pending is valid resumable evidence; the controller has not yet consumed it.
- An unresolved Request not referenced by the Run pointer is WARN and must be preserved for forensics.

## Required checks

```text
workspace existence
Run pointer ↔ immutable revision
Run pointer ↔ latest transaction ↔ latest Snapshot
all transaction manifests and referenced record hashes
orphan control records
active Peak ↔ Run metadata ↔ Checkpoint bundle ↔ local artifact
all Checkpoint bundles
active Harness ↔ Run metadata ↔ ACCEPT Decision ↔ snapshot
all Harness snapshots
Trace Dataset required files, JSONL rows, counts, and accepted SHA-256
approval Request/Sample/Decision integrity and pending binding
quarantine marker ↔ committed Decision
retained lock inventory
```

## Test requirements

```text
clean completed workspace PASS
status view matches durable Run
strict WARN exit behavior
Run pointer/history tamper FAIL
control record tamper FAIL
Peak pointer or model artifact tamper FAIL
Harness snapshot tamper FAIL
Trace Dataset missing/malformed/hash/count mismatch FAIL
approval Request/Decision tamper FAIL
orphan record WARN
retained lock WARN without deletion
non-local artifact WARN
report schema and exit code
read-only mutation inventory excluding explicit report
CLI status/audit output and exit codes
parent demo/rsi/coevolve compatibility
```

Tests are deterministic and require no network, API key, GPU, Docker daemon, cloud account, or production endpoint.

## Evidence boundary

A passing local audit proves only that the checked local evidence graph is internally consistent at audit time. It does not prove provider correctness, model quality, benchmark validity, production privacy, enterprise identity, distributed storage, disaster recovery readiness, Git Town configuration, or production readiness.
