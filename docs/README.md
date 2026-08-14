# Documentation index

This directory separates supported runtime truth, implemented components, exact schemas, target architecture, and PR delivery metadata.

## Required Agent read path

```text
../AGENTS.md
  → ../README.md
  → closest scoped AGENTS.md
  → docs/README.md
  → implementation-status.md
  → state-machine.md
  → rsi-convergence.md
  → relevant component contract
  → traceability-index.md
  → stacked-pr-plan.md
```

## Status vocabulary

- **Supported** — reachable from a checked-in CLI/API path and covered by deterministic evidence.
- **Implemented component** — coded and tested but not composed into the supported runtime.
- **Contract only** — schema or protocol exists without executable behavior.
- **Partial** — some required edges/evidence are missing.
- **Planned** — target only.
- **Verified** — exact commit and named gate passed.
- **Not verified** — required execution evidence is absent.

Do not infer support from an enum value, dataclass, diagram, fixture, or isolated unit test.

## Current truth documents

| Document | Purpose |
|---|---|
| [`implementation-status.md`](implementation-status.md) | exact branch/PR status, supported commands, validation evidence, and open gaps |
| [`state-machine.md`](state-machine.md) | current states, transition guards, durable records, terminal precedence, and Co-Evolution target |
| [`rsi-convergence.md`](rsi-convergence.md) | supported PR #7 controller, data/evidence flow, resume semantics, CLI, and artifact layout |
| [`traceability-index.md`](traceability-index.md) | requirement → code → test → artifact → PR → status mapping |
| [`stacked-pr-plan.md`](stacked-pr-plan.md) | actual PR graph, proposed molecular successors, allowed paths, collision ownership, and Git Town admission |

## Exact component contracts

| Document | Component boundary |
|---|---|
| [`control-plane-contracts.md`](control-plane-contracts.md) | `post-training-rsi.control/v1` State/Event/Decision/Evidence records |
| [`rsi-loop-policy.md`](rsi-loop-policy.md) | strict historical-Peak, reject, rollback, plateau, max-iteration, and budget policy |
| [`adapter-runtime.md`](adapter-runtime.md) | strict provider selection, command protocol, artifact integrity, and serving lifecycle |
| [`lineage-runtime.md`](lineage-runtime.md) | immutable control transactions, Checkpoint bundles, Peak CAS, and quarantine markers |
| [`hitl-approval.md`](hitl-approval.md) | deterministic sampling, immutable review Decisions, authority validation, and fail-closed gates |

## Target and production documents

| Document | Purpose |
|---|---|
| [`architecture.md`](architecture.md) | target PDF-derived RSI and Model/Harness Co-Evolution architecture |
| [`integration-contracts.md`](integration-contracts.md) | provider-neutral boundaries where present; verify against current code before relying on it |
| [`productionization.md`](productionization.md) | real API/GPU/sandbox/identity/storage/operations prerequisites and non-claims |

## Current branch boundary

Draft PR #7 on `feat/rsi-convergence` supports:

```text
demo
rsi
verify
audit
approvals
review
```

It does not yet support `coevolve`, and it is not merged to `main`.

Validated code head before the latest documentation commits:

```text
ac334be8411f45196d2522c885ff893cb2d44fda
```

The repair-and-validation workflow for that code head passed compile, Ruff, mypy, full pytest with coverage, compatibility demo, converged RSI, and Checkpoint audit smoke. The exact current documentation head still requires its own normal PR check set before PR #7 can leave Draft.

## Structural documentation update set

A change to a State, Event, StopReason, Decision, Evidence kind, schema field, transition guard, resume rule, artifact path, approval rule, Peak rule, CLI command, directory owner, or PR dependency must update:

```text
../README.md
implementation-status.md
state-machine.md
rsi-convergence.md          # supported runtime changes
relevant component document
traceability-index.md
stacked-pr-plan.md           # branch/merge changes
closest scoped AGENTS.md     # ownership changes
```

Documentation must identify the exact branch and commit that supports a claim. Target diagrams must be labeled target, not current.
