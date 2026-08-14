# AGENTS.md — Post-Training RSI Pipeline

This file is the repository-wide operating contract for coding Agents and human contributors. Read it before editing code, configuration, tests, documentation, CI, branches, or Pull Requests.

## 1. Required read order

Read the following in order:

1. `AGENTS.md`
2. `README.md`
3. the closest scoped `AGENTS.md` above the files you will change
4. `docs/README.md`
5. `docs/implementation-status.md`
6. `docs/state-machine.md`
7. `docs/rsi-convergence.md` for supported runtime work
8. the component contract for the directory being changed
9. `docs/traceability-index.md`
10. `docs/stacked-pr-plan.md`
11. `tests/AGENTS.md` before changing behavior or evidence claims

Closest-scope rules extend this file and may narrow allowed paths. They do not override repository invariants.

## 2. Source-of-truth order

When sources disagree, use this order:

1. executable code on the exact branch and commit under review;
2. deterministic tests and generated evidence from that exact commit;
3. serialized schemas and component contracts;
4. `docs/implementation-status.md` and `docs/rsi-convergence.md`;
5. `README.md` and other explanatory documentation;
6. target architecture diagrams;
7. issue or conversation prose.

Update stale prose. Never change code merely to preserve an obsolete documentation claim.

## 3. Truth vocabulary

Use only these status labels:

- **Supported** — reachable from a checked-in CLI or supported API path and covered by deterministic evidence.
- **Implemented component** — coded and tested in isolation but not reachable from the supported composition root.
- **Contract only** — schema/protocol exists; executable behavior is not implemented.
- **Partial** — some required edges or evidence exist, but the capability is incomplete.
- **Planned** — target behavior only.
- **Verified** — an exact commit passed the named gate; always include the commit and evidence source.
- **Not verified** — code may exist, but the required execution evidence is absent.

An enum value, dataclass, diagram, fixture, or isolated unit test is not proof that a supported runtime reaches a state.

## 4. Current integration boundary

On `feat/rsi-convergence` / Draft PR #7, the supported CLI surface is:

```text
demo
rsi
verify
audit
approvals
review
```

`demo` is the compatibility one-pass path. `rsi` is the converged recursive controller. `verify`, `audit`, `approvals`, and `review` are operational evidence and HITL commands.

The following are not yet supported runtime claims:

```text
coevolve
real Teacher API execution
real GPU SFT/DPO
live vLLM/SGLang lifecycle
production Inspect AI/lm-eval
remote DVC/lakeFS/MLflow transactions
enterprise reviewer identity
multi-region/distributed writer safety
```

The branch is not merged to `main`. Do not describe PR #7 behavior as default-branch behavior until the merge is complete.

## 5. Directory ownership

| Path | Owns | Must not own |
|---|---|---|
| `src/post_training_rsi/__main__.py` | CLI parsing and dispatch | hidden policy or provider-specific logic |
| `config.py` | strict BOOT validation and defaults | transition or promotion decisions |
| `control_plane/` | State/Event/Stop/Action/Subject/Evidence representation | adjacency, thresholds, SDK calls, persistence |
| `orchestration/rsi_policy.py` | Peak/reject/rollback/plateau/budget policy | filesystem, provider SDK, approval authentication |
| `orchestration/converged.py` | sequencing, composition, durable resume | weakening child-component invariants |
| `orchestration/run_state.py` | immutable Run identity/config hash/clock/resume metadata | model quality decisions |
| `adapter_runtime/` | bounded provider execution, result validation, evidence translation | Peak, rollback, or approval authority |
| `approval/` | immutable Dataset/Checkpoint/Harness authority | reviewer authentication implementation or score policy |
| `verification/` | data admission, diversity, contamination, safety, code gates | model-quality decisions |
| `training/` | Candidate creation from exact Dataset + parent | benchmark/promotion policy |
| `serving/` | endpoint deployment, readiness, teardown | promotion policy |
| `evaluation/` | benchmark scores and failure traces | direct Peak mutation |
| `lineage/` | immutable transactions, Checkpoint bundles, Peak CAS, quarantine | deciding whether a score is sufficient |
| `cost.py` | cost ledger and provider-circuit facts | quality policy |
| `harness/` | future Harness search and trace-harvest components | model weight updates or unsupported CLI claims |
| `tests/` | deterministic evidence | network/API/GPU dependency by default |
| `docs/` | current/component/target separation and traceability | unsupported completion claims |

When a change requires crossing ownership boundaries, split it into component and convergence commits or PRs. Do not let the composition root absorb component policy for convenience.

## 6. State Machine change protocol

A change to any of the following is structural:

```text
ControlState
ControlEvent
StopReason
DecisionAction
DecisionSubject
EvidenceKind
record field or schema version
transition guard or precedence
resume rule
artifact path or hash contract
approval authority rule
Peak update rule
CLI command
component owner
PR dependency
```

A structural change must update, in the same PR:

- implementation;
- deterministic tests;
- closest scoped `AGENTS.md` when ownership changes;
- `README.md`;
- `docs/implementation-status.md`;
- `docs/state-machine.md`;
- `docs/rsi-convergence.md` when the supported runtime changes;
- the relevant component document;
- `docs/traceability-index.md`;
- `docs/stacked-pr-plan.md` when branch/merge ownership changes.

Do not merge a structural change with stale State diagrams or traceability rows.

## 7. Non-negotiable runtime invariants

```text
active_checkpoint_id == peak_checkpoint_id
candidate.parent_checkpoint_id == active_checkpoint_id
candidate_score > peak_score + min_improvement
```

Also preserve:

