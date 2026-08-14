# AGENTS.md — `src/post_training_rsi/recovery_activation/`

This package owns a content-bound recovery activation **plan and preflight** boundary. It does not own execution of a live pointer switch.

## Read order

1. `../../../../AGENTS.md`
2. `../../AGENTS.md`
3. `../../../../README.md`
4. `../../../../docs/implementation-status.md`
5. `../../../../docs/coevolution-audit-recovery.md`
6. `../../../../docs/forensic-recovery-bundle.md`
7. `../../../../docs/recovery-activation-plan.md`
8. `../../../../docs/stacked-pr-plan.md`
9. `../../../../tests/AGENTS.md`

## Ownership

This directory owns:

- immutable expected-live, target, and rollback pointer contracts;
- content-bound staged-bundle and strict-audit evidence references;
- content-bound human authority receipts;
- separation-of-duties checks for requester and reviewer identities;
- deterministic activation-plan identity and SHA-256;
- finite plan and authority expiry;
- local reviewer-role allowlists;
- exact preflight comparison against explicit observations;
- `READY_FOR_HUMAN_EXECUTION` reports with `executed=false`;
- a package-local `build`, `verify`, and `preflight` CLI.

It must not own:

- an `activate`, `apply`, `switch`, `resume`, or `rollback` command;
- mutation of the live workspace or an external routing pointer;
- creation of approval decisions or authentication of a reviewer;
- removal of retained locks;
- automatic generation selection;
- remote storage, encryption, retention, or legal hold;
- model, Harness, Dataset, Checkpoint, or serving-provider side effects;
- Git, Git Town, issue, or Pull Request mutations;
- production disaster-recovery authority.

## State Machine

```text
REQUEST_RECEIVED
  → AUTHORITY_BOUND
  → PLAN_BUILT
  → PLAN_VERIFIED
  → OBSERVATION_RECEIVED
  → PREFLIGHT_VERIFIED
  → READY_FOR_HUMAN_EXECUTION
```

Failure from any edge is terminal for the invocation. There is no transition from `READY_FOR_HUMAN_EXECUTION` to `ACTIVE` in this package.

## Hard invariants

```text
target generation != expected live generation
rollback pointer == expected live pointer
reviewer != requester
authority bundle == staged bundle
authority expected/target generations == plan expected/target generations
staged audit status == PASS
target workspace URI == strictly audited staged root URI
target workspace URI != live workspace URI
plan validity <= local policy TTL
plan validity <= authority expiry
preflight current live pointer == expected live pointer
all observed hashes and URIs exactly match the plan
successful report always has executed=false
```

## Authority boundary

`RecoveryAuthorityReceipt` is a deterministic representation of already-existing authority evidence. The package does not create or authenticate the underlying decision. Production use must independently prove:

```text
identity-provider authentication
organization role assignment
MFA / quorum where required
immutable decision bytes
recovery ticket authorization
separation of duties
non-expired authority
```

A syntactically valid receipt is not itself proof that these external controls occurred.

## Data and privacy rules

- Do not include secrets, credentials, tokens, private keys, raw model weights, raw private traces, or hidden reasoning in plan metadata.
- Network destination URIs are rejected by the reference contract.
- Plans may contain sensitive internal paths and incident identifiers; never transmit them to another provider without explicit data-and-destination authorization.
- CLI errors must remain structured and must not print private artifact bytes.

## Required tests

Changes must cover, as applicable:

```text
content-addressed plan and receipt identity
exact serialization fields
requester/reviewer separation
reviewer-role allowlist
finite TTL and expiry
rollback equality
bundle/audit/decision/target binding
stale live pointer rejection
unknown-field rejection
secret-like metadata rejection
network URI rejection
structured exit code 2
existing output preservation
absence of activate/apply/switch commands
executed=false on every success path
```

Tests require no network, API key, cloud account, GPU, Docker daemon, or mutable production service.

## Successor boundary

A future execution PR may consume a successful preflight report only after separate human authorization. Minimum execution contract:

```text
expected live generation compare-and-swap
exact plan_id and plan SHA-256
exact authority receipt and decision SHA-256
exact bundle_id and strict staged-audit SHA-256
single atomic external pointer update
immutable activation receipt
rollback pointer preservation
post-switch strict audit
explicit writer-resume authorization
```

That successor must remain a separate PR and command surface. This package intentionally stops before any side effect.
