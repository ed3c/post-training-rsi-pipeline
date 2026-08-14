# AGENTS.md — `src/post_training_rsi/harness/trace_harvesting/`

Read the repository root, `src/post_training_rsi/AGENTS.md`, and the parent Harness outer-loop contract first.

## Purpose

This package owns the **implemented successful observable-trace middle-loop component**:

```text
HARVEST_TRACES
  → deterministic successful-trace selection
  → VERIFY_TRACES through the common data gates
  → HARVEST_TRACES retry | TRAIN_MODEL handoff | QUARANTINED/STOPPED/ABORTED
```

It accepts only observable task inputs, tool calls/results, state observations, final outputs, errors, scores, statuses, and non-secret metadata. It must not request, infer, store, or label hidden chain-of-thought, scratchpads, private reasoning, or hidden model state.

## Ownership

This package owns:

- exact observable trace/step contracts;
- content-addressed Trace and batch identities;
- deterministic successful-trace selection independent of input order;
- frozen model/Harness lineage checks;
- minimum score, task-family cap, duplicate, and target-count decisions;
- conversion to observable-only training examples;
- reuse of `VerificationPipeline` for diversity, novelty, contamination, safety, and code gates;
- immutable local Trace Dataset/audit/quarantine bundle and exact SHA-256;
- in-memory EvidenceRecords for Trace Dataset, audit, and quarantine;
- `HARVEST_TRACES`/`VERIFY_TRACES` retry, quarantine, budget, batch-limit, and `TRAIN_MODEL` handoff policy;
- paired Decision/Transition/Snapshot records for those edges.

It must not own:

- model training, evaluation, promotion, rollback, or hot-swap;
- Harness mutation or acceptance policy;
- persistent control transaction or Peak/Harness pointer mutation;
- Dataset approval authority or reviewer authentication;
- production task execution;
- production storage/provider credentials;
- root CLI or root integration documentation;
- final Co-Evolution convergence.

PR #10 owns model inner-loop behavior. PR #11 owns persistence, approval binding, resume, and `coevolve` composition.

## Observable-only invariant

Allowed event types:

```text
TASK_INPUT
TOOL_CALL
TOOL_RESULT
STATE_OBSERVATION
FINAL_OUTPUT
ERROR
```

Forbidden metadata keys include normalized forms of:

```text
analysis
chain_of_thought
cot
hidden_reasoning
hidden_state
internal_reasoning
private_reasoning
reasoning
scratchpad
thought
thoughts
```

Do not evade the restriction by renaming hidden reasoning to a new metadata field. A future schema addition must be reviewed against the observable-only contract.

## Frozen lineage invariants

```text
current.active_checkpoint_id == current.peak_checkpoint_id
trace.model_checkpoint_id == current.active_checkpoint_id
trace.harness_id == current.active_harness_id
trace.run_id == current.run_id
trace.cycle == current.cycle
```

Only successful traces above the configured score floor are eligible. Selected traces must have a `TASK_INPUT` and successful traces must contain `FINAL_OUTPUT`.

## Dataset invariants

```text
same VerificationPipeline as other training data
accepted.jsonl SHA-256 computed from exact bytes
filter config hash recorded
Trace/Model/Harness/Run/Cycle lineage recorded
immutable path conflict fails closed
symlinked output path fails closed
verification evidence exists before policy handoff
```

The trace conversion contains observable inputs/actions/outcome only. It never creates a `reasoning` or hidden-state training field.

## State policy invariants

- Exact per-batch/total budget limits are allowed; crossing aborts.
- A zero-accepted or low-acceptance batch is quarantined.
- Quarantine may retry another batch while limits remain.
- Cumulative accepted count reaches the configured target before `TRAIN_MODEL` handoff.
- Exhausting the batch limit before target produces explicit `STOPPED(MAX_ITERATIONS)`.
- `TRAIN_MODEL` is a typed handoff, not proof that training occurred.
- Every edge has non-empty evidence IDs and paired Decision/Transition/Snapshot records.
- Cross-Run, cycle, model, Harness, batch, or Dataset substitution fails closed.

## Validation requirements

```text
trace content identity and exact round trip
contiguous step indexes
required tool identity
hidden-reasoning metadata rejection
successful trace requires FINAL_OUTPUT
input-order invariant selection
duplicate/unsuccessful/low-score/lineage rejection
task-family cap and target bound
observable-only conversion
common-gate verification and exact Dataset hash
immutable replay and conflict detection
Trace Dataset EvidenceRecord mapping
HARVEST_TRACES → VERIFY_TRACES
zero/low acceptance quarantine and retry
cumulative target → TRAIN_MODEL
batch limit → STOPPED
budget exact/crossing matrix
frozen model/Harness invariants
deterministic paired control records
parent demo/rsi/audit compatibility
```

Default tests require no network, API key, GPU, Docker daemon, cloud account, or hidden model output.

## Delivery boundary

This PR is an **Implemented component** until PR #11 persists its records, binds Dataset approval where required, composes durable resume, connects `TRAIN_MODEL` to PR #10, and exposes the full Co-Evolution CLI.

Do not update root integration truth from this branch. Record successor obligations in the PR body and `docs/trace-harvesting.md`.