- Equality at the promotion threshold is rejection.
- Rejected or rolled-back Candidates never become the next parent.
- A valid final-iteration improvement is committed before max-iteration stop.
- Exact budget limits are allowed; crossing a limit aborts.
- Peak mutation requires a committed `PROMOTE` Decision for the same Checkpoint.
- Peak mutation uses compare-and-swap against the expected previous Peak.
- Peak iteration cannot move backward and Peak score must increase strictly.
- Worker artifact hashes are recomputed by the controller.
- Artifact paths are confined and symlinks rejected unless an explicit, reviewed external-storage policy says otherwise.
- Dataset, Checkpoint, and Harness approvals bind Subject type, ID, SHA-256, Run, and iteration.
- Missing, pending, denied, expired, malformed, unauthorized, or mismatched approval fails closed.
- Serving teardown runs in `finally` and its failure is not discarded.
- Control records cannot reference cross-Run or future evidence.
- A transaction marker is written last; orphan files are not committed evidence.
- Reusing an immutable ID with different bytes is a conflict.
- Resume state comes from durable metadata, transactions, snapshots, and Peak state, not process memory.
- Secrets and raw private review content do not enter generic metadata or logs.

If a requested change violates an invariant, stop and propose a versioned migration or a separate policy discussion. Do not silently weaken the invariant.

## 8. Evidence-first implementation contract

Every task packet must state:

```yaml
purpose: why this slice exists
baseline: exact branch and commit
allowed_paths: files/directories the Agent may change
excluded_paths: files/directories the Agent must not change
dependencies: parent PRs, schemas, or services
state_edges: exact states/events affected
inputs: typed inputs and hashes
outputs: artifacts and records
required_evals: deterministic gates
evidence_boundary: what this PR proves and does not prove
rollback_subject: smallest safe revert unit
collision_paths: likely merge-conflict paths
rebase_owner: one named owner
human_owned_operations: actions the Agent must not perform autonomously
```

A task is not complete until the stated evidence exists or the missing evidence is explicitly recorded as a blocker.

## 9. Validation gates

For Python behavior changes, run from a clean environment:

```bash
python -m compileall -q src tests
ruff check src tests
mypy src
python -m pytest -q \
  --cov=post_training_rsi \
  --cov-report=term-missing \
  --cov-fail-under=75
```

Supported CLI smoke paths:

```bash
python -m post_training_rsi --workspace /tmp/rsi-demo demo
python -m post_training_rsi \
  --workspace /tmp/rsi-run \
  --run-id smoke-run \
  rsi
```

When a Checkpoint is produced, audit it with the exact workspace and ID. When approvals are enabled, test pause/list/review/resume with exact Request SHA-256.

Do not substitute an earlier commit's green run for the current head. Record:

```text
commit SHA
workflow/run ID or local environment
commands
pass/fail result
known skips
```

Network, API, GPU, Docker, and cloud-dependent tests are opt-in and must have explicit credentials, budget, teardown, and secret-handling contracts.

## 10. Failure handling

Fail closed on:

- unknown config/schema fields;
- malformed or non-finite numbers;
- missing evidence or unresolved references;
- cross-Run/future evidence;
- Dataset or artifact hash mismatch;
- path escape or symlink;
- stale or conflicting idempotency records;
- approval ambiguity;
- stale Peak compare-and-swap;
- missing teardown evidence;
- resume configuration mismatch;
- unsupported State transition;
- unverified production operation.

Do not catch broad exceptions merely to emit `completed`. Preserve the original error, persist allowed failure evidence, attempt required teardown, and leave the accepted Peak unchanged.

## 11. Pull Request graph and collision ownership

Actual ordinary GitHub graph:

```text
PR #1  repository and Agent contracts
└── PR #2  State-domain contracts
    ├── PR #3  RSI decision policy
    ├── PR #4  transactional lineage runtime
    ├── PR #5  adapter runtime
    └── PR #6  HITL approval
         \__ PR #7  RSI convergence
```

PR #7 is the single owner for integrated root documentation and composition conflicts. Sibling component PRs should not independently rewrite root integration truth after convergence begins.

Proposed successor graph:

```text
PR #7
├── PR #8  Harness outer loop
├── PR #9  trace harvesting
└── PR #10 model inner loop
     \__ PR #11 Co-Evolution convergence
```

Use [`docs/stacked-pr-plan.md`](docs/stacked-pr-plan.md) for allowed paths, merge order, collision paths, gates, and rollback subjects.

## 12. Git Town admission

Git Town is currently disabled. Do not invoke Git Town mutating commands until all of the following are committed and reviewed:

```text
exact version pin
repository Git Town configuration
verified branch-parent graph
linked-worktree leases/ownership
non-interactive dry run
no-push rehearsal evidence
active stack.tsv
human approval for ref mutation
```

Until then, use ordinary GitHub branches and PRs. Documentation may describe a proposed Stack but must label it non-executable.

## 13. Human-owned operations

Agents must not autonomously perform:

- merging or shipping the PR stack;
- rewriting shared branch history;
- deleting branches or production artifacts;
- stale-lock recovery;
- provisioning or rotating secrets;
- changing cloud quota, billing, or GPU capacity;
- mutating production endpoints;
- assigning production reviewer roles;
- approving their own Dataset, Checkpoint, or Harness request;
- enabling unrestricted environment inheritance or external artifact paths;
- changing retention/disaster-recovery policy;
- enabling Git Town;
- claiming production readiness without production evidence.

## 14. Documentation language

Use exact repository paths, State names, schema versions, artifact names, PR numbers, and commit SHAs. Separate current supported behavior from implemented components and target behavior.

Prefer diagrams that expose guards, evidence, and terminal reasons. Avoid diagrams that imply a happy path without failure, approval, rollback, budget, or teardown edges.
