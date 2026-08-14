# AGENTS.md — Model/Harness Co-Evolution convergence

Read, in order:

1. repository `AGENTS.md`;
2. `src/post_training_rsi/AGENTS.md`;
3. `README.md`;
4. `docs/state-machine.md`;
5. `docs/harness-outer-loop.md`;
6. `docs/trajectory-harvesting.md`;
7. `docs/model-inner-loop.md`;
8. `docs/coevolution-convergence.md`;
9. `docs/traceability-index.md`;
10. `docs/stacked-pr-plan.md`.

## Ownership

This directory owns composition and resumable cycle control across the accepted Model/Checkpoint, fixed-model Harness outer loop, successful observable trajectory harvesting, verified-trace Model inner loop, verified promotion handoff, Harness slimming, counter reset, and cycle termination.

It must not reimplement the component policies owned by `harness/`, `trajectory/`, or `model_loop/`. It must not collect hidden chain-of-thought, provision production credentials, mutate managed production endpoints by default, merge pull requests, enable Git Town, or bypass Human-in-the-Loop gates.

## Invariants

- Every cycle starts from one accepted Checkpoint and one accepted Harness.
- Harness search freezes the Model and Checkpoint for that cycle.
- Trace harvesting binds to the accepted Harness and frozen Checkpoint that produced the successful executions.
- Model training consumes only the verified Trace Dataset from the same cycle lineage.
- Hot-swap is allowed only after a verified `PROMOTE_MODEL` recommendation and complete immutable evidence.
- A rollback recommendation leaves the accepted Checkpoint unchanged.
- Harness slimming occurs only after successful promotion and preserves mandatory safety, tool, verification, retry, timeout, and termination constraints.
- Resume must not repeat already committed Trainer, Evaluator, or hot-swap side effects.
- Candidate artifacts and latest artifacts are not aliases for accepted artifacts.
- Missing, malformed, stale, cross-cycle, cross-Run, or uncommitted evidence fails closed.

## Success boundary

A Draft PR and a passing local helper are not production approval. Keep the PR Draft until exact-head pull-request CI and human architecture review are complete.