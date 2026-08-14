# AGENTS.md — `src/post_training_rsi/recovery_bundle/`

This file narrows the repository and runtime Agent contracts for the forensic backup and staged-restore slice.

## Read order

1. `../../../../AGENTS.md`
2. `../../AGENTS.md`
3. `../../../../README.md`
4. `../../../../docs/implementation-status.md`
5. `../../../../docs/coevolution-audit-recovery.md`
6. `../../../../docs/forensic-recovery-bundle.md`
7. `../../../../docs/stacked-pr-plan.md`
8. `../../../../tests/AGENTS.md`

If a referenced predecessor document is absent on the checked-out parent, treat that as a stack-admission defect. Do not infer recovery authority from a branch name.

## Ownership

This directory owns:

- deterministic enumeration of a local workspace;
- rejection of symbolic links and unsupported filesystem entries;
- content-addressed immutable file blobs;
- canonical `post-training-rsi.recovery-bundle/v1` manifests;
- source file stability checks while bytes are copied;
- exact bundle verification;
- restore into a new inactive directory;
- exact staged-directory verification;
- fail-closed single-writer create/stage lock files;
- structured exit codes through the package-local CLI.

It does **not** own:

- switching a production pointer to the staged directory;
- deleting, repairing, or rewriting the source workspace;
- deciding whether a retained lock is stale;
- model or Harness promotion and rollback policy;
- Dataset, Checkpoint, or Harness approval authority;
- reviewer identity, RBAC, MFA, or quorum;
- remote storage credentials, encryption keys, retention, or legal hold;
- provider retries, GPU jobs, serving endpoints, or Git operations;
- automatic disaster recovery.

## State Machine

```text
SOURCE_SELECTED
  → SOURCE_SCANNED
  → BLOBS_WRITTEN
  → MANIFEST_COMMITTED
  → BUNDLE_VERIFIED

BUNDLE_VERIFIED
  → STAGE_TARGET_RESERVED
  → FILES_RECONSTRUCTED
  → STAGE_VERIFIED
  → STAGED_INACTIVE
```

Failure from any edge is terminal for that invocation. There is no `ACTIVATE` edge in this package.

## Hard invariants

```text
bundle path is outside the source workspace
manifest is written after all content-addressed blobs
bundle_id == SHA-256(canonical identity payload)
manifest paths are canonical relative POSIX paths
symbolic links and special files are rejected
worker-reported or manifest-reported hashes are always recomputed
stage destination must not already exist
staged bytes must exactly match the manifest
successful stage result always reports activated=false
```

Retained `*.create.lock` and `*.stage.lock` files fail closed. Removing one is a human-owned forensic operation after confirming no writer remains.

## Privacy and security boundary

A recovery bundle may contain private model, Dataset, approval, Trace, or operational evidence. The implementation performs no network transmission. Agents must never:

- upload a bundle to another provider without explicit data-and-destination authorization;
- print blob bytes or private records into logs or PR descriptions;
- add secrets or encryption material to Git;
- weaken symlink, path-containment, manifest, or hash checks for convenience;
- interpret a verified bundle as proof that production identity or business correctness is valid.

## Required tests

Changes must cover, as applicable:

```text
deterministic bundle identity
blob deduplication
manifest exact-field validation
path traversal rejection
symlink rejection
special-file rejection
source/output containment
source mutation detection
blob tamper detection
retained lock behavior
new-destination-only staging
staged byte verification
mode preservation
terminal CLI exit codes
no activation side effect
```

Tests must require no network, cloud account, API key, GPU, Docker daemon, or mutable production service.

## Successor boundary

A later recovery-orchestration PR may consume a **verified staged directory**, but activation must remain a separate, human-authorized compare-and-swap operation with:

```text
expected live generation
approved recovery ticket / reviewer identity
exact bundle_id
strict audit PASS for the staged copy
atomic pointer switch
rollback pointer
post-switch audit
```

This PR intentionally stops before that boundary.
