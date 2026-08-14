# AGENTS.md — `src/post_training_rsi/approval/`

This scope narrows the repository and `src/post_training_rsi/` Agent contracts for Human-in-the-Loop approval records, deterministic review samples, immutable decisions, and fail-closed authority checks.

## Read order

1. `../../../AGENTS.md`
2. `../AGENTS.md`
3. `../../../docs/control-plane-contracts.md`
4. `../../../docs/hitl-approval.md`
5. the focused tests for the approval path being changed
6. the current PR task packet and sibling ownership graph

## Ownership

This directory owns:

- approval policy for Dataset acceptance, Checkpoint promotion, and Harness acceptance;
- deterministic content-hash-ranked review samples;
- exact `post-training-rsi.approval/v1` sample, request, and decision records;
- immutable local request/decision persistence with exact replay semantics;
- reviewer-role authorization and finite review deadlines;
- conversion of approval outcomes to `post-training-rsi.control/v1` Evidence and Decision records;
- fail-closed `require_approved` checks for convergence controllers.

This directory must not own:

- Candidate score thresholds, Peak comparison, rollback, or plateau policy;
- Trainer, Evaluator, Teacher, or Serving provider execution;
- Checkpoint bundles, Peak compare-and-swap, or general Lineage transactions;
- CLI composition or interactive user interfaces;
- implicit Git, cloud, model, Dataset, or production endpoint mutation.

## Hard invariants

1. Missing, malformed, mismatched, pending, denied, or expired decisions are not approvals.
2. A decision is bound to the exact request SHA-256 and exact Subject/Action tuple.
3. Dataset and Harness approval request `ACCEPT`; Checkpoint approval requests `PROMOTE`.
4. Request IDs are content-addressed; exact retries are idempotent and conflicting bytes are rejected.
5. One immutable decision file exists per request. A second different review cannot overwrite it.
6. Review samples contain content hashes and review metadata, not raw private Dataset or Benchmark bodies.
7. Sampling is deterministic for the same candidates, seed, subject, and policy.
8. Reviewer roles are explicit and validated before a decision is accepted.
9. Decision timestamps cannot precede requests or exceed finite expiration deadlines.
10. Approval Evidence must not contain credentials, hidden Benchmark bodies, model weights, or unrestricted raw traces.

## Required evidence matrix

For every contract or gate change, add deterministic coverage for applicable cases:

```text
request create | exact replay | conflicting replay
sample determinism | sample min/max bounds | sample tamper
approve | deny | missing | pending | expired
wrong request hash | wrong Subject | wrong Action
unauthorized reviewer role | malformed record | symlink/non-file record
approved Evidence/Decision | denied StopReason.APPROVAL_NOT_GRANTED
```

Tests must run without network, GPU, external identity providers, interactive prompts, or mutable production services. Use deterministic clocks, reviewer IDs, content hashes, and temporary local stores.

## Sibling boundaries

- PR #3 owns RSI Candidate policy.
- PR #4 owns transactional Lineage and Peak persistence.
- PR #5 owns Adapter Runtime and Serving lifecycle.
- PR #6 owns this approval subsystem.
- PR #7 is the only owner allowed to compose all siblings into supported RSI commands and synchronize shared root documentation.
