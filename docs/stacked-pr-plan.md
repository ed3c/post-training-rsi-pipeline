# Molecular Pull Request plan

Status: **ordinary GitHub PR graph; Git Town not configured**  
Current convergence PR: [#7](https://github.com/ed3c/post-training-rsi-pipeline/pull/7)  
Validated code head before the latest documentation commits: `ac334be8411f45196d2522c885ff893cb2d44fda`

This document assigns one responsibility, one collision owner, one evidence boundary, and one rollback subject to each Pull Request.

## 1. Actual graph

```text
PR #1  docs/agent-state-machine-index
└── PR #2  feat/state-domain-contracts
    ├── PR #3  feat/rsi-loop-policy
    ├── PR #4  feat/lineage-runtime
    ├── PR #5  feat/adapter-runtime
    └── PR #6  feat/hitl-approval
         \__ PR #7  feat/rsi-convergence
```

PR #3–#6 are component siblings from the stable PR #2 contract. PR #7 is based on PR #3 and contains the sibling implementations through an explicit convergence merge. This is a Git commit/PR graph, not a Git Town stack.

## 2. Completed component slices

### PR #1 — Repository and Agent contracts

```yaml
purpose: establish read order, truth labels, directory ownership, traceability, and PR planning
owner_paths:
  - AGENTS.md
  - README.md
  - docs/**
  - scoped AGENTS.md
excludes:
  - runtime behavior
required_evals:
  - documentation path/link review
  - current/component/target separation
rollback_subject: documentation and Agent contract commit set
```

### PR #2 — State-domain contracts

```yaml
purpose: freeze provider-neutral State/Event/Stop/Decision/Evidence language
owner_paths:
  - src/post_training_rsi/control_plane/**
  - tests/test_control_plane.py
  - docs/control-plane-contracts.md
excludes:
  - adjacency policy
  - persistence
  - provider calls
  - approvals
required_evals:
  - exact-field/schema/type/time/hash parsing
  - canonical JSON
  - terminal State/StopReason rules
rollback_subject: post-training-rsi.control/v1 implementation
```

### PR #3 — RSI decision policy

```yaml
purpose: decide promote/reject/rollback/continue/stop from an evaluated Candidate
parent: PR #2
owner_paths:
  - src/post_training_rsi/orchestration/rsi_policy.py
  - tests/test_rsi_policy.py
  - docs/rsi-loop-policy.md
excludes:
  - providers
  - persistence
  - approval authority
  - CLI composition
required_evals:
  - strict threshold
  - parent/Peak invariants
  - rollback
  - plateau/max/budget precedence
  - final-iteration promotion
rollback_subject: pure RSI decision-policy component
```

### PR #4 — Transactional lineage runtime

```yaml
purpose: persist control records, Checkpoint bundles, Peak CAS, and quarantine history
parent: PR #2
owner_paths:
  - src/post_training_rsi/lineage/**
  - tests/test_lineage*.py
  - tests/test_peak_pointer_monotonic.py
  - docs/lineage-runtime.md
excludes:
  - score policy
  - provider execution
  - approval authority
  - CLI composition
required_evals:
  - immutable/idempotent/conflicting transactions
  - orphan/uncommitted/cross-Run/future dependency rejection
  - artifact/bundle tamper detection
  - stale/non-PROMOTE/non-monotonic Peak rejection
  - quarantine marker integrity
rollback_subject: transactional lineage component
```

### PR #5 — Adapter runtime

```yaml
purpose: provide strict provider selection, bounded execution, artifact integrity, and serving lifecycle
parent: PR #2
owner_paths:
  - src/post_training_rsi/adapter_runtime/**
  - src/post_training_rsi/synthesis/**
  - src/post_training_rsi/training/**
  - src/post_training_rsi/evaluation/**
  - src/post_training_rsi/serving/**
  - adapter config/tests/docs
excludes:
  - Peak policy
  - persistence authority
  - HITL authority
  - CLI composition
required_evals:
  - strict config negative cases
  - stale/malformed/mismatch/timeout/retry command cases
  - Dataset/parent/provider echo checks
  - path escape/symlink/artifact hash cases
  - endpoint handoff and teardown matrix
rollback_subject: provider-boundary component
```

### PR #6 — HITL approval

```yaml
purpose: bind Dataset/Checkpoint/Harness release authority to immutable human Decisions
parent: PR #2
owner_paths:
  - src/post_training_rsi/approval/**
  - tests/test_approval.py
  - docs/hitl-approval.md
excludes:
  - reviewer authentication implementation
  - score policy
  - provider execution
  - Peak persistence
  - CLI composition
required_evals:
  - deterministic sample/order invariance
  - exact request/decision replay and conflict
  - missing/pending/approved/denied/expired matrix
  - subject/action/hash/role substitution rejection
rollback_subject: local HITL authority component
```

## 3. PR #7 — RSI convergence

Branch: `feat/rsi-convergence`  
Base PR: PR #3 / `feat/rsi-loop-policy`  
Integrated dependencies: PR #4, #5, #6  
Status: Draft

```yaml
purpose: compose the independent RSI components into a supported resumable controller and CLI
owner_paths:
  - src/post_training_rsi/orchestration/converged.py
  - src/post_training_rsi/orchestration/run_state.py
  - src/post_training_rsi/orchestration/__init__.py
  - src/post_training_rsi/__main__.py
  - convergence configuration glue
  - convergence tests
  - root README/AGENTS and integrated status documents
may_touch_for_integration:
  - lineage dependency validation and Peak monotonic guards
  - component exports required by the composition root
must_not:
  - weaken PR #3 promotion/parent/stop semantics
  - let providers update Peak
  - let persistence decide score quality
  - treat missing approval as granted
  - claim real cloud/GPU execution without evidence
  - expose coevolve before PR #11
required_evals:
  - compileall
  - Ruff
  - mypy
  - full pytest with coverage floor
  - demo smoke
  - multi-iteration RSI smoke
  - Checkpoint audit smoke
  - Dataset/Checkpoint approval pause-review-resume matrix when enabled
  - exact-head PR checks
collision_paths:
  - README.md
  - AGENTS.md
  - docs/README.md
  - docs/implementation-status.md
  - docs/state-machine.md
  - docs/rsi-convergence.md
  - docs/traceability-index.md
  - docs/stacked-pr-plan.md
  - src/post_training_rsi/__main__.py
  - src/post_training_rsi/config.py
  - src/post_training_rsi/orchestration/__init__.py
rebase_owner: ed3c
rollback_subject: convergence composition and synchronized root truth; component PRs remain independently reviewable
human_owned_operations:
  - merging or retargeting the PR graph
  - selecting production thresholds
  - enabling credentials or external infrastructure
  - marking ready/merging after exact-head evidence review
```

PR #7 data flow:

```text
Run/config
  → diagnosis/hypothesis
  → synthesis + cost
  → verification + Dataset hash
  → optional Dataset approval
  → training + artifact integrity
  → serving + endpoint handoff + teardown
  → evaluation
  → PR #3 decision policy
  → optional Checkpoint approval
  → PR #4 control transaction + Checkpoint bundle
  → Peak CAS or reject/rollback marker
  → next iteration or terminal report
```

Validation evidence already obtained for code head `ac334be8411f45196d2522c885ff893cb2d44fda`:

```text
compileall             PASS
Ruff                  PASS
mypy                  PASS
full pytest/coverage  PASS
compatibility demo    PASS
converged RSI smoke   PASS
Checkpoint audit      PASS
```

The PR-triggered check for that bot-authored commit required workflow approval and ran no jobs. PR #7 remains Draft until a normal green check set exists for the exact latest head.

## 4. Proposed successor graph

```text
PR #7  RSI convergence
├── PR #8  feat/harness-outer-loop
├── PR #9  feat/trace-harvesting
└── PR #10 feat/model-inner-loop
     \__ PR #11 feat/coevolution-convergence
```

PR #8 and PR #9 can begin as path-disjoint children of PR #7 after PR #7 contracts are stable. PR #10 depends on verified trace-Dataset contracts from PR #9. PR #11 is the sole integrated Co-Evolution documentation and CLI owner.

### PR #8 — Harness outer loop

```yaml
purpose: freeze the active model and search non-parametric Harness mutations
parent: PR #7
allowed_paths:
  - src/post_training_rsi/harness/mutator.py
  - src/post_training_rsi/harness/git_lineage.py
  - new Harness policy/evaluation modules
  - focused Harness tests/docs
excluded_paths:
  - model training implementation
  - trace Dataset transformation
  - root CLI
  - root integrated docs
state_edges:
  - FREEZE_MODEL -> MUTATE_HARNESS
  - MUTATE_HARNESS -> VALIDATE_HARNESS
  - VALIDATE_HARNESS -> EVALUATE_HARNESS | REJECT_HARNESS
  - EVALUATE_HARNESS -> ACCEPT_HARNESS | REJECT_HARNESS | HARNESS_REVIEW_PENDING
  - plateau -> HARVEST_TRACES handoff
required_evals:
  - deterministic mutation identity
  - static/policy validation
  - strict Harness improvement
  - approval binding when enabled
  - plateau and budget termination
rollback_subject: Harness search component only
```

### PR #9 — Successful trace harvesting

```yaml
purpose: turn observable successful task trajectories into a verified training Dataset
parent: PR #7 or PR #8 after stable handoff contract
allowed_paths:
  - src/post_training_rsi/harness/trace_harvester.py
  - trace contracts/transforms
  - focused trace verification tests/docs
excluded_paths:
  - model training
  - Peak/model promotion
  - root CLI
state_edges:
  - HARVEST_TRACES -> VERIFY_TRACES
  - VERIFY_TRACES -> TRAIN_MODEL handoff | quarantine
required_evals:
  - observable-field allowlist
  - no hidden chain-of-thought capture
  - deterministic trace identity
  - same diversity/decontamination/safety gates
  - exact accepted trace-Dataset hash
rollback_subject: trace middle-loop component
```

### PR #10 — Model inner loop

```yaml
purpose: train and evaluate a Candidate model from the verified trace Dataset
parent: PR #9
allowed_paths:
  - new model-inner orchestration modules
  - focused training/evaluation/hot-swap tests/docs
excluded_paths:
  - Harness mutation policy
  - root coevolve CLI
state_edges:
  - TRAIN_MODEL -> EVALUATE_MODEL
  - EVALUATE_MODEL -> PROMOTE_MODEL | ROLLBACK_MODEL
  - PROMOTE_MODEL -> SLIM_HARNESS handoff
required_evals:
  - exact Dataset/parent/artifact lineage
  - strict model improvement
  - approval before hot-swap when enabled
  - rollback keeps accepted model active
  - serving teardown/cost/evidence
rollback_subject: model inner-loop component
```

### PR #11 — Co-Evolution convergence

```yaml
purpose: compose PR #8/#9/#10 into a resumable Model/Harness Co-Evolution CLI
parent: convergence of PR #8/#9/#10
owner_paths:
  - Co-Evolution composition root
  - coevolve CLI
  - integrated tests
  - root README/AGENTS/status/state/traceability/stack docs
state_edges:
  - full outer/middle/inner cycle
  - SLIM_HARNESS -> FREEZE_MODEL reset
  - cycle/budget/plateau/approval terminal edges
required_evals:
  - deterministic end-to-end cycle
  - resume after every durable boundary
  - active model/Harness invariants
  - approval and rollback matrix
  - artifact/evidence lineage
  - cost and teardown
  - exact-head CI
rollback_subject: Co-Evolution composition; component PRs remain separately usable
```

## 5. Collision ownership

| Collision path | Owner |
|---|---|
| root README/AGENTS and integrated status docs through RSI | PR #7 |
| root README/AGENTS and integrated status docs for Co-Evolution | PR #11 |
| `control_plane/` schema changes | dedicated versioned schema PR before dependents |
| `config.py` shared fields | convergence owner after component contracts are stable |
| `orchestration/__init__.py` exports | current convergence owner |
| CI matrix | convergence owner, with component needs documented in task packet |
| migrations for persisted schemas | dedicated migration PR; never ad hoc sibling edits |

One path has one integration owner. Component siblings document needed integration changes in their PR body rather than racing to edit a shared file.

## 6. Merge and rebase discipline

Before merging a child or sibling:

```text
1. verify parent/base SHA
2. verify allowed/excluded paths
3. review collision paths
4. run exact-head gates
5. update requirement traceability
6. record rollback subject
7. rebase/retarget only by named owner
8. merge only with human approval
```

Do not force-push shared branches or rewrite a reviewed graph autonomously.

## 7. Git Town admission gate

Git Town remains fail closed. Required evidence before activation:

```yaml
version_pin: exact Git Town release committed
repository_config: committed and reviewed
parent_graph: complete and verified
worktree_leases: branch-to-owner mapping
non_interactive_rehearsal: passed
no_push_rehearsal: passed
stack_tsv: active, reviewed, and current
human_approval: explicit approval to mutate refs
```

Only after admission may a separate PR translate this documented graph into executable Git Town metadata. Until then:

- do not run `git town propose`, `sync`, or `ship`;
- do not describe sibling PRs as a valid Git Town Stack;
- do not infer parentage from branch names;
- use GitHub PR base/head metadata as the source of truth.

## 8. Human-owned operations

```text
merge/ship/retarget/rebase of shared branches
history rewrite or branch deletion
Git Town activation
production secrets and credentials
cloud/GPU quota and billing
production endpoint mutation
reviewer role assignment and approval
stale-lock recovery
retention and disaster recovery
production threshold acceptance
```
