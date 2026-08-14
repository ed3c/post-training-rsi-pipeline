# Post-Training RSI Pipeline

> Evidence-first reference implementation for post-training data design, Recursive Self-Improvement (RSI), and Model/Harness Co-Evolution.

This repository converts the source PDF architecture into executable components and explicit integration contracts. It deliberately separates:

- **current executable truth** — what the checked-in CLI and tests can reach now;
- **contract-only components** — adapters or stores that exist but are not selected by the runtime;
- **target architecture** — the full RSI and Co-Evolution state machines still being integrated.

Start with [`AGENTS.md`](AGENTS.md), then use the [documentation index](docs/README.md).

## Current integration truth

Baseline: `feat/pdf-architecture` at `2fa9a8d9746ae5dccd5ff68d78b3a7d75e7c43be`, package version `0.2.0`. The latest CI run for that commit is green.

| Capability | Status | Current truth |
|---|---|---|
| Deterministic `demo` | Implemented | Executes one synthesis → verify → train → deploy → evaluate pass |
| Verification stack | Implemented | Exact/lexical/semantic/decontamination/safety/AST decisions are reachable |
| Budget ledger | Partial | Generation cost is charged; all-stage accounting is not wired |
| External Teacher/trainer/evaluator/serving | Contract only | Protocols/adapters exist; CLI/config does not select them |
| Lineage store/schema | Partial | Iteration bundle is written; checkpoint manifest and Peak pointer are not wired by the engine |
| Recursive RSI | Partial | Config fields exist, but `RSIEngine.run()` performs one hard-coded iteration |
| Peak, rollback, plateau stopping | Planned | Candidate is currently marked promoted without historical comparison |
| `verify`, `audit`, `coevolve` CLI | Planned | Checked-in CLI registers only `demo` |
| Harness mutation/trace harvesting | Planned | `harness/` is currently a placeholder namespace |
| HITL Dataset/Model/Harness approval | Planned | No approval state or operational command exists |
| Git Town stack | Not configured | No config, exact version pin, parent graph, or active stack manifest |

The detailed evidence and gap IDs are in [`docs/implementation-status.md`](docs/implementation-status.md). Do not infer completion from the target diagrams below.

## Quick start: supported path

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

post-training-rsi \
  --config configs/pipeline.example.json \
  --workspace artifacts/demo \
  demo
```

Development gate:

```bash
make install
make lint
make typecheck
make test
make demo
```

`make coevolve` is a known red target at this baseline because the CLI does not expose `coevolve` yet.

## Current executable state machine

```mermaid
stateDiagram-v2
    [*] --> CONFIG_LOADED
    CONFIG_LOADED --> SYNTHESIZED: DeterministicGenerator.generate
    SYNTHESIZED --> BUDGET_CHARGED: CostLedger.charge
    BUDGET_CHARGED --> VERIFIED: VerificationPipeline.verify
    VERIFIED --> DATA_REJECTED: no accepted records
    VERIFIED --> TRAINED: at least one accepted record
    TRAINED --> DEPLOYED: ServingAdapter.deploy
    DEPLOYED --> EVALUATED: Evaluator.evaluate
    EVALUATED --> COMPLETED: write run summary
    DATA_REJECTED --> COMPLETED
    COMPLETED --> [*]
```

Current flow limitations:

- hypothesis is hard-coded;
- only iteration `1` runs;
- `min_acceptance_rate` is configured but not enforced by the engine;
- parent checkpoint is `None`;
- candidate score is assigned as Peak score without comparison;
- checkpoint manifest and `peak_checkpoint.json` are not written;
- endpoint is not passed into evaluation and is not torn down.

## Target five-stage RSI state machine

The source architecture requires a closed loop:

```mermaid
flowchart TD
    D[1. Diagnose failure trajectories] --> H[2. Form versioned data hypothesis]
    H --> S[3. Teacher synthesis]
    S --> B{Budget/provider circuit closed?}
    B -- no --> A[Abort + ledger evidence]
    B -- yes --> V[3. Verification stack]
    V --> F{Acceptance/diversity/safety floor met?}
    F -- no --> Q[Quarantine + root-cause evidence]
    F -- yes --> R{Dataset review required?}
    R -- pending/denied --> X[Stop fail-closed]
    R -- approved/not required --> T[4. SFT/DPO candidate training]
    T --> E[5. Serve + benchmark]
    E --> P{Candidate > historical Peak + delta?}
    P -- yes --> M{Model promotion approval?}
    M -- approved/not required --> K[Promote Peak + persist complete lineage]
    M -- pending/denied --> J[Reject; Peak unchanged]
    P -- no --> J
    J --> C{Patience/budget remains?}
    K --> C
    C -- yes --> D
    C -- no --> Z[Plateau/max-iteration stop or rollback]
