# Integration status and requirements

## Snapshot

| Field | Value |
|---|---|
| Snapshot date | `2026-08-14` |
| Baseline branch | `feat/pdf-architecture` |
| Baseline commit | `2fa9a8d9746ae5dccd5ff68d78b3a7d75e7c43be` |
| Documentation parent | PR `#1`, `docs/agent-state-machine-index` |
| Contract child | PR `#2`, `feat/state-domain-contracts` |
| RSI policy child | PR `#3`, `feat/rsi-loop-policy` |
| Package version | `0.2.0` |
| Supported CLI | `post-training-rsi ... demo` |
| Latest baseline CI | GitHub Actions `CI` run `31735375681`, successful |
| Control schema | `post-training-rsi.control/v1`, Contract only in supported runtime |
| Candidate policy | Implemented component; not called by `RSIEngine` |
| Git Town | Not configured; no repository config, version pin, or active stack manifest |

This snapshot describes checked-in GitHub code and the ordinary GitHub stacked PRs above, not a separate local package or design bundle.

## What is integrated now

| Capability | Status | Evidence in repository | Runtime truth |
|---|---|---|---|
| Configuration and threshold validation | Implemented | `config.py`, `tests/test_config.py` | Loaded by `demo` |
| Versioned control-plane contracts | Contract only | `control_plane/`, `tests/test_control_plane.py`, `control-plane-contracts.md` | Strict State/Event/Stop/Decision/Evidence records exist; `RSIEngine` does not emit them |
| RSI candidate decision policy | Implemented component | `orchestration/rsi_policy.py`, `tests/test_rsi_policy.py`, `rsi-loop-policy.md` | Strict Peak, parent, rollback, plateau, max-iteration, and budget records work in isolation; runtime not wired |
| Per-iteration and total cost ledger | Implemented component | `cost.py`, `tests/test_cost.py` | Generation charge is enforced; external-stage costs are not wired |
| Deterministic synthesis fixture | Implemented | `generation.py`, `tests/test_engine.py` | Used by `demo` |
| Teacher protocol/OpenAI-compatible client | Contract only | `synthesis/runtime.py`, `synthesis/teacher.py` | Not selected by CLI/config |
| Diversity, decontamination, safety, static verification | Implemented | `verification/`, `tests/test_verification.py` | Used by `demo` |
| Acceptance-rate threshold | Partial | configured in `VerificationConfig` | Engine only checks whether accepted set is empty |
| Mock training | Implemented | `training/adapter.py`, adapter tests | Used by `demo` |
| External command trainer | Contract only | `CommandTrainer`, adapter tests | Not selected from config |
| Local serving adapter | Implemented | `serving/adapter.py` | Deploy-only local URI is used |
| External serving adapter | Contract only | `CommandServingAdapter`, adapter tests | No lifecycle/undeploy wiring |
| Deterministic evaluation | Implemented | `evaluation/adapter.py` | Used by `demo` |
| External command evaluator | Contract only | `CommandEvaluator`, adapter tests | Not selected from config |
| Iteration artifact bundle | Implemented | `lineage/store.py`, lineage tests | Raw/accepted/quarantine/audit/summary are written |
| Checkpoint lineage manifest schema | Implemented component | `lineage/manifest.py`, lineage tests | `RSIEngine` does not create/persist it |
| Peak checkpoint persistence | Implemented component | `ArtifactStore.write_peak/load_peak` | Not called by `RSIEngine` |
| Five-stage recursive RSI loop | Partial | `engine.py`; isolated decision policy in `orchestration/` | One hard-coded runtime iteration; no recursive diagnose/hypothesis composition |
| Peak comparison and rollback | Implemented component | `RSIDecisionPolicy` | Not reachable from supported CLI/runtime |
| Plateau/early stopping | Implemented component | `RSIDecisionPolicy`, schema-v1 stop records | Not reachable from supported CLI/runtime |
| Regression audit CLI | Planned | README target and evidence kind contract | `audit` command is absent |
| Model/Harness co-evolution | Planned | control state/event contracts, config fields, docs | `harness/` contains only `__init__.py`; `coevolve` command is absent |
| Human approval boundary | Planned | decision/action/evidence contracts and production checklist | No approval store, state transition, or CLI |
| DVC/lakeFS/MLflow mirrors | Planned | production checklist | Local JSON is the only store |

## Reachable current state machine

The checked-in `demo` path is unchanged by PR #2 and PR #3:

```text
CONFIG_LOADED
  -> SYNTHESIZED
  -> BUDGET_CHARGED
  -> VERIFIED
  -> DATA_REJECTED | TRAINED
  -> DEPLOYED
  -> EVALUATED
  -> COMPLETED
```

The pure PR #3 policy can evaluate an `EVALUATE` snapshot and emit:

