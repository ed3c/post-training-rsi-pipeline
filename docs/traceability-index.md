# Requirement traceability index

Status: Draft PR #7 integration branch  
Validated code head before the latest documentation commits: `ac334be8411f45196d2522c885ff893cb2d44fda`

This index maps architecture requirements to code ownership, deterministic tests, durable artifacts, implementation status, gaps, and Pull Requests. A row marked Supported applies to `feat/rsi-convergence`, not to `main` until PR #7 is merged.

## Status legend

```text
Supported               reachable from current branch CLI/API and tested
Implemented component   coded/tested but not composition-reachable
Contract only           schema/protocol only
Partial                 some required evidence/edges missing
Planned                 target only
Not verified            implementation may exist; required execution evidence absent
```

## 1. Recursive Self-Improvement

| Requirement ID | Requirement | Code owner | Tests/evidence | Durable artifact | Status | Gap / PR |
|---|---|---|---|---|---|---|
| PDF-RSI-01 | five-stage Diagnose → Hypothesis → Synthesize/Verify → Train → Evaluate loop | `orchestration/converged.py`, `run_state.py`, CLI `rsi` | full test suite; converged RSI smoke | control records, iteration bundles, Run report | Supported | PR #7 |
| PDF-RSI-02 | historical Peak separate from latest Candidate | `rsi_policy.py`, `lineage/peak_store.py` | `test_rsi_policy.py`, Peak monotonic tests, audit smoke | `peak_checkpoint.json`, `peak_history/` | Supported | PR #3 + #4 + #7 |
| PDF-RSI-03 | strict minimum improvement | `rsi_policy.py` | threshold equality and strict promotion tests | promotion/rejection Decision | Supported | PR #3 |
| PDF-RSI-04 | plateau early stop | `rsi_policy.py`, converged controller | plateau/patience tests | terminal Decision/Snapshot | Supported | PR #3 + #7 |
| PDF-RSI-05 | maximum iteration stop | `rsi_policy.py`, config | final-iteration promotion and max tests | stop Decision/Snapshot | Supported | PR #3 + #7 |
| PDF-RSI-06 | budget circuit breaker | `cost.py`, `rsi_policy.py`, controller | exact-limit and crossing tests | cost Evidence + abort Decision | Supported | PR #3 + #7 |
| PDF-RSI-07 | regression rollback | `rsi_policy.py`, `quarantine_store.py` | rollback and marker tests | rollback Decision + marker | Supported | PR #3 + #4 + #7 |
| PDF-RSI-08 | rejected Candidate never becomes parent | `rsi_policy.py`, resume/controller guards | parent-invariant tests | Snapshot + Peak pointer | Supported | PR #3 + #7 |
| PDF-RSI-09 | deterministic resume | `run_state.py`, `converged.py`, stores | replay/resume tests and smoke | Run metadata, transactions, Snapshots, Peak | Supported | PR #7 |

## 2. Synthetic data and verification

| Requirement ID | Requirement | Code owner | Tests/evidence | Durable artifact | Status | Gap / PR |
|---|---|---|---|---|---|---|
| PDF-DATA-01 | exact duplicate rejection | `verification/pipeline.py` | verification tests | `filter_audit.jsonl` | Supported | baseline + PR #7 composition |
| PDF-DATA-02 | Shannon entropy floor | `verification/lexical.py`, pipeline | lexical/verification tests | audit metrics/reason | Supported | baseline |
| PDF-DATA-03 | Distinct-N / TTR floors | `verification/lexical.py`, pipeline | lexical/verification tests | audit metrics/reason | Supported | baseline |
| PDF-DATA-04 | semantic novelty against history | `verification/semantic.py`, pipeline | semantic/verification tests | similarity metric/reason | Supported by configured backend | production embedding backend not verified |
| PDF-DATA-05 | N-gram benchmark decontamination | `verification/decontamination.py` | decontamination tests | overlap metric/reason | Supported | production corpus governance gap |
| PDF-DATA-06 | LCS benchmark decontamination | `verification/decontamination.py` | decontamination tests | LCS metric/reason | Supported | production corpus governance gap |
| PDF-DATA-07 | prompt/role safety gate | `verification/safety.py` | safety tests | safety category/reason | Supported | production safety model not verified |
| PDF-DATA-08 | Python AST/import static gate | `verification/code.py` | static-code tests | static reasons | Supported | sandbox execution planned |
| PDF-DATA-09 | exact accepted Dataset hash | `lineage/store.py`, controller/adapter integrity | Dataset-hash tests, verify CLI | `accepted.jsonl` SHA-256 | Supported | PR #5 + #7 |
| PDF-DATA-10 | quarantine rejected data | verification + `lineage/quarantine_store.py` | quarantine tests | `quarantine.jsonl`, marker | Supported | PR #4 + #7 |