```

The Peak is a stable historical pointer, not an alias for the latest candidate. A rejected candidate must never become the next parent.

## Target Model/Harness Co-Evolution state machine

```mermaid
flowchart LR
    F[Freeze active model] --> M[Mutate Harness Prompt/tool/retry policy]
    M --> V[Static + policy validation]
    V --> E[Evaluate Harness candidate]
    E -->|improves| A[Accept Harness snapshot]
    A --> M
    E -->|plateau| H[Harvest successful observable traces]
    H --> G[Run the same data verification gates]
    G --> T[Train candidate model]
    T --> C{Candidate beats active model?}
    C -->|yes| P[Promote/hot-swap model]
    C -->|no| R[Rollback model]
    P --> S[Slim Harness and reset counters]
    S --> F
    R --> F
```

The current repository does not yet implement this outer/middle/inner loop.

## Directory → State Machine ownership

```text
post-training-rsi-pipeline/
├── AGENTS.md                         repository-wide Agent contract and read order
├── README.md                         current/target State Machines and top-level data flow
├── configs/                          BOOT policy inputs and threshold configuration
│   ├── pipeline.example.json
│   └── rsi_policy_rules.json
├── docs/
│   ├── README.md                     multi-hop documentation index
│   ├── implementation-status.md      exact branch truth and gap registry
│   ├── state-machine.md              transition/guard/evidence contracts
│   ├── traceability-index.md         requirement → code → test → artifact → PR
│   ├── stacked-pr-plan.md            molecular PR graph and Git Town admission
│   ├── architecture.md               target PDF architecture
│   └── productionization.md          real cloud/GPU/sandbox requirements
├── src/post_training_rsi/
│   ├── __main__.py                   BOOT / CLI dispatch; currently only `demo`
│   ├── config.py                     CONFIG_LOADED / CONFIG_REJECTED
│   ├── models.py                     shared state payloads and evidence records
│   ├── engine.py                     current transition coordinator
│   ├── generation.py                 current deterministic SYNTHESIZED fixture
│   ├── cost.py                       BUDGET_CHARGED / budget and provider circuits
│   ├── synthesis/                    target SYNTHESIZE provider boundary
│   │   ├── prompts.py                versioned Teacher Prompt construction
│   │   ├── runtime.py                Teacher protocol and SynthesisBatch
│   │   └── teacher.py                mock/OpenAI-compatible Teacher clients
│   ├── verification/                 VERIFIED / QUARANTINED
│   │   ├── lexical.py                entropy, Distinct-N, TTR
│   │   ├── semantic.py               novelty against accepted history
│   │   ├── decontamination.py        Benchmark N-gram/LCS separation
│   │   ├── safety.py                 safety/injection classification
│   │   ├── code.py                   Python AST static checks
│   │   └── pipeline.py               one decision per input record
│   ├── training/                     TRAINED
│   │   └── adapter.py                mock and external command contracts
│   ├── serving/                      DEPLOYED; target SERVE + TEARDOWN
│   │   └── adapter.py
│   ├── evaluation/                   EVALUATED + failure-trace evidence
│   │   └── adapter.py
│   ├── lineage/                      evidence persistence; target Peak transaction
│   │   ├── manifest.py
│   │   └── store.py
│   └── harness/                      target MUTATE_HARNESS / HARVEST_TRACES; placeholder
├── tests/                             deterministic transition and adapter evidence
└── .github/workflows/ci.yml          no-network/no-GPU verification gate
```

### Ownership matrix

| Directory/module | State-machine responsibility | Input | Output | Forbidden responsibility |
|---|---|---|---|---|
| `config.py` | validate BOOT policy | JSON/defaults | immutable config | deciding promotion |
| `models.py` | portable state/evidence types | typed values | serializable records | external calls |
| `engine.py` | transition order and policy | config + protocols | outcomes/terminal reason | provider-specific SDK logic |
| `generation.py`, `synthesis/` | synthesis | hypothesis | examples + Teacher evidence | model promotion |
| `verification/` | data admission | examples + benchmark/history | accepted/quarantine/records | score-based model decisions |
| `training/` | candidate creation | exact accepted dataset/hash + parent | checkpoint + loss + artifact metadata | benchmark policy |
| `serving/` | endpoint lifecycle | checkpoint | readiness/endpoint/teardown | deciding whether model is better |
| `evaluation/` | benchmark evidence | checkpoint/endpoint/Harness | task scores + failures | updating Peak directly |
| `lineage/` | immutable persistence | all upstream evidence | manifests, pointers, audit lookup | quality policy |
| `harness/` | non-parametric search and trace harvest | failures/tasks/accepted Harness | candidate snapshots/training traces | direct model weight mutation |
| `tests/` | transition proof | deterministic fixtures | assertions | network/API/GPU dependency |

## Data flow and evidence flow

### Current runnable data flow

```mermaid
flowchart LR
    C[PipelineConfig] --> G[DeterministicGenerator]
    G --> L[CostLedger]
    G --> V[VerificationPipeline]
    V -->|raw/accepted/quarantine/audit| S[ArtifactStore iteration bundle]
    V -->|accepted JSONL + SHA-256| T[Trainer]
    T --> P[Checkpoint artifact]
    P --> D[ServingAdapter]
    P --> E[Evaluator]
    E --> R[RSIRunResult report]
