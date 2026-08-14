# Integration status and requirements

## Snapshot

| Field | Value |
|---|---|
| Snapshot date | `2026-08-14` |
| Baseline branch | `feat/pdf-architecture` |
| Baseline commit | `2fa9a8d9746ae5dccd5ff68d78b3a7d75e7c43be` |
| Documentation parent | PR `#1`, `docs/agent-state-machine-index` |
| Contract child | PR `#2`, `feat/state-domain-contracts` |
| Package version | `0.2.0` |
| Supported CLI | `post-training-rsi ... demo` |
| Latest baseline CI | GitHub Actions `CI` run `31735375681`, successful |
| Control schema | `post-training-rsi.control/v1`, Contract only |
| Git Town | Not configured; no repository config, version pin, or active stack manifest |

This snapshot describes checked-in GitHub code and the ordinary GitHub stacked PRs above, not a separate local package or design bundle.

## What is integrated now

| Capability | Status | Evidence in repository | Runtime truth |
|---|---|---|---|
| Configuration and threshold validation | Implemented | `config.py`, `tests/test_config.py` | Loaded by `demo` |
| Versioned control-plane contracts | Contract only | `control_plane/`, `tests/test_control_plane.py`, `control-plane-contracts.md` | Strict state/event/stop/decision/evidence records exist; `RSIEngine` does not import or emit them |
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
| Five-stage recursive RSI loop | Partial | `engine.py` | One hard-coded iteration; no recursive diagnose/hypothesis loop |
| Peak comparison and rollback | Planned | config/model fields, control contracts, architecture docs | Candidate is currently always marked promoted |
| Plateau/early stopping | Planned | config fields and `StopReason.PLATEAU` contract | No reachable transition uses it |
| Regression audit CLI | Planned | README target and evidence kind contract | `audit` command is absent |
| Model/Harness co-evolution | Planned | control state/event contracts, config fields, docs | `harness/` contains only `__init__.py`; `coevolve` command is absent |
| Human approval boundary | Planned | decision/action/evidence contracts and production checklist | No approval store, state transition, or CLI |
| DVC/lakeFS/MLflow mirrors | Planned | production checklist | Local JSON is the only store |

## Reachable current state machine

The checked-in `demo` path is unchanged by PR #2:

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

The following target states are represented by the shared enum but are not reachable yet:

```text
DIAGNOSE, HYPOTHESIS, SYNTHESIZE, VERIFY, DATA_REVIEW_PENDING,
DECIDE, MODEL_REVIEW_PENDING, PROMOTED, REJECTED, ROLLED_BACK,
STOPPED, ABORTED, MUTATE_HARNESS, VALIDATE_HARNESS,
EVALUATE_HARNESS, HARVEST_TRACES, VERIFY_TRACES, TRAIN_MODEL,
EVALUATE_MODEL, PROMOTE_MODEL, ROLLBACK_MODEL, SLIM_HARNESS
```

A value existing in `ControlState` is not evidence that its transition is implemented.

## Critical integration mismatches

1. `README.md` previously advertised `coevolve` and `audit`, while `__main__.py` only registers `demo`.
2. `RSIConfig.max_iterations`, `plateau_patience`, and `min_improvement` are validated but unused by `RSIEngine.run()`.
3. `RSIEngine.run_once()` sets `parent_checkpoint_id=None`, sets `promoted=True`, and sets `peak_score=candidate_score` without comparing a historical peak.
4. `ArtifactStore.write_checkpoint()`, `write_peak()`, `load_peak()`, and `LineageManifest.create()` are not connected to the runnable engine.
5. The engine deploys a checkpoint but does not pass the returned endpoint into the evaluator or tear it down.
6. `harness/` is a namespace placeholder, so no outer-loop mutation or middle-loop trace harvesting exists.
7. `make coevolve` invokes an unsupported CLI command and is intentionally classified as a red target.
8. The current engine still returns free-form string statuses and legacy result dataclasses rather than `StateSnapshot`, `TransitionRecord`, and `DecisionRecord`.

## Required next outcomes

| Gap ID | Requirement | Exit condition |
|---|---|---|
| `GAP-CTRL-01` | Adopt shared control records | Supported controllers emit schema-v1 snapshots/transitions/decisions; lineage persists them and E2E tests assert exact evidence IDs |
| `GAP-RSI-01` | Multi-iteration controller | `max_iterations`, parent checkpoint, deterministic hypothesis input, and stop reason are exercised by E2E tests |
| `GAP-RSI-02` | Peak/promotion/rollback policy | Candidate only promotes when `score > peak + min_improvement`; rejected model never becomes parent |
| `GAP-RSI-03` | Plateau and budget termination | Explicit terminal states and evidence for plateau, per-iteration budget, total budget, and provider circuit open |
| `GAP-LIN-01` | Runtime lineage integration | Every trained candidate writes checkpoint metadata and a complete `LineageManifest`; peak pointer is atomic |
| `GAP-CLI-01` | Supported operational commands | `verify`, `audit`, and `coevolve` are registered, documented, and smoke-tested |
| `GAP-ADP-01` | Adapter selection | Strict config selects Teacher/trainer/evaluator/safety/serving adapters without controller edits |
| `GAP-SRV-01` | Serving lifecycle | Endpoint is provided to evaluator and always torn down in `finally` |
| `GAP-HITL-01` | Fail-closed approvals | Dataset sample and Model/Harness promotion pause/resume through immutable decisions |
| `GAP-COEV-01` | Harness outer loop | Candidate mutation, static validation, sandbox evaluation, acceptance, and plateau transitions exist |
| `GAP-COEV-02` | Trace harvest and model inner loop | Successful traces pass the same verification gates, train a model, compare it, hot-swap or rollback, then reset outer loop |
| `GAP-OPS-01` | Production evidence | Real adapters remain opt-in; no secret/GPU/network requirement enters deterministic CI |

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