## 3. Provider and serving boundary

| Requirement ID | Requirement | Code owner | Tests/evidence | Durable artifact | Status | Gap / PR |
|---|---|---|---|---|---|---|
| PDF-ADP-01 | provider-neutral Teacher | `synthesis/`, `adapter_runtime/factory.py` | adapter/Teacher tests | synthesis manifest/evidence | Supported with mock/command selection | real API not verified; infrastructure PR |
| PDF-ADP-02 | bounded retries and timeout | `adapter_runtime/command.py`, adapter configs | timeout/retry/stale-result tests | request/response/commit bundle | Supported | managed scheduler not verified |
| PDF-ADP-03 | no shell command interpolation | adapter config/command runtime | command-config tests | command metadata | Supported | PR #5 |
| PDF-ADP-04 | secret-minimizing environment | adapter runtime | secret-exclusion tests | sanitized metadata | Supported | production secret manager gap |
| PDF-ADP-05 | Trainer echo and Dataset/parent integrity | `training/adapter.py`, controller | mismatch tests | training evidence + Checkpoint payload | Supported | real GPU not verified |
| PDF-ADP-06 | controller artifact SHA-256 | `adapter_runtime/integrity.py` | path/hash/symlink/mutation tests | commit + Checkpoint hash | Supported | remote URI policy gap |
| PDF-SERVE-01 | Candidate deployment | `serving/adapter.py`, lifecycle | deploy tests | endpoint Evidence | Supported with local/command adapter | live vLLM/SGLang not verified |
| PDF-SERVE-02 | exact endpoint handoff to evaluator | lifecycle/controller | endpoint-handoff tests | evaluation metadata | Supported | PR #5 + #7 |
| PDF-SERVE-03 | teardown on success/failure | lifecycle/controller | teardown matrix | teardown Evidence | Supported | production provider not verified |
| PDF-EVAL-01 | dynamic benchmark evaluation | `evaluation/adapter.py`, controller | deterministic/command evaluator tests | evaluation Evidence | Supported with deterministic/command adapter | Inspect AI/lm-eval live suite gap |
| PDF-EVAL-02 | task-family failure traces | evaluator/control vocabulary | evaluator tests where present | failure-trace Evidence | Partial | production suite PR |

## 4. Lineage and audit

| Requirement ID | Requirement | Code owner | Tests/evidence | Durable artifact | Status | Gap / PR |
|---|---|---|---|---|---|---|
| PDF-LIN-01 | immutable Evidence/Decision/Transition/Snapshot | `lineage/control_store.py` | transaction/replay/tamper tests | `control/*` | Supported | PR #4 + #7 |
| PDF-LIN-02 | transaction marker written last | control store | orphan/uncommitted tests | `control/transactions/*.json` | Supported | PR #4 |
| PDF-LIN-03 | cross-Run/future dependency rejection | control store | dependency integrity tests | rejected write/no marker | Supported | PR #4 repair + #7 |
| PDF-LIN-04 | atomic Checkpoint bundle | `checkpoint_store.py` | bundle/idempotency/tamper tests | three-file Checkpoint bundle | Supported | PR #4 + #7 |
| PDF-LIN-05 | full Teacher/Prompt/Filter/Dataset/Parent/Score lineage | `lineage/manifest.py`, controller | manifest/bundle tests, audit smoke | `lineage_manifest.json` | Supported | production external service gap |
| PDF-LIN-06 | monotonic Peak compare-and-swap | `peak_store.py` | stale/non-promote/hash/score/iteration tests | Peak pointer + history | Supported | distributed consensus gap |
| PDF-LIN-07 | immutable reject/quarantine/rollback history | `quarantine_store.py` | marker action/subject/evidence/conflict tests | `quarantine/*.json` | Supported | retention/legal-hold gap |
| PDF-LIN-08 | reverse Checkpoint audit | CLI `audit`, lineage stores | audit smoke | regression audit report | Supported | cross-service index gap |
| PDF-LIN-09 | code commit provenance | lineage manifest/run metadata | manifest tests | commit field in lineage | Supported locally | signed/reproducible build gap |