```text
EVALUATE
  -> PROMOTED -> DIAGNOSE | STOPPED(MAX_ITERATIONS)
  -> REJECTED -> DIAGNOSE | STOPPED(PLATEAU/MAX_ITERATIONS)
  -> ROLLED_BACK(REGRESSION_ROLLBACK)
  -> ABORTED(PER_ITERATION_BUDGET_EXCEEDED/TOTAL_BUDGET_EXCEEDED)
```

That second graph is component behavior, not a supported end-to-end runtime path.

The following target states remain uncomposed or entirely unimplemented:

```text
DIAGNOSE, HYPOTHESIS, SYNTHESIZE, VERIFY, DATA_REVIEW_PENDING,
DECIDE, MODEL_REVIEW_PENDING, MUTATE_HARNESS, VALIDATE_HARNESS,
EVALUATE_HARNESS, HARVEST_TRACES, VERIFY_TRACES, TRAIN_MODEL,
EVALUATE_MODEL, PROMOTE_MODEL, ROLLBACK_MODEL, SLIM_HARNESS
```

A value existing in `ControlState` or a pure policy test is not evidence that the supported CLI reaches it.

## Critical integration mismatches

1. `README.md` previously advertised `coevolve` and `audit`, while `__main__.py` only registers `demo`.
2. `RSIConfig.max_iterations`, `plateau_patience`, and `min_improvement` remain unused by `RSIEngine.run()` even though PR #3 can consume them through `RSIPolicyLimits.from_config()`.
3. `RSIEngine.run_once()` sets `parent_checkpoint_id=None`, sets `promoted=True`, and sets `peak_score=candidate_score` without calling `RSIDecisionPolicy`.
4. `ArtifactStore.write_checkpoint()`, `write_peak()`, `load_peak()`, and `LineageManifest.create()` are not connected to the runnable engine or schema-v1 policy records.
5. The engine deploys a checkpoint but does not pass the returned endpoint into the evaluator or tear it down.
6. `harness/` is a namespace placeholder, so no outer-loop mutation or middle-loop trace harvesting exists.
7. `make coevolve` invokes an unsupported CLI command and is intentionally classified as a red target.
8. The current engine still returns free-form string statuses and legacy result dataclasses rather than `StateSnapshot`, `TransitionRecord`, and `DecisionRecord`.
9. PR #3 regression tolerance is an explicit policy input; production calibration and configuration ownership remain human-reviewed work.

## Required next outcomes

| Gap ID | Requirement | Current progress | Exit condition |
|---|---|---|---|
| `GAP-CTRL-01` | Adopt shared control records | schema and pure policy records exist | supported controllers emit them; lineage persists them; E2E tests assert evidence IDs |
| `GAP-RSI-01` | Multi-iteration controller | decision boundary implemented | diagnose/hypothesis/synthesis/verify/train/serve/evaluate composition exercises `max_iterations` and stop reasons |
| `GAP-RSI-02` | Peak/promotion/rollback policy | pure component implemented | runtime uses strict `score > peak + min_improvement`; rejected model never becomes parent |
| `GAP-RSI-03` | Plateau and budget termination | pure component implemented | runtime emits terminal evidence for plateau, per-iteration budget, total budget, and provider circuit open |
| `GAP-LIN-01` | Runtime lineage integration | record schemas exist | every candidate writes checkpoint/control records and complete `LineageManifest`; Peak pointer is atomic |
| `GAP-CLI-01` | Supported operational commands | none | `verify`, `audit`, and `coevolve` are registered, documented, and smoke-tested |
| `GAP-ADP-01` | Adapter selection | protocols exist | strict config selects Teacher/trainer/evaluator/safety/serving without controller edits |
| `GAP-SRV-01` | Serving lifecycle | deploy exists | endpoint reaches evaluator and teardown always runs in `finally` |
| `GAP-HITL-01` | Fail-closed approvals | vocabulary exists | Dataset sample and Model/Harness promotion pause/resume through immutable decisions |
| `GAP-COEV-01` | Harness outer loop | state vocabulary only | mutation, static validation, sandbox evaluation, acceptance, and plateau transitions exist |
| `GAP-COEV-02` | Trace harvest and model inner loop | state vocabulary only | successful traces pass verification, train a model, compare it, hot-swap or rollback, and reset outer loop |
| `GAP-OPS-01` | Production evidence | deterministic boundary exists | real adapters remain opt-in; no secret/GPU/network requirement enters deterministic CI |

## Definition of integrated

A feature is integrated only when all conditions hold:

- reachable through a supported runtime or CLI path;
- guarded by typed/validated inputs;
- emits durable evidence for every decision;
- uses the shared control-plane schema rather than free-form duplicate taxonomies;
- has deterministic positive, negative, and rollback tests;
- appears in the directory/state map and traceability index;
- does not require network, API keys, or GPUs in the default CI path;
- has a documented human-owned boundary for destructive or production-impacting operations.

An **Implemented component** satisfies code/test requirements inside its boundary but remains below **Integrated** until composition and persistence are verified.
