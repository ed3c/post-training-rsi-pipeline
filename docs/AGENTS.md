# AGENTS.md — `docs/`

This file narrows the repository root [`AGENTS.md`](../AGENTS.md) for architecture, status, State Machine, traceability, production, and Pull Request documentation.

## Required read order

1. `../AGENTS.md`
2. `../README.md`
3. `README.md`
4. `implementation-status.md`
5. `state-machine.md`
6. `rsi-convergence.md`
7. the component document being edited
8. `traceability-index.md`
9. `stacked-pr-plan.md`

## Truth rules

- Separate **Supported**, **Implemented component**, **Contract only**, **Partial**, **Planned**, **Verified**, and **Not verified**.
- State whether a claim applies to `main`, a Draft PR branch, or an isolated component branch.
- Do not convert a diagram, enum, schema, fixture, component test, or earlier green commit into a current supported-runtime claim.
- Use exact branch names, commit SHAs, PR numbers, State/Event/StopReason names, schema versions, commands, paths, and evidence filenames.
- Record the exact commit and validation source for every Verified claim.
- When code and prose disagree, update prose or land code/tests in the same structural PR. Never preserve stale prose by weakening code.

## Document ownership

| Document | Owns |
|---|---|
| `implementation-status.md` | exact current branch truth, validation evidence, open gaps, explicit non-claims |
| `state-machine.md` | supported/target states, guards, precedence, evidence, resume rules |
| `rsi-convergence.md` | PR #7 controller, CLI, data flow, artifacts, failure/resume semantics |
| `control-plane-contracts.md` | exact serialized `post-training-rsi.control/v1` contract |
| `rsi-loop-policy.md` | strict Candidate decision semantics |
| `adapter-runtime.md` | provider selection, process contract, artifact integrity, lifecycle |
| `lineage-runtime.md` | transaction, Checkpoint bundle, Peak CAS, quarantine |
| `hitl-approval.md` | sampling, request/decision, authority and fail-closed review |
| `traceability-index.md` | requirement → code → test → artifact → PR → status |
| `stacked-pr-plan.md` | actual/proposed PR graph, allowed paths, collision ownership, gates, Git Town admission |
| `architecture.md` | target design only |
| `productionization.md` | production prerequisites, risks, and non-claims |

## Structural update set

A change to a State, Event, StopReason, Decision/Evidence field, schema version, transition guard, precedence, resume rule, artifact path, approval rule, Peak rule, CLI command, directory owner, or PR dependency requires synchronized updates to all relevant documents:

```text
../README.md
implementation-status.md
state-machine.md
rsi-convergence.md          # supported runtime changes
relevant component document
traceability-index.md
stacked-pr-plan.md          # delivery graph changes
closest scoped AGENTS.md    # ownership changes
```

Do not update only one diagram.

## Link and index discipline

- Every new architecture/status/component document must be linked from `docs/README.md`.
- Every new supported command must be linked from `README.md` and `rsi-convergence.md`.
- Every new requirement needs a stable traceability ID.
- Every new PR slice needs parent, allowed paths, excluded paths, state edges, required gates, collision paths, rebase owner, rollback subject, and human-owned operations.
- Do not use raw local absolute paths in repository documentation.

## Diagram discipline

A supported State diagram must show:

```text
failure edges
budget/circuit edges
approval edges
reject/rollback edges
terminal StopReasons
resume source
teardown where relevant
```

A target diagram must be labeled Target and must not appear in the current-capability table as Supported.

## Validation claims

Before writing “Verified,” confirm:

```text
exact commit SHA
exact workflow/run or local environment
commands/gates
pass/fail result
known skips
whether the run tested branch head or a synthetic merge ref
```

A PR workflow with `action_required` and no jobs is a permission state, not a pass and not a code failure.

## Git Town language

Git Town is not configured. Refer to the current graph as an ordinary GitHub PR graph. Proposed Stack metadata is documentation-only until the admission gate in `stacked-pr-plan.md` is satisfied.
