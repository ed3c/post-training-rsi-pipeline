# Implementation status

Status snapshot: Draft PR #7, branch `feat/rsi-convergence`  
Validated code head before the latest documentation commits: `ac334be8411f45196d2522c885ff893cb2d44fda`  
Package version: `0.2.0`  
Default branch status: PR #7 is not merged to `main`

This file is the current integration truth. Component documents describe exact boundaries; `architecture.md` describes the target design.

## 1. Supported CLI surface on PR #7

| Command | Status | Evidence boundary |
|---|---|---|
| `demo` | Supported | dependency-free, one-pass compatibility flow |
| `rsi` | Supported | multi-iteration/resumable RSI composition with durable control and lineage state |
| `verify` | Supported | verification bundle and exact accepted-Dataset hash |
| `audit` | Supported | Checkpoint bundle, control transaction, lineage, and Peak relation reload/verification |
| `approvals` | Supported | immutable approval request listing and status derivation |
| `review` | Supported | immutable approve/deny Decision bound to exact Request SHA-256 |
| `coevolve` | Planned | no supported CLI or converged Model/Harness outer/middle/inner loop |

Support here means reachable on `feat/rsi-convergence`, not on `main`.

## 2. Capability matrix

| ID | Capability | Status | Current evidence | Remaining gap |
|---|---|---|---|---|
| CAP-BOOT-01 | strict configuration | Supported | unknown fields, invalid values, string booleans, unsafe adapter combinations rejected | production secret/identity/storage policy not part of config proof |
| CAP-RUN-01 | immutable Run identity | Supported | Run ID, config hash, start metadata, deterministic resume contract | distributed Run registry not implemented |
| CAP-RSI-01 | five-stage recursive controller | Supported | `rsi` CLI composes synthesis, verification, training, serving, evaluation, decision, persistence | real external infrastructure not verified |
| CAP-RSI-02 | historical Peak | Supported | strict policy plus committed promotion Decision and Peak CAS | remote/distributed Peak store not implemented |
| CAP-RSI-03 | plateau/max-iteration/budget stop | Supported | typed stop Decisions and terminal Snapshots | production threshold selection remains human-owned |
| CAP-RSI-04 | regression rollback | Supported | rollback policy and immutable marker; accepted Peak remains active | real task-family regression suite not verified |
| CAP-DATA-01 | lexical diversity gates | Supported | entropy, Distinct-2, TTR and exact-duplicate decisions | production threshold calibration not verified |
| CAP-DATA-02 | semantic novelty | Supported by configured backend | token-Jaccard default; optional embedding boundary exists | embedding dependency/runtime not part of default proof |
| CAP-DATA-03 | benchmark decontamination | Supported | N-gram and LCS audit evidence | production benchmark corpus/version governance not implemented |
| CAP-DATA-04 | safety/static-code admission | Supported | rule safety and Python AST/import checks | production safety model and sandbox execution not verified |
| CAP-ADP-01 | strict adapter selection | Supported by composition | mock/command backends selected through strict config | real provider SDK credentials and quotas not verified |
| CAP-ADP-02 | bounded command execution | Supported | exact request/response schema, timeout/retry, no shell, idempotency | managed job scheduler integration not verified |
| CAP-ADP-03 | artifact integrity | Supported | path confinement, symlink rejection, controller SHA-256 | remote URI trust policy not implemented |
| CAP-SERVE-01 | endpoint lifecycle | Supported | endpoint handoff and teardown in `finally` | live vLLM/SGLang lifecycle not verified |
| CAP-HITL-01 | Dataset review | Supported when enabled | content-addressed sample/request/decision and fail-closed release gate | enterprise identity/quorum/MFA absent |
| CAP-HITL-02 | Checkpoint promotion review | Supported when enabled | exact Checkpoint subject/hash authority before Peak mutation | enterprise reviewer separation of duties absent |
| CAP-HITL-03 | Harness review | Implemented component | approval schema supports Harness | no supported Co-Evolution runtime reaches it |
| CAP-LIN-01 | immutable control transactions | Supported | records written immutably; transaction marker written last | remote transaction service not implemented |
| CAP-LIN-02 | atomic Checkpoint bundle | Supported | metadata, lineage, artifact hash, bundle manifest tied to transaction | remote object-store atomicity not implemented |
| CAP-LIN-03 | Peak compare-and-swap | Supported | expected previous Peak, strict score/iteration monotonicity, promotion link | distributed consensus not implemented |
| CAP-LIN-04 | quarantine/reject/rollback history | Supported | immutable evidence-backed markers | retention and legal-hold policy not implemented |
| CAP-AUD-01 | reverse audit | Supported | `audit` reloads bundle, transaction, lineage, and Peak relation | cross-service audit index not implemented |
| CAP-COEV-01 | Harness mutation outer loop | Planned integration | component files may exist | no supported State Machine/CLI convergence |
| CAP-COEV-02 | successful trace harvesting | Planned integration | component files may exist | no supported middle-loop evidence graph |
| CAP-COEV-03 | model inner loop and hot-swap | Planned | target architecture only | PR #10/#11 required |
| CAP-GT-01 | Git Town stack | Not configured | documentation-only PR graph | version/config/leases/rehearsal/stack.tsv/human approval missing |

## 3. Supported RSI flow

```text
BOOT
  → DIAGNOSE
  → HYPOTHESIS
  → SYNTHESIZE
  → VERIFY
  → optional DATA_REVIEW_PENDING
  → TRAIN
  → SERVE
  → EVALUATE
  → DECIDE
  → optional MODEL_REVIEW_PENDING
  → PROMOTED | REJECTED | ROLLED_BACK
  → DIAGNOSE next iteration | STOPPED | ABORTED
```

