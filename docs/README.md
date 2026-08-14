# Documentation index

This directory separates supported runtime truth, implemented components, exact schemas, target architecture, and PR delivery metadata.

## Required Agent read path

```text
../AGENTS.md
  → ../README.md
  → closest scoped AGENTS.md
  → docs/README.md
  → architecture-manifest.json
  → implementation-status.md
  → state-machine.md
  → rsi-convergence.md
  → relevant component contract
  → traceability-index.md
  → stacked-pr-plan.md
```

The machine-readable directory → State Machine → evidence → PR index is [`architecture-manifest.json`](architecture-manifest.json).

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
| [`architecture-manifest.json`](architecture-manifest.json) | machine-readable supported commands, State Machines, directory ownership, evidence vocabulary, artifact index, Draft PR graph, validation index, and non-claims |
| [`implementation-status.md`](implementation-status.md) | exact branch/PR status, supported commands, validation evidence, and open gaps |
| [`state-machine.md`](state-machine.md) | current states, transition guards, durable records, terminal precedence, and Co-Evolution flow |
| [`rsi-convergence.md`](rsi-convergence.md) | supported PR #7 RSI controller, data/evidence flow, resume semantics, CLI, and artifact layout |
| [`coevolution-convergence.md`](coevolution-convergence.md) | PR #11 deterministic Co-Evolution reference composition, durable resume, pointer history, approval boundaries, and non-claims |
| [`provider-preflight.md`](provider-preflight.md) | fail-closed provider admission checks, backend classification, redaction rules, and the destination-authorization receipt schema |
| [`traceability-index.md`](traceability-index.md) | requirement → code → test → artifact → PR → status mapping |
| [`stacked-pr-plan.md`](stacked-pr-plan.md) | actual PR graph, molecular successors, allowed paths, collision ownership, and Git Town admission |

## Exact component contracts

| Document | Component boundary |
|---|---|
| [`control-plane-contracts.md`](control-plane-contracts.md) | `post-training-rsi.control/v1` State/Event/Decision/Evidence records |
| [`rsi-loop-policy.md`](rsi-loop-policy.md) | strict historical-Peak, reject, rollback, plateau, max-iteration, and budget policy |
| [`adapter-runtime.md`](adapter-runtime.md) | strict provider selection, command protocol, artifact integrity, and serving lifecycle |
| [`lineage-runtime.md`](lineage-runtime.md) | immutable control transactions, Checkpoint bundles, Peak CAS, and quarantine markers |
| [`hitl-approval.md`](hitl-approval.md) | deterministic sampling, immutable review Decisions, authority validation, and fail-closed gates |
| [`harness-outer-loop.md`](harness-outer-loop.md) | frozen-model Harness mutation, validation, benchmark, approval, strict acceptance, and plateau handoff |
| [`trace-harvesting.md`](trace-harvesting.md) | successful observable Trace selection, common verification gates, immutable Trace Dataset, and training handoff |
| [`model-inner-loop.md`](model-inner-loop.md) | exact Dataset/artifact/endpoint integrity, model decision policy, approval, promotion/rollback commit separation |

## Target and production documents

| Document | Purpose |
|---|---|
| [`architecture.md`](architecture.md) | source-derived RSI and Model/Harness Co-Evolution architecture; target sections remain explicitly labeled |
| [`integration-contracts.md`](integration-contracts.md) | provider-neutral boundaries and fail-closed composition obligations |
| [`productionization.md`](productionization.md) | real API/GPU/sandbox/identity/storage/operations prerequisites and non-claims |

## Current branch boundary

Draft PR #11 on `feat/coevolution-convergence` supports deterministic local reference commands:

```text
demo
rsi
coevolve
verify
audit
approvals
review
```

Support here means reachable on the Draft branch and covered by deterministic no-network/no-GPU evidence. It does not claim real Teacher API inference, gradient training, production serving, production benchmark validity, enterprise identity, distributed transactions, autonomous Git mutation, or Git Town.

Exact component and convergence records are indexed under [`validation/`](validation/INDEX.md). A `PENDING` record must never be presented as a passing gate.

## Structural documentation update set

A change to a State, Event, StopReason, Decision, Evidence kind, schema field, transition guard, resume rule, artifact path, approval rule, Peak rule, CLI command, directory owner, or PR dependency must update:

```text
../README.md
architecture-manifest.json
implementation-status.md
state-machine.md
rsi-convergence.md or coevolution-convergence.md
relevant component document
traceability-index.md
stacked-pr-plan.md
closest scoped AGENTS.md
validation index / exact-head record when verification changes
```

Documentation must identify the exact branch and commit that supports a claim. Target diagrams must be labeled target, not current.

## PR #12 current audit boundary

- [`architecture-manifest.json`](architecture-manifest.json) — machine-readable current command, State Machine, directory, artifact, validation, and PR graph contract.
- [`integration-contracts.md`](integration-contracts.md) — provider-neutral handoff and identity propagation rules.
- [`coevolution-convergence.md`](coevolution-convergence.md) — supported deterministic `coevolve` reference runtime.
- [`coevolution-audit-recovery.md`](coevolution-audit-recovery.md) — read-only `coevolve-status` and `coevolve-audit`, strict exit semantics, and human recovery playbook.

Current branch commands include `demo`, `rsi`, `verify`, `audit`, `approvals`, `review`, `coevolve`, `coevolve-status`, and `coevolve-audit`.

<!-- PR13_FORENSIC_RECOVERY_INDEX_START -->
## Forensic recovery successor

| Document | Purpose |
|---|---|
| [`forensic-recovery-bundle.md`](forensic-recovery-bundle.md) | content-addressed local export, exact verification, and inactive staged restore |
| [`forensic-recovery-manifest.json`](forensic-recovery-manifest.json) | machine-readable PR-13 identity, State Machine, paths, invariants, and human-owned boundary |

PR #13 follows the read-only audit boundary. It implements no automatic repair or production activation.
<!-- PR13_FORENSIC_RECOVERY_INDEX_END -->