## 5. Human-in-the-Loop

| Requirement ID | Requirement | Code owner | Tests/evidence | Durable artifact | Status | Gap / PR |
|---|---|---|---|---|---|---|
| PDF-HITL-01 | deterministic review sampling | `approval/sampling.py` | order-invariance/sample-bound tests | sample manifest | Supported | PR #6 + #7 |
| PDF-HITL-02 | content-addressed request | `approval/contracts.py`, service/store | request replay/conflict tests | immutable request | Supported | PR #6 |
| PDF-HITL-03 | immutable reviewer Decision | approval service/store | approve/deny/replay/conflict tests | immutable Decision | Supported | PR #6 |
| PDF-HITL-04 | Dataset acceptance gate | controller + approval service | pause/require-approved tests | request/decision evidence | Supported when enabled | enterprise identity gap |
| PDF-HITL-05 | Checkpoint promotion gate | controller + approval service | subject/action/hash substitution tests | request/decision evidence | Supported when enabled | enterprise quorum gap |
| PDF-HITL-06 | Harness acceptance gate | approval component | component tests | request/decision evidence | Implemented component | PR #8/#11 required |
| PDF-HITL-07 | missing/pending/denied/expired fail closed | approval service | state matrix tests | no release authority | Supported | PR #6 |
| PDF-HITL-08 | reviewer role boundary | approval policy | unauthorized-role tests | Decision metadata | Supported locally | IdP/MFA/RBAC gap |

## 6. State Machine and Agent contracts

| Requirement ID | Requirement | Code owner | Tests/evidence | Durable artifact | Status | Gap / PR |
|---|---|---|---|---|---|---|
| OPS-STATE-01 | stable State/Event/Stop vocabulary | `control_plane/enums.py` | control-plane tests | canonical records | Supported | PR #2 |
| OPS-STATE-02 | exact schema/fail-closed parsing | `records.py`, `validation.py` | unknown-field/type/time/hash tests | rejected parse or canonical JSON | Supported | PR #2 |
| OPS-STATE-03 | directory → State ownership | `AGENTS.md`, README, scoped AGENTS | documentation review + path-disjoint PRs | docs | Supported as repository contract | PR #1/#7 |
| OPS-STATE-04 | current/component/target separation | docs contract | documentation review | docs | Supported as repository contract | PR #1/#7 |
| OPS-TRACE-01 | requirement → code/test/artifact/PR index | this document | row review | docs | Supported | update with every structural PR |
| OPS-RESUME-01 | durable resume, not process memory | controller/run state/lineage | resume/replay tests | Run metadata + control state | Supported | PR #7 |

## 7. Model/Harness Co-Evolution target

| Requirement ID | Requirement | Planned/current owner | Evidence required | Status | Planned PR |
|---|---|---|---|---|---|
| PDF-COEV-01 | freeze active model during Harness search | `harness/`, future controller | freeze Snapshot and active-model invariant tests | Planned integration | PR #8 |
| PDF-COEV-02 | mutate Prompt/tool/retry policy | Harness mutator | mutation diff, static/policy validation | Planned integration | PR #8 |
| PDF-COEV-03 | benchmark Harness Candidate and accept only improvement | Harness evaluator/policy | scores, traces, accept/reject Decisions | Planned integration | PR #8 |
| PDF-COEV-04 | plateau triggers trace harvesting | trace harvester | plateau Decision + trace batch evidence | Planned integration | PR #9 |
| PDF-COEV-05 | verify harvested traces through same gates | verification pipeline | accepted/quarantine trace Dataset hashes | Planned integration | PR #9 |
| PDF-COEV-06 | train/evaluate Candidate model from verified traces | trainer/evaluator | Checkpoint, evaluation, lineage | Planned | PR #10 |
| PDF-COEV-07 | promote or rollback and hot-swap | model policy/controller | Decision, Peak/Harness pointer, rollback evidence | Planned | PR #10 |
| PDF-COEV-08 | slim Harness and reset outer loop | future convergence | before/after Harness snapshots and cycle State | Planned | PR #11 |
| PDF-COEV-09 | supported `coevolve` CLI and resume | future convergence | E2E tests, cost/plateau/approval/resume | Planned | PR #11 |