Durable proof consists of evidence records, Decisions, Transitions, Snapshots, control transactions, Checkpoint bundles, Peak history, approval records, and quarantine markers. A process return value alone is not sufficient evidence.

## 4. Required invariants currently enforced

```text
active_checkpoint_id == peak_checkpoint_id
candidate.parent_checkpoint_id == active_checkpoint_id
candidate_score > peak_score + min_improvement
```

Also enforced:

- threshold equality rejects;
- rejected/rolled-back Candidate never becomes parent;
- promotion precedes final max-iteration stop when improvement is valid;
- exact budget limits are allowed;
- Peak update requires committed matching `PROMOTE` Decision;
- Peak CAS rejects stale writer, backward iteration, and non-increasing score;
- artifact SHA-256 is recomputed by the controller;
- approvals bind Request hash, Run, iteration, Subject type/ID/hash, reviewer role, and deadline;
- missing/pending/denied/expired/malformed/mismatched approval fails closed;
- teardown is attempted in success and failure paths;
- control dependencies reject cross-Run and future evidence;
- transaction marker is the commit point;
- resume rejects immutable Run/config mismatch.

## 5. Validation status

A dedicated repair-and-validation workflow applied the convergence repair to `feat/rsi-convergence`, then completed successfully for code commit `ac334be8411f45196d2522c885ff893cb2d44fda`:

```text
python -m compileall -q src tests
ruff check src tests
mypy src
python -m pytest -q --cov=post_training_rsi --cov-report=term-missing --cov-fail-under=75
python -m post_training_rsi --workspace /tmp/rsi-demo demo
python -m post_training_rsi --workspace /tmp/rsi-run --run-id ci-convergence rsi
Checkpoint audit smoke
```

The PR-triggered CI run for that bot-authored commit concluded `action_required` with no jobs. That status indicates an Actions approval boundary, not a test failure. The current PR remains Draft until the latest documentation head receives a normal green PR run.

## 6. Artifact truth

Expected local evidence graph:

```text
<workspace>/
├── run/run-metadata.json
├── iterations/iter-<N>/
│   ├── raw.jsonl
│   ├── accepted.jsonl
│   ├── quarantine.jsonl
│   ├── filter_audit.jsonl
│   ├── synthesis_manifest.json
│   └── dataset_summary.json
├── control/{evidence,decisions,transitions,snapshots,transactions}/
├── checkpoints/<checkpoint-id>/{checkpoint.json,lineage_manifest.json,bundle_manifest.json}
├── peak_checkpoint.json
├── peak_history/
├── quarantine/
├── approvals/{samples,requests,decisions}/
├── adapter-runtime/
└── reports/
```

Exact component paths are schema-owned. Inspect current code before writing migrations.

## 7. Open gap registry

| Gap ID | Description | Planned owner | Exit evidence |
|---|---|---|---|
| GAP-CI-01 | exact current PR #7 head needs a normal green check set | PR #7 | compile/Ruff/mypy/full tests/CLI smoke on latest head |
| GAP-DOC-01 | merge current synchronized docs with PR #7 | PR #7 | README/AGENTS/docs match exact supported runtime |
| GAP-INF-01 | real Teacher API execution | production adapter PR | request/token/cost/error evidence with no secret leakage |
| GAP-INF-02 | real GPU SFT/DPO | training provider PR | reproducible job, Dataset hash, artifact hash, loss/metrics, teardown/cost |
| GAP-INF-03 | live inference serving | serving provider PR | readiness, endpoint handoff, benchmark, teardown evidence |
| GAP-EVAL-01 | production Inspect AI/lm-eval task families | evaluation PR | versioned suite, per-family scores, failure traces, regression thresholds |
| GAP-STO-01 | remote DVC/lakeFS/MLflow/object storage | lineage provider PR | remote transaction/recovery/integrity drill |
| GAP-SEC-01 | enterprise reviewer identity and quorum | identity/HITL PR | IdP, MFA, RBAC, separation-of-duties tests |
| GAP-SEC-02 | production sandbox and egress policy | sandbox PR | allow/deny, timeout, resource, network, and escape tests |
| GAP-OPS-01 | distributed locks, retention, backup, disaster recovery | operations PR | contention, restore, retention, and corruption drills |
| GAP-COEV-01 | Harness outer loop | PR #8 | mutate/validate/evaluate/accept/plateau records |
| GAP-COEV-02 | trace harvesting middle loop | PR #9 | observable successful traces → verified Dataset evidence |
| GAP-COEV-03 | model inner loop | PR #10 | train/evaluate/promote-or-rollback/hot-swap evidence |
| GAP-COEV-04 | Co-Evolution convergence and CLI | PR #11 | end-to-end cycle, resume, cost/plateau/rollback, docs, CLI |
| GAP-GT-01 | Git Town admission | human-owned setup | exact version/config/parent graph/worktree leases/rehearsal/stack.tsv |

## 8. Explicit non-claims

The repository does not currently claim:

- production readiness;
- real post-training gradient updates on cloud GPU;
- real inference-cloud Teacher generation;
- live external benchmark validity;
- enterprise authorization;
- distributed transactional safety;
- complete Model/Harness Co-Evolution;
- active Git Town automation.

Any future claim must include exact commit, configuration, data/artifact hashes, environment, command, result, and known limitations.
