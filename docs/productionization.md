# Productionization requirements

The checked-in baseline implements deterministic components and adapter contracts, not a complete autonomous production control plane. Read [`implementation-status.md`](implementation-status.md) before using this checklist.

## Gate 0 — close control-plane gaps

Production work must not start until these deterministic gaps are closed:

- multi-iteration RSI with explicit stop reasons;
- historical Peak comparison, rejection, rollback, and parent invariants;
- runtime checkpoint/lineage/peak persistence;
- strict adapter selection through config;
- serving endpoint handoff and teardown;
- supported `verify` and `audit` commands;
- fail-closed approval boundaries;
- deterministic E2E tests for every terminal path.

## Required replacements for real post-training

- Connect `TeacherClient` to a batch-capable inference provider or isolated vLLM/SGLang endpoint.
- Replace token-Jaccard history with calibrated Sentence-Transformers plus FAISS/HNSW where scale requires it.
- Connect `CommandTrainer` to TRL/DeepSpeed, a managed GPU job, or an internal training platform.
- Connect `CommandEvaluator` to Inspect AI, lm-eval, or a real Agent benchmark environment.
- Add deploy/readiness/endpoint/teardown lifecycle for vLLM/SGLang or managed serving.
- Mirror complete local lineage manifests to DVC/lakeFS and MLflow only after local atomic persistence succeeds.

## Security controls

- Execute generated code only in an isolated sandbox with filesystem, process, network, time, and memory limits.
- Keep Benchmark ground truth in a read-only store that Teacher generation cannot access.
- Redact secrets from prompts, traces, stdout/stderr, result files, and tracking artifacts.
- Use provider-side quotas in addition to application cost ledgers.
- Require human approval for production permissions, tool-schema changes, model promotion, and Harness promotion.
- Sign model artifacts and verify content hashes before evaluation and deployment.
- Restrict external command adapters to allowlisted executables, controlled working directories, and bounded environment contracts.

## Reliability controls

- Run synthesis, training, serving, and evaluation as durable idempotent jobs.
- Persist checkpoints before Spot-instance termination.
- Make dataset acceptance and peak-pointer updates atomic.
- Always tear down candidate endpoints in `finally` paths.
- Use shadow evaluation and canary traffic before production replacement.
- Track task-family scores and hard invariants, not only one aggregate score.
- Drill budget abort, provider circuit open, malformed result, artifact mismatch, evaluation failure, approval deny, rollback, and audit reconstruction.

## Data-science controls

The example thresholds are starting points. Calibrate entropy, Distinct-N, semantic similarity, N-gram overlap, LCS, acceptance, and promotion thresholds against labelled false-positive/false-negative sets for each domain. Add:

- Teacher/source diversity and source-balance constraints;
- curriculum coverage and task-family quotas;
- held-out and temporal generalization sets;
- catastrophic-forgetting checks;
- contamination red-team fixtures;
- human review sampling and disagreement metrics.

## Safe deployment sequence

1. Keep deterministic CI green while closing `GAP-RSI-*` and `GAP-LIN-*`.
2. Connect a real Teacher while retaining mock training/evaluation.
3. Add a sandboxed evaluator and verify failure-trajectory quality.
4. Run a small LoRA/QLoRA experiment with immutable dataset/checkpoint lineage.
5. Add artifact signing and DVC/lakeFS/MLflow mirrors.
6. Enable fail-closed Dataset and Model approval.
7. Add Harness outer loop in a sandbox; keep Git mutation human-reviewed.
8. Enable full Co-Evolution only after rollback, cost, teardown, and audit drills pass repeatedly.