## 8. Production requirements

| Requirement ID | Requirement | Current status | Exit evidence |
|---|---|---|---|
| PROD-INF-01 | real Teacher API | Not verified | versioned requests, token/cost/error evidence, no secret leakage |
| PROD-INF-02 | real GPU SFT/DPO | Not verified | reproducible job, Dataset/artifact hashes, loss/metrics, teardown/cost |
| PROD-INF-03 | live serving | Not verified | readiness, endpoint benchmark, teardown, failure drill |
| PROD-EVAL-01 | production task suites | Not verified | versioned Inspect AI/lm-eval config, per-family scores and traces |
| PROD-STO-01 | remote versioning/lineage | Not verified | DVC/lakeFS/MLflow/object-store transaction and recovery drill |
| PROD-SEC-01 | sandbox/egress | Not verified | allow/deny/resource/network/escape test matrix |
| PROD-SEC-02 | enterprise HITL identity | Not verified | IdP, MFA, RBAC, quorum, separation of duties |
| PROD-OPS-01 | distributed writer safety | Not verified | lock/consensus/contention tests |
| PROD-OPS-02 | backup/restore/retention | Not verified | corruption, restore, retention, and disaster-recovery drill |

## 9. Pull Request mapping

```text
PR #1  Agent/documentation contracts
PR #2  State-domain contracts
PR #3  RSI decision policy
PR #4  transactional lineage runtime
PR #5  adapter runtime
PR #6  HITL approval
PR #7  supported RSI convergence
PR #8  proposed Harness outer loop
PR #9  proposed trace harvesting
PR #10 proposed model inner loop
PR #11 proposed Co-Evolution convergence
```

Git Town is not configured. These mappings are traceability metadata, not executable stack configuration.

## 10. Update rule

Every structural PR must add or update rows for:

```text
requirement ID
exact code owner
exact tests/evidence
artifact path/schema
status
known gap
PR owner
```

Do not mark a row Supported until a supported path reaches the capability and the exact head has deterministic evidence.

<!-- PR13_FORENSIC_RECOVERY_INDEX_START -->
## Recovery bundle traceability

| Requirement ID | Requirement | Code | Test / evidence | Status |
|---|---|---|---|---|
| `REC-BUNDLE-01` | deterministic content-addressed local export | `recovery_bundle/bundle.py:create_bundle` | `test_bundle_is_deterministic_and_deduplicates_blobs` | Implemented component |
| `REC-BUNDLE-02` | canonical manifest and exact blob verification | `verify_bundle`, `load_manifest` | blob/manifest tamper tests | Implemented component |
| `REC-BUNDLE-03` | reject path traversal, symlink, special file, and nested output | path/source guards | focused negative matrix | Implemented component |
| `REC-BUNDLE-04` | restore only into a new inactive directory | `stage_bundle` | round-trip and existing-destination tests | Implemented component |
| `REC-BUNDLE-05` | verify staged path set and bytes | `verify_staged_directory` | staged mutation test | Implemented component |
| `REC-BUNDLE-06` | no automatic activation | no `ACTIVATE` edge or command | `test_forensic_recovery_state_machine_has_no_activation_edge` | Verified by contract test |
| `REC-BUNDLE-07` | retained writer locks fail closed | create/stage lock files | retained-lock test | Implemented component |
| `REC-BUNDLE-08` | machine-readable ownership and non-claims | `forensic-recovery-manifest.json` | `test_forensic_recovery_manifest.py` | Implemented component |
<!-- PR13_FORENSIC_RECOVERY_INDEX_END -->
