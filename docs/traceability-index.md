# Architecture traceability index

This index maps the source-PDF requirements to checked-in implementation, tests, evidence artifacts, known gaps, and the molecular PR that should close each gap.

Status: **I** implemented/reachable, **C** contract only, **P** partial, **N** not implemented.

| ID | Requirement | Code/config | Tests/evidence | Status | Gap / planned PR |
|---|---|---|---|:---:|---|
| `CTRL-DOM-01` | One versioned provider-neutral State/Event/Stop/Decision/Evidence vocabulary | `control_plane/enums.py`, `records.py`, `validation.py`; schema `post-training-rsi.control/v1` | `tests/test_control_plane.py`, `docs/control-plane-contracts.md` | C | `GAP-CTRL-01`; adoption in PR-03/04/05/06/07 |
| `CTRL-DOM-02` | Exact-field canonical JSON and fail-closed record deserialization | `control_plane/records.py`, `validation.py` | round-trip, unknown-field, schema, enum, hash, timestamp, cost, evidence tests | C | persistence/runtime emission in PR-04/07 |
| `PDF-RSI-01` | Five-stage diagnose → hypothesis → synthesize/verify → train → evaluate/decide loop | `engine.py`, `config.RSIConfig`, shared control enum | `test_engine.py`, run summary; contract tests only for target names | P | `GAP-RSI-01` / `PR-03-rsi-loop` |
| `PDF-RSI-02` | Historical Peak is separate from latest candidate | `ArtifactStore.write_peak/load_peak`, `IterationOutcome`, `DecisionAction.PROMOTE` | lineage unit tests and contract serialization only | C | `GAP-RSI-02` / `PR-03-rsi-loop` |
| `PDF-RSI-03` | Plateau/early stopping prevents post-peak over-search | `RSIConfig.plateau_patience`, `StopReason.PLATEAU` | no runnable transition test | N/C | `GAP-RSI-03` / `PR-03-rsi-loop` |
| `PDF-RSI-04` | Regressed candidate rolls back; rejected candidate is not next parent | legacy outcome fields plus rollback/reject control vocabulary | no transition test | N/C | `GAP-RSI-02` / `PR-03-rsi-loop` |
| `PDF-COST-01` | Per-trial and total API budget circuit breakers | `cost.py`, `BudgetConfig`, budget stop-reason contracts | `test_cost.py`, ledger events | I/P | generation only; `PR-05-adapter-runtime` extends all stages |
| `PDF-COST-02` | Bounded provider failures/retries | `CostLedger.record_api_failure`, provider circuit event/reason contracts | cost unit test; contract serialization | C | not wired to Teacher; `PR-05-adapter-runtime` |
| `PDF-DATA-01` | Exact duplicate, Shannon entropy, Distinct-N, TTR | `verification/lexical.py`, `pipeline.py` | `test_verification.py`, `filter_audit.jsonl` | I | calibrate domain thresholds later |
| `PDF-DATA-02` | Semantic novelty against accepted history | `verification/semantic.py`, `max_semantic_similarity` | verification tests/metrics | I | dense index remains optional contract |
| `PDF-DATA-03` | Benchmark N-gram/LCS decontamination | `verification/decontamination.py` | verification tests/metrics | I | large-scale index is production work |
| `PDF-SAFE-01` | Prompt-injection/content safety gate | `verification/safety.py` | verification tests | I/P | rule classifier only; external classifier selection in `PR-05` |
| `PDF-CODE-01` | Generated Python static allowlist check | `verification/code.py` | verification tests | I | execution sandbox remains out of core |
| `PDF-LIN-01` | Teacher/API/prompt/filter/dataset/checkpoint lineage | `generation.py`, `synthesis/`, `LineageManifest`, evidence kinds/records | `test_lineage.py`, `test_control_plane.py` | C/P | manifests/control records not emitted by engine; `PR-04-lineage-runtime` |
| `PDF-LIN-02` | Regression audit from checkpoint back to data/filter/Teacher | local store primitives and `EvidenceKind.REGRESSION_AUDIT` | no `audit` CLI | N/C | `GAP-CLI-01` / `PR-07-cli-operations` |
| `PDF-TRAIN-01` | Provider-neutral SFT/DPO training boundary | `Trainer`, `MockTrainer`, `CommandTrainer`, training/checkpoint evidence kinds | `test_adapters.py`, contract tests | C/P | mock used; strict selection and artifact verification in `PR-05` |
| `PDF-EVAL-01` | Dynamic benchmark with task/failure evidence | `Evaluator`, deterministic/command adapters, evaluation/failure evidence kinds | adapter and contract tests | I/P | endpoint not passed; one aggregate decision path |
| `PDF-SERVE-01` | Candidate deploy → evaluate → teardown | `ServingAdapter.deploy`, serving evidence kinds/events | adapter and contract tests | P/C | no endpoint handoff/undeploy; `PR-05-adapter-runtime` |
| `PDF-HITL-01` | Human review for data and Model/Harness promotion | approval actions/subjects/evidence vocabulary only | control contract tests | C/N | `GAP-HITL-01` / `PR-06-hitl-approval` |
| `PDF-COEV-01` | Harness mutation from failure traces | control states/events plus `harness/__init__.py` placeholder | contract tests only | C/N | `GAP-COEV-01` / `PR-08-harness-outer-loop` |
| `PDF-COEV-02` | Plateau triggers successful trace harvesting | control states/events and config only | contract tests only | C/N | `GAP-COEV-02` / `PR-09-trace-harvest` |
| `PDF-COEV-03` | Verified traces train candidate model | reusable verification/trainer contracts and control vocabulary | none end to end | C/N | `PR-10-model-inner-loop` |
| `PDF-COEV-04` | Better model hot-swaps, Harness slims, outer loop resets | serving/control contracts only | none end to end | C/N | `PR-11-coevolution-convergence` |
| `OPS-CLI-01` | Documented commands are actually registered | `__main__.py` | `test_cli.py` | P | only `demo`; `PR-07-cli-operations` |
| `OPS-CI-01` | Deterministic no-network/no-GPU CI | `.github/workflows/ci.yml` | successful baseline run; PR #2 CI required | I | add transition matrix as features land |
| `DOC-AGENT-01` | Agent read order, ownership, invariants | root/scoped `AGENTS.md`, `docs/README.md` | documentation PR review | I | maintain in every structural PR |
| `DOC-SM-01` | Directory → state-machine → data-flow mapping | `README.md`, `state-machine.md`, `control-plane-contracts.md` | traceability review | I | update with every transition/schema change |
| `OPS-GT-01` | Safe Git Town stack operation | no config/version/manifest | no admission evidence | N | remain fail closed; see `stacked-pr-plan.md` |

