# AGENTS.md — `src/post_training_rsi/harness/model_inner_loop/`

Read the repository root, `src/post_training_rsi/AGENTS.md`, PR #8 Harness contract, and PR #9 Trace harvesting contract first.

## Purpose

This package owns the **implemented model inner-loop component**:

```text
TRAIN_MODEL
  → integrity-first training
  → EVALUATE_MODEL under an ephemeral endpoint
  → optional MODEL_REVIEW_PENDING
  → PROMOTE_MODEL | ROLLBACK_MODEL | ABORTED
  → SLIM_HARNESS handoff | FREEZE_MODEL next-cycle handoff
```

It consumes a verified observable Trace Dataset, keeps the accepted model as the Candidate parent, validates Dataset and artifact bytes, guarantees serving teardown, compares the Candidate against the accepted model score, and emits promotion/rollback handoffs.

## Ownership

This package owns:

- content-addressed model training requests and Candidate artifact contracts;
- exact Run/cycle/parent/Dataset echo checks;
- Dataset and Candidate artifact SHA-256 verification;
- injected train/deploy/evaluate/teardown execution sequencing;
- exact endpoint handoff and teardown in `finally`;
- controller EvidenceRecords for training, Checkpoint, endpoint, evaluation, and teardown;
- strict model improvement, regression, budget, review-pending, promotion, and rollback policy;
- `PROMOTE_MODEL → SLIM_HARNESS` only after a verified promotion-commit observation;
- `ROLLBACK_MODEL → FREEZE_MODEL` only after a verified rollback-commit observation;
- paired Decision/Transition/Snapshot records.

It must not own:

- Trace harvesting or data admission;
- Harness mutation or slimming implementation;
- persistent control transactions, Checkpoint bundles, or Peak CAS;
- reviewer authentication or approval-store implementation;
- production provider credentials, GPU jobs, or endpoint mutation;
- root CLI or root integration documentation;
- final Co-Evolution convergence.

PR #11 owns persistence, immutable approval binding, Peak CAS, actual Harness slimming, durable resume, cycle convergence, and `coevolve`.

## Lineage invariants

```text
current.active_checkpoint_id == current.peak_checkpoint_id
candidate.parent_checkpoint_id == current.active_checkpoint_id
candidate.run_id == current.run_id
candidate.cycle == current.cycle
candidate.dataset_id == current.metadata.trace_dataset_id
candidate.dataset_sha256 == current.metadata.trace_dataset_sha256
```

The `TRAIN_MODEL` Snapshot must carry a finite `metadata.active_model_score` in `[0, 1]`. `StateSnapshot.peak_score` remains available to the wider Harness cycle and is not silently reinterpreted as the model score.

## Integrity invariants

Before training:

```text
Trace Dataset path exists
path is not a symlink
path is inside the configured Dataset root when configured
SHA-256(exact Dataset bytes) == request.dataset_sha256
accepted_example_count > 0
```

After training:

```text
Trainer echoes request/Run/cycle/model/parent/Dataset fields
Candidate artifact exists
artifact path is inside configured artifact root
artifact path and entries are not symlinks
controller recomputes file/directory SHA-256
controller hash == Candidate artifact_sha256
```

Worker/provider hashes are claims to verify, not trust anchors.

## Serving and evaluation invariants

```text
serving lease Checkpoint == Candidate Checkpoint
evaluation Checkpoint == Candidate Checkpoint
evaluation parent == accepted model
evaluation endpoint == exact deployed endpoint
teardown deployment and Checkpoint == serving lease
```

Teardown runs in `finally`. If evaluation and teardown both fail, preserve the evaluation exception and add teardown failure context. If teardown alone fails, propagate it.

## Decision invariants

```text
candidate_score > active_model_score + min_improvement
```

Consequences:

- threshold equality rolls back;
- no-improvement Candidate never becomes active;
- regression beyond tolerance emits explicit rollback evidence;
- review-pending does not grant authority;
- approved review must still target the exact strictly improved Candidate;
- denied review rolls back and keeps the accepted model;
- promotion Decision alone does not mutate active/Peak state;
- active/Peak state changes only after a matching promotion-commit observation;
- rollback handoff keeps the accepted model unchanged;
- exact budget equality is allowed; crossing aborts.

## Validation requirements

```text
training request/Candidate content identity
secret metadata rejection
Dataset hash/path/root/symlink checks
Trainer echo substitution
artifact path/hash mutation
endpoint handoff
teardown on success/evaluation failure
dual evaluation/teardown failure preservation
execution EvidenceKind mapping
TRAIN_MODEL → EVALUATE_MODEL
strict promotion and equality rollback
regression rollback
approval pending/approved/denied/substitution
per-stage and total budget matrix
promotion commit identity/score/bundle checks
rollback commit identity checks
PROMOTE_MODEL → SLIM_HARNESS
ROLLBACK_MODEL → FREEZE_MODEL
active/Peak, parent, Dataset, Run, and cycle invariants
deterministic paired control records
parent demo/rsi/audit compatibility
```

Default tests require no network, API key, GPU, Docker daemon, cloud account, or production endpoint.

## Delivery boundary

This PR is an **Implemented component**. It does not persist the policy records, execute Peak CAS, bind the immutable approval store, slim the Harness, or expose `coevolve`.

Do not update root integration truth from this branch. Record PR #11 obligations in the PR body and `docs/model-inner-loop.md`.
