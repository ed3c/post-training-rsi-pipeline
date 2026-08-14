# Integration contracts

Status: **Supported reference integration boundary on Draft PR #7**

This document is the cross-component index for the provider-neutral boundaries composed by `feat/rsi-convergence`. It does not claim that a real Teacher API, GPU trainer, production evaluator, live serving endpoint, remote lineage catalog, or enterprise identity provider has been verified.

Read the exact component contracts before changing an integration:

- [`adapter-runtime.md`](adapter-runtime.md) — Teacher, trainer, evaluator, serving, retry, idempotency, result validation, artifact integrity, endpoint handoff, and teardown;
- [`lineage-runtime.md`](lineage-runtime.md) — immutable control transactions, Checkpoint bundles, Peak compare-and-swap, and quarantine markers;
- [`hitl-approval.md`](hitl-approval.md) — deterministic sampling, immutable review Decisions, reviewer-role checks, and fail-closed approval gates;
- [`control-plane-contracts.md`](control-plane-contracts.md) — `post-training-rsi.control/v1` State, Event, Decision, Evidence, Transition, and Snapshot records;
- [`rsi-convergence.md`](rsi-convergence.md) — the supported composition, pause/resume behavior, CLI surface, and artifact graph.

## Composition boundary

```text
PipelineConfig
  → AdapterRuntime factory
  → Teacher synthesis result + cost/idempotency evidence
  → VerificationPipeline + exact accepted-Dataset SHA-256
  → optional Dataset approval
  → Trainer result + controller-recomputed artifact SHA-256
  → Serving deploy + exact endpoint handoff
  → Evaluator result
  → serving teardown in success or failure paths
  → RSI decision policy
  → optional Checkpoint promotion approval
  → immutable control transaction
  → atomic Checkpoint bundle
  → Peak CAS or quarantine/reject/rollback marker
```

`src/post_training_rsi/orchestration/` is the composition owner. Provider adapters do not update Peak state, lineage stores do not decide model quality, and approval records do not bypass verification or score policy.

## Directory ownership

| Boundary | Primary implementation | Contract output | Forbidden responsibility |
|---|---|---|---|
| Runtime selection | `config.py`, `adapter_runtime/factory.py` | selected provider-neutral adapters | State transition policy |
| External process execution | `adapter_runtime/command.py` | validated result envelope and explicit failure | shell interpolation or silent malformed-result acceptance |
| Artifact integrity | `adapter_runtime/integrity.py` | controller-computed file/directory SHA-256 | trusting only a Worker-reported hash |
| Endpoint lifecycle | `adapter_runtime/lifecycle.py` | deploy, endpoint, evaluation, teardown evidence | model promotion |
| Human authority | `approval/` | immutable Request/Decision and control records | reviewer authentication or production RBAC |
| Durable lineage | `lineage/` | committed evidence, Checkpoint bundle, Peak pointer, quarantine marker | score thresholds |
| RSI composition | `orchestration/converged.py` | resumable StateSnapshots and terminal result | provider-specific SDK internals |

## Fail-closed requirements

Integration changes must preserve all of the following:

1. Unknown configuration and result fields are rejected.
2. Command adapters use argument arrays and bounded attempts/timeouts; they do not execute shell command strings.
3. Dataset identity is the SHA-256 of the exact accepted bytes used by training.
4. Candidate artifact identity is recomputed by the controller before Checkpoint commitment.
5. Request, Run, iteration, Subject, parent Checkpoint, model, Dataset, and operation identities must agree across adapter results and control records.
6. Evidence must be committed before a Decision, Transition, or Snapshot depends on it.
7. The transaction manifest is written last and defines committed records.
8. Serving teardown is attempted after successful deployment even when evaluation fails.
9. Missing, pending, denied, expired, malformed, unauthorized, or mismatched approval does not grant authority.
10. Peak mutation requires the matching committed `PROMOTE` Decision and compare-and-swap against the expected previous Peak.
11. Rejected or rolled-back Candidates never become the next parent.
12. Retries with the same idempotency identity must either replay the same committed result or fail on conflicting content.

## Evidence and secret boundary

Evidence metadata may contain stable identifiers, hashes, scores, costs, task-family metrics, failure categories, and artifact URIs. It must not contain API keys, authorization headers, raw secrets, hidden benchmark bodies, or unrestricted private review content.

Provider credentials are supplied through deployment-specific secret management and are never committed to configuration examples, manifests, approval records, or test fixtures.

## External lineage mirrors

The checked-in local control transaction, Checkpoint bundle, and Peak pointer are the reference implementation's source of truth. DVC, lakeFS, MLflow, or an internal catalog may mirror lineage later, but they are not wired as authoritative distributed transactions on PR #7.

A production successor must define whether a mirror failure blocks promotion, is retried asynchronously, or is treated as an operational incident. It must not silently rewrite the already committed RSI Decision.

## Validation obligations

A change to any provider request/result schema, adapter selection field, artifact rule, endpoint lifecycle, approval identity, lineage link, or composition edge must run:

```text
python -m compileall -q src tests
ruff check src tests
mypy src
pytest --cov=post_training_rsi --cov-report=term-missing --cov-fail-under=75
compatibility demo smoke
recursive RSI smoke
Checkpoint audit smoke
approval pause/list/review/resume tests when the authority boundary changes
```

The current production gaps and non-claims are maintained in [`productionization.md`](productionization.md) and [`implementation-status.md`](implementation-status.md).
