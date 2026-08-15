<!-- i18n-key: README; locale: en; reviewed: 2026-08-15 -->
[English](README.md) · [繁體中文](README.zh-TW.md)

# Post-Training RSI Pipeline

[![CI](https://github.com/ed3c/post-training-rsi-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/ed3c/post-training-rsi-pipeline/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](pyproject.toml)

**An evidence-first reference pipeline for post-training data, Recursive Self-Improvement (RSI), and Model/Harness Co-Evolution.**

> **Maturity:** alpha reference implementation. The deterministic local path is supported. Real Teacher APIs, GPU training, external serving, production benchmarks, automatic release, and autonomous self-modification are not verified by the repository unless an exact run publishes the required evidence.

## Why this project exists

Post-training systems fail when they treat generated data, training, deployment, evaluation, model promotion, Harness changes, and recovery as one opaque loop. A score increase alone cannot prove that the data was admissible, the candidate came from the declared parent, the benchmark was comparable, or the promoted artifact can be recovered.

This project makes those transitions explicit:

```text
diagnose
→ form a data hypothesis
→ synthesize candidate data
→ verify and optionally review the dataset
→ train a candidate
→ serve in a bounded adapter lifecycle
→ evaluate against declared benchmarks
→ approve, reject, roll back, or stop
→ persist lineage, decisions, checkpoints, and audit evidence
```

A separate Co-Evolution controller freezes one side while changing the other, captures observable traces, and preserves rollback and stop conditions.

## Core capabilities

| Area | What the repository provides |
|---|---|
| Data contracts | Typed datasets, source identity, verification results, budgets, and immutable control records |
| RSI controller | Resumable multi-iteration State Machine with promotion, rejection, rollback, plateau, and stop rules |
| Lineage | Atomic checkpoint bundles, transactions, parent/Peak continuity, quarantine, and compare-and-swap promotion |
| Human review | Content-addressed dataset/checkpoint requests and immutable approve/deny decisions |
| Provider boundary | Strict mock/command adapters, bounded execution, artifact recomputation, endpoint teardown, and fail-closed preflight |
| Co-Evolution | Frozen-model Harness search, trace harvesting, model inner loop, convergence rules, durable resume, and audit |
| Recovery | Read-only status, integrity audit, forensic bundle, and explicit recovery activation planning |
| Evidence | Exact hashes, run IDs, decisions, transactions, artifacts, pointers, and machine-readable architecture mapping |

The exact supported, component-only, planned, and externally unverified state is maintained in [`docs/implementation-status.md`](docs/implementation-status.md). Historical branch or Pull Request overlays are delivery records, not the current `main` contract.

## Architecture

```mermaid
flowchart LR
    A[BOOT] --> B[DIAGNOSE]
    B --> C[HYPOTHESIS]
    C --> D[SYNTHESIZE]
    D --> E[VERIFY]
    E -->|admissible| F[TRAIN]
    E -->|invalid| Q[QUARANTINED]
    F --> G[SERVE]
    G --> H[EVALUATE]
    H --> I[DECIDE]
    I -->|strict improvement + authority| P[PROMOTED]
    I -->|no improvement| R[REJECTED]
    I -->|regression| X[ROLLED BACK]
    P --> B
    R --> B
    P --> S[STOPPED]
    R --> S
```

Model/Harness Co-Evolution:

```text
FREEZE_MODEL
→ MUTATE_HARNESS
→ HARVEST_TRACES
→ TRAIN_MODEL
→ PROMOTE_MODEL or ROLLBACK_MODEL
→ SLIM_HARNESS
→ next bounded cycle or STOPPED
```

See [`docs/state-machine.md`](docs/state-machine.md), [`docs/rsi-convergence.md`](docs/rsi-convergence.md), and [`docs/coevolution-convergence.md`](docs/coevolution-convergence.md).

## Quick start

### Requirements

- Python 3.11+
- Git
- No cloud or GPU dependency for the deterministic reference path

```bash
git clone https://github.com/ed3c/post-training-rsi-pipeline.git
cd post-training-rsi-pipeline

python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

make lint
make typecheck
make test
make demo
```

Run or resume the deterministic RSI controller:

```bash
post-training-rsi   --config configs/pipeline.example.json   --workspace artifacts/rsi   --run-id run-local-001   rsi
```

Run the deterministic Co-Evolution reference:

```bash
make coevolve
```

Discover the command surface:

```bash
post-training-rsi --help
```

Optional extras exist for cloud adapters, semantic models, experiment tracking, LangGraph, and training libraries. Installing an extra does not prove that an external provider, GPU job, serving endpoint, or production benchmark is admitted.

## Evidence and promotion rules

The controller keeps these concepts separate:

```text
generated data
!= verified dataset
!= reviewed dataset
!= trained candidate
!= evaluated candidate
!= qualified candidate
!= approved promotion
!= active Peak
!= production release
```

Key invariants include:

```text
active_checkpoint_id == peak_checkpoint_id
candidate.parent_checkpoint_id == active_checkpoint_id
candidate_score > peak_score + min_improvement
```

Equality at the improvement boundary is rejection. Rejected or rolled-back candidates never become the next parent. Promotion requires a committed decision bound to the exact checkpoint, and worker-reported artifact hashes are recomputed by the controller.

## Provider and data boundary

Before any configured external destination is used, provider preflight checks adapter type, credential **names**, destination policy, command resolution, budgets, approvals, benchmark requirements, and an authorization receipt bound to the exact configuration and origin.

Do not send private training data, proprietary repository content, customer data, model weights, or credentials to a provider without explicit data-and-destination authorization.

## Repository map

```text
src/post_training_rsi/
├── orchestration/     RSI and Co-Evolution controllers
├── control/           typed State and decision records
├── lineage/           transactions, checkpoints, Peak and recovery
├── adapters/          synthesis, training, serving and evaluation boundaries
├── approvals/         immutable HITL authority
└── audit/             read-only status and integrity evidence

configs/               deterministic policy examples
docs/                  architecture, status, contracts, recovery and traceability
tests/                 transition, tamper, failure and resume coverage
artifacts/             generated local workspaces; not source truth
```

## Documentation

- [Documentation index](docs/README.md)
- [Implementation status](docs/implementation-status.md)
- [Architecture](docs/architecture.md)
- [State Machine](docs/state-machine.md)
- [RSI convergence](docs/rsi-convergence.md)
- [Harness outer loop](docs/harness-outer-loop.md)
- [Model inner loop](docs/model-inner-loop.md)
- [Co-Evolution convergence](docs/coevolution-convergence.md)
- [HITL approval](docs/hitl-approval.md)
- [Provider preflight](docs/provider-preflight.md)
- [Recovery and audit](docs/coevolution-audit-recovery.md)
- [Documentation language policy](docs/I18N.md)
- [Open-source readiness checklist](docs/OPEN_SOURCE_CHECKLIST.md)

## Non-goals

This repository does not claim:

- that recursive improvement will continue, converge, or outperform a baseline;
- that synthetic data is correct merely because it was generated;
- that a local or mock run represents a real cloud/GPU run;
- that an automated score authorizes model promotion, release, or deployment;
- that external provider terms, privacy, security, or legal requirements are satisfied automatically.

## Contributing, security, and governance

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing state, lineage, provider, or evidence semantics. Report vulnerabilities through [SECURITY.md](SECURITY.md). Support and authority boundaries are documented in [SUPPORT.md](SUPPORT.md) and [GOVERNANCE.md](GOVERNANCE.md).

## License

Licensed under the [MIT License](LICENSE). Third-party models, datasets, provider services, and dependencies remain subject to their own terms.