```

### Target complete evidence graph

```mermaid
flowchart TD
    FT[Failure traces + active/Peak IDs] --> DH[Diagnostic + hypothesis]
    DH --> TP[Teacher model/API + Prompt hash]
    TP --> RAW[raw.jsonl + synthesis_manifest.json]
    RAW --> FD[filter_audit.jsonl]
    FD --> ACC[accepted.jsonl + dataset SHA-256]
    FD --> Q[quarantine.jsonl + reasons]
    ACC --> AR[optional approval request/decision]
    AR --> TJ[training job + idempotency key]
    TJ --> CK[checkpoint artifact + SHA-256 + parent]
    CK --> EP[ephemeral serving endpoint]
    EP --> EV[task-family scores + failure traces]
    EV --> DC[Peak comparison + approval + decision]
    DC --> LM[lineage_manifest.json + decision.json]
    LM --> PP[atomic peak_checkpoint.json]
    EV --> FT
```

## Evidence bundle

Current iteration bundle:

```text
<workspace>/
├── iterations/iter-001/
│   ├── raw.jsonl
│   ├── accepted.jsonl
│   ├── quarantine.jsonl
│   ├── filter_audit.jsonl
│   ├── synthesis_manifest.json
│   └── dataset_summary.json
├── checkpoints/<checkpoint-id>/
│   └── weights.mock.json
└── reports/rsi-run-summary.json
```

Target integration additionally persists:

```text
checkpoints/<checkpoint-id>/checkpoint.json
checkpoints/<checkpoint-id>/lineage_manifest.json
iterations/iter-N/evaluation.json
iterations/iter-N/decision.json
approvals/pending/<request-id>.json
approvals/decisions/<request-id>.json
peak_checkpoint.json
reports/regression-audit-<checkpoint-id>.json
harness/<harness-version>.json
```

## Verification order

The order is intentional and cheap checks run first:

1. exact content-hash duplicate;
2. Shannon entropy, Distinct-2, and Type-Token Ratio;
3. semantic novelty against accepted history;
4. Benchmark N-gram overlap and LCS ratio;
5. prompt/role injection and safety checks;
6. optional Python AST import/call allowlist.

Only accepted examples enter semantic history and the accepted-dataset hash.

## Control-plane invariants

- Latest is not Peak.
- No training record lacks a filter decision.
- Rejected candidates never become parents.
- Budget/retry/recursion/wall-time limits fail closed.
- Quarantine and rejection are durable, traceable states.
- Serving teardown occurs even when evaluation fails.
- Human approval is explicit and immutable when enabled.
- The deterministic CI path never requires network, API keys, GPUs, or production endpoints.
- Generated code is not executed in the core runtime.
- Git changes are never an implicit side effect of Harness optimization.

## Documentation and traceability index

| Need | Document |
|---|---|
| Agent operating contract | [`AGENTS.md`](AGENTS.md) |
| Exact current implementation and gaps | [`docs/implementation-status.md`](docs/implementation-status.md) |
| State guards, transitions, and evidence | [`docs/state-machine.md`](docs/state-machine.md) |
| PDF requirement mapping | [`docs/traceability-index.md`](docs/traceability-index.md) |
| Molecular implementation/merge plan | [`docs/stacked-pr-plan.md`](docs/stacked-pr-plan.md) |
| Target architecture | [`docs/architecture.md`](docs/architecture.md) |
| Production controls | [`docs/productionization.md`](docs/productionization.md) |

## Git Town and molecular Stack PRs

Git Town is **not admitted** for this repository yet. There is no checked-in configuration, exact version pin, verified parent graph, or active stack manifest. Automation must fail closed rather than infer hierarchy from branch names.

The proposed review graph is:

```text
feat/pdf-architecture
└── PR-01 docs/repository-contracts
    └── PR-02 feat/state-domain-contracts
        ├── PR-03 feat/rsi-loop-policy
        ├── PR-04 feat/lineage-runtime
        ├── PR-05 feat/adapter-runtime
        └── PR-06 feat/hitl-approval
             \__ PR-07 feat/rsi-convergence
                  ├── PR-08 feat/harness-outer-loop
                  └── PR-09 feat/trace-harvesting
                       \__ PR-10 feat/model-inner-loop
                            \__ PR-11 feat/coevolution-convergence
```

Shared interfaces are frozen in `PR-02`; path-disjoint work remains sibling PRs; `PR-07` and `PR-11` are explicit convergence PRs. Allowed paths, collision paths, required gates, merge order, and rebase owner are indexed in [`docs/stacked-pr-plan.md`](docs/stacked-pr-plan.md).

## External adapter boundary

Current protocols allow external Teacher, trainer, evaluator, and serving implementations, but the supported CLI does not select them yet. Production adapters must remain behind stable typed or environment/JSON contracts and must not leak provider SDK details into transition policy.

See [`docs/productionization.md`](docs/productionization.md) before enabling real inference, training, code execution, or endpoint mutation.
