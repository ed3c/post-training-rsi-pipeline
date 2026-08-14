# AGENTS.md — `src/post_training_rsi/harness/outer_loop/`

Read the repository root and `src/post_training_rsi/AGENTS.md` first.

## Purpose

This package owns the **implemented Harness outer-loop component**:

```text
FREEZE_MODEL
  → MUTATE_HARNESS
  → VALIDATE_HARNESS
  → EVALUATE_HARNESS
  → optional HARNESS_REVIEW_PENDING
  → ACCEPT_HARNESS | REJECT_HARNESS
  → MUTATE_HARNESS | HARVEST_TRACES handoff
```

It freezes the accepted model, mutates only non-parametric Harness state, admits Candidates through static/policy validation, evaluates them under the same model/task suite, accepts only strict improvement, and hands off to trace harvesting after plateau or iteration limit.

## Ownership

This package owns:

- immutable Harness, mutation, task, validation, benchmark, and review-observation contracts;
- deterministic content-addressed Harness mutation;
- static/policy validation before evaluation;
- weighted deterministic benchmark aggregation;
- strict Harness improvement, budget, review-pending, accept, reject, and plateau-handoff policy;
- `DecisionRecord`, `TransitionRecord`, and `StateSnapshot` creation for its edges.

It must not own:

- model weight training or promotion;
- trace Dataset transformation or verification;
- persistence of control records or Harness snapshots;
- reviewer authentication or approval-store implementation;
- production provider calls;
- root CLI composition;
- root README/status/traceability synchronization;
- Model/Harness Co-Evolution convergence.

PR #11 owns final composition. PR #9 owns trace harvesting. PR #10 owns the model inner loop.

## Invariants

```text
active_checkpoint_id == peak_checkpoint_id
frozen model Checkpoint never changes during one outer-loop cycle
candidate.parent_harness_id == active_harness_id
candidate_score > active_harness_score + min_improvement
```

Also preserve:

- threshold equality rejects;
- invalid Harness never reaches evaluation;
- rejected/denied Harness never becomes active;
- accepted Harness resets plateau count;
- ordinary rejection increments plateau count;
- plateau or outer-iteration limit hands off to `HARVEST_TRACES`;
- budget crossing aborts; exact limits are allowed;
- review-pending state does not imply approval;
- review evidence cannot target a different Candidate;
- only observable trace URIs and task facts are represented; no hidden chain-of-thought capture;
- tools are unique and policy-validated;
- deterministic mutation identity includes parent, mutation, policy fields, and metadata;
- every policy edge has non-empty evidence IDs and paired Decision/Transition/Snapshot records.

## Validation requirements

```text
contract exactness and round trip
mutation determinism and parent mismatch
static invalid Candidate rejection
tool allowlist and forbidden Prompt checks
weighted aggregate and family scores
runner task/family substitution rejection
strict improvement and equality rejection
approval-required pending/approved/denied
active/frozen model invariants
plateau and iteration-limit handoff
per-iteration and total budget boundaries
record pairing and deterministic identities
no supported CLI claim
```

Default tests must require no network, API key, GPU, Docker daemon, or cloud account.

## Delivery boundary

This PR is an **Implemented component** until a later convergence PR persists its records, binds the existing immutable Harness approval service, snapshots Harness content, invokes real tasks, and connects `HARVEST_TRACES` to PR #9.

Do not edit root integration truth from this component branch. Record required convergence changes in the PR body and `docs/harness-outer-loop.md`.
