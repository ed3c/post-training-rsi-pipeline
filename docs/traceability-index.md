# Architecture traceability index

This index maps the source-PDF requirements to checked-in implementation, tests, evidence artifacts, known gaps, and the molecular PR that should close each gap.

Status: **I** implemented/reachable, **C** contract only, **P** partial, **N** not implemented.

| ID | Requirement | Code/config | Tests/evidence | Status | Gap / planned PR |
|---|---|---|---|:---:|---|
| `PDF-RSI-01` | Five-stage diagnose → hypothesis → synthesize/verify → train → evaluate/decide loop | `engine.py`, `config.RSIConfig` | `test_engine.py`, run summary | P | `GAP-RSI-01` / `PR-03-rsi-loop` |
| `PDF-RSI-02` | Historical Peak is separate from latest candidate | `ArtifactStore.write_peak/load_peak`, `IterationOutcome` | lineage unit tests only | C | `GAP-RSI-02` / `PR-03-rsi-loop` |
| `PDF-RSI-03` | Plateau/early stopping prevents post-peak over-search | `RSIConfig.plateau_patience` | none on runnable path | N | `GAP-RSI-03` / `PR-03-rsi-loop` |
| `PDF-RSI-04` | Regressed candidate rolls back; rejected candidate is not next parent | fields exist in `IterationOutcome` | no transition test | N | `GAP-RSI-02` / `PR-03-rsi-loop` |
| `PDF-COST-01` | Per-trial and total API budget circuit breakers | `cost.py`, `BudgetConfig` | `test_cost.py`, ledger events | I/P | generation only; `PR-05-adapter-runtime` extends all stages |
| `PDF-COST-02` | Bounded provider failures/retries | `CostLedger.record_api_failure` | cost unit test | C | not wired to Teacher; `PR-05-adapter-runtime` |
| `PDF-DATA-01` | Exact duplicate, Shannon entropy, Distinct-N, TTR | `verification/lexical.py`, `pipeline.py` | `test_verification.py`, `filter_audit.jsonl` | I | calibrate domain thresholds later |
| `PDF-DATA-02` | Semantic novelty against accepted history | `verification/semantic.py`, `max_semantic_similarity` | verification tests/metrics | I | dense index remains optional contract |
| `PDF-DATA-03` | Benchmark N-gram/LCS decontamination | `verification/decontamination.py` | verification tests/metrics | I | large-scale index is production work |
| `PDF-SAFE-01` | Prompt-injection/content safety gate | `verification/safety.py` | verification tests | I/P | rule classifier only; external classifier selection in `PR-05` |
| `PDF-CODE-01` | Generated Python static allowlist check | `verification/code.py` | verification tests | I | execution sandbox remains out of core |
| `PDF-LIN-01` | Teacher/API/prompt/filter/dataset/checkpoint lineage | `generation.py`, `synthesis/`, `LineageManifest` | `test_lineage.py` | C/P | manifest not emitted by engine; `PR-04-lineage-runtime` |
| `PDF-LIN-02` | Regression audit from checkpoint back to data/filter/Teacher | local store primitives | no `audit` CLI | N | `GAP-CLI-01` / `PR-07-cli-operations` |
| `PDF-TRAIN-01` | Provider-neutral SFT/DPO training boundary | `Trainer`, `MockTrainer`, `CommandTrainer` | `test_adapters.py` | C/P | mock used; strict selection and artifact verification in `PR-05` |
| `PDF-EVAL-01` | Dynamic benchmark with task/failure evidence | `Evaluator`, deterministic/command adapters | adapter tests | I/P | endpoint not passed; one aggregate decision path |
| `PDF-SERVE-01` | Candidate deploy → evaluate → teardown | `ServingAdapter.deploy` | adapter tests | P | no endpoint handoff/undeploy; `PR-05-adapter-runtime` |
| `PDF-HITL-01` | Human review for data and Model/Harness promotion | none | none | N | `GAP-HITL-01` / `PR-06-hitl-approval` |
| `PDF-COEV-01` | Harness mutation from failure traces | `harness/__init__.py` only | none | N | `GAP-COEV-01` / `PR-08-harness-outer-loop` |
| `PDF-COEV-02` | Plateau triggers successful trace harvesting | config only | none | N | `GAP-COEV-02` / `PR-09-trace-harvest` |
| `PDF-COEV-03` | Verified traces train candidate model | reusable verification/trainer contracts | none end to end | N | `PR-10-model-inner-loop` |
| `PDF-COEV-04` | Better model hot-swaps, Harness slims, outer loop resets | serving contract only | none | N | `PR-11-coevolution-convergence` |
| `OPS-CLI-01` | Documented commands are actually registered | `__main__.py` | `test_cli.py` | P | only `demo`; `PR-07-cli-operations` |
| `OPS-CI-01` | Deterministic no-network/no-GPU CI | `.github/workflows/ci.yml` | successful baseline run | I | add transition matrix as features land |
| `DOC-AGENT-01` | Agent read order, ownership, invariants | `AGENTS.md`, `docs/README.md` | documentation PR review | I | maintain in every structural PR |
| `DOC-SM-01` | Directory → state-machine → data-flow mapping | `README.md`, `state-machine.md` | traceability review | I | update with every transition change |
| `OPS-GT-01` | Safe Git Town stack operation | no config/version/manifest | no admission evidence | N | remain fail closed; see `stacked-pr-plan.md` |

## Evidence paths

| Evidence | Producer | Consumer |
|---|---|---|
| `iterations/iter-N/raw.jsonl` | synthesis/generation | verification audit |
| `iterations/iter-N/accepted.jsonl` | verification | trainer |
| `iterations/iter-N/quarantine.jsonl` | verification | audit/root-cause flow |
| `iterations/iter-N/filter_audit.jsonl` | verification | data-science review |
| `iterations/iter-N/synthesis_manifest.json` | generator/Teacher | lineage |
| `iterations/iter-N/dataset_summary.json` | artifact store | controller/ops |
| `checkpoints/<id>/weights.mock.json` | mock trainer | local serving/evaluation |
| `checkpoints/<id>/checkpoint.json` | artifact store target path | audit/deployment |
| `checkpoints/<id>/lineage_manifest.json` | lineage target path | audit/MLflow/DVC mirror |
| `peak_checkpoint.json` | promotion transaction target | next iteration and rollback |
| `reports/rsi-run-summary.json` | current engine | human/CI evidence |

## Traceability update rule

A requirement may move to **Implemented** only when:

1. its supported CLI/runtime path exists;
2. a negative/rollback test exists where applicable;
3. the exact evidence file is asserted;
4. current-state docs no longer list the gap;
5. the PR is linked in this table or its successor manifest.
