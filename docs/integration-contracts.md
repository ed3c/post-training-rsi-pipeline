# Integration contracts

Status: **supported local reference boundaries; production providers are not verified**

This document is the provider-neutral handoff index for the RSI and Model/Harness Co-Evolution pipelines. It does not replace the exact component documents or claim that a real inference cloud, GPU trainer, serving platform, enterprise reviewer, or distributed storage service has been exercised.

## Read order

1. [`../AGENTS.md`](../AGENTS.md)
2. [`../README.md`](../README.md)
3. [`control-plane-contracts.md`](control-plane-contracts.md)
4. [`adapter-runtime.md`](adapter-runtime.md)
5. [`lineage-runtime.md`](lineage-runtime.md)
6. [`hitl-approval.md`](hitl-approval.md)
7. [`rsi-convergence.md`](rsi-convergence.md)
8. [`coevolution-convergence.md`](coevolution-convergence.md)
9. [`coevolution-audit-recovery.md`](coevolution-audit-recovery.md)

## Boundary map

```text
PipelineConfig
  → provider-neutral Adapter invocation
  → exact request / response / artifact SHA-256
  → EvidenceRecord
  → immutable control transaction
  → State Machine policy
  → Checkpoint or Harness snapshot bundle
  → compare-and-swap pointer
  → read-only status / integrity audit
```

| Boundary | Input contract | Output contract | Owner | Must not decide |
|---|---|---|---|---|
| Teacher synthesis | versioned hypothesis, prompt and idempotency identity | synthesis batch, token/cost metadata, synthesis evidence | `adapter_runtime/`, `synthesis/` | Dataset admission or promotion |
| Verification | raw examples, benchmark corpus, diversity and safety policy | accepted/quarantine JSONL, filter audit, Dataset SHA-256 | `verification/` | model or Harness quality |
| Training | exact Dataset SHA-256, parent Checkpoint, model identity | Candidate artifact, controller-recomputed SHA-256, training evidence | `training/`, `adapter_runtime/` | benchmark outcome |
| Serving | verified Candidate artifact | deployment identity, exact Endpoint, teardown evidence | `serving/`, `adapter_runtime/` | promotion |
| Evaluation | exact Endpoint, task suite and active Harness identity | aggregate/task-family scores and observable failure traces | `evaluation/`, Harness policy | pointer mutation |
| Approval | content-addressed Subject, sample and expiry | immutable approve/deny Decision and evidence | `approval/` | authentication implementation or quality threshold |
| Lineage | committed records and verified artifacts | transaction manifest, bundles, Peak/Harness pointers, quarantine markers | `lineage/`, Harness persistence | policy thresholds |
| Co-Evolution audit | durable Run pointer and immutable evidence graph | versioned read-only status/audit report | `audit/` | repair, resume, approval, rollback, provider retry |

## Required identity propagation

Every external or long-running operation must preserve the applicable subset of:

```text
run_id
iteration or cycle
request_id
idempotency_key
subject_type
subject_id
parent_checkpoint_id
active_harness_id
dataset_sha256
artifact_sha256
config_sha256
control_transaction_id
```

A result with a mismatched identity is rejected rather than repaired by inference.

## Failure semantics

```text
missing / malformed / unknown schema       → fail closed
stale idempotency content                  → conflict
worker-reported artifact hash mismatch     → integrity failure
pending or denied approval                 → no side effect
non-improving Candidate                     → reject or rollback; active pointer unchanged
serving evaluation failure                 → teardown still attempted
uncommitted record                          → orphan, not evidence
retained lock                               → human investigation; never auto-delete
read-only audit FAIL                        → freeze writers and restore one consistent backup generation
```

## Production adapter obligations

A real provider implementation must add evidence for credentials and destination authorization outside repository content, finite timeout/retry/cost limits, secret redaction, endpoint teardown, artifact retention, reviewer authentication, and disaster recovery. The local reference adapters and deterministic ports prove control-flow and integrity contracts only.

## Source architecture mapping

The source architecture requires the five-stage RSI loop, historical Peak preservation, cost and diversity circuit breakers, complete Dataset/Checkpoint lineage, HITL gates, and the Harness outer loop → observable Trace harvest → model inner loop → hot-swap/reset cycle. The implementation documents keep those responsibilities separate so no provider or storage layer can silently bypass the policy boundary.