A row with combined status such as `N/C` means that names/records exist as a contract, but the required runtime behavior remains unimplemented.

## Evidence paths

| Evidence | Producer | Consumer | Current status |
|---|---|---|---|
| `iterations/iter-N/raw.jsonl` | synthesis/generation | verification audit | current |
| `iterations/iter-N/accepted.jsonl` | verification | trainer | current |
| `iterations/iter-N/quarantine.jsonl` | verification | audit/root-cause flow | current |
| `iterations/iter-N/filter_audit.jsonl` | verification | data-science review | current |
| `iterations/iter-N/synthesis_manifest.json` | generator/Teacher | lineage | current |
| `iterations/iter-N/dataset_summary.json` | artifact store | controller/ops | current |
| `checkpoints/<id>/weights.mock.json` | mock trainer | local serving/evaluation | current |
| `checkpoints/<id>/checkpoint.json` | artifact store target path | audit/deployment | target |
| `checkpoints/<id>/lineage_manifest.json` | lineage target path | audit/MLflow/DVC mirror | target |
| `control/evidence/<id>.json` | adapters/modules via `EvidenceRecord` | decisions/transitions/lineage | target persistence |
| `control/decisions/<id>.json` | orchestration/approval via `DecisionRecord` | transitions/audit | target persistence |
| `control/transitions/<id>.json` | controller via `TransitionRecord` | replay/audit | target persistence |
| `control/snapshots/<id>.json` | controller via `StateSnapshot` | resume/ops | target persistence |
| `peak_checkpoint.json` | promotion transaction target | next iteration and rollback | target |
| `reports/rsi-run-summary.json` | current engine | human/CI evidence | current |

PR #2 defines the in-memory/canonical JSON contracts for the four `control/` record types. PR-04/07 must decide and test the exact persistence layout before those paths become current evidence.

## Traceability update rule

A requirement may move to **Implemented** only when:

1. its supported CLI/runtime path exists;
2. a negative/rollback test exists where applicable;
3. the exact evidence file is asserted;
4. schema-v1 control records are emitted where the requirement crosses module boundaries;
5. current-state docs no longer list the gap;
6. the PR is linked in this table or its successor manifest.

A control record may move from **Contract only** to **Implemented** only when a supported controller emits it, a store round-trips it, and deterministic E2E tests assert its evidence references.
