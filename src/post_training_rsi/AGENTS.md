# AGENTS.md — `src/post_training_rsi/`

This file narrows the root [`AGENTS.md`](../../AGENTS.md) for control-plane and orchestration source code. Root policy remains authoritative.

## Read before editing

1. `../../AGENTS.md`
2. `../../README.md` directory/state ownership table
3. `../../docs/implementation-status.md`
4. `../../docs/state-machine.md`
5. `../../docs/control-plane-contracts.md`
6. `../../docs/rsi-loop-policy.md`
7. `../../docs/traceability-index.md`
8. the tests covering the module being changed

## Dependency direction

```text
config + control_plane + portable data records
        ↓
provider-neutral protocols and deterministic components
        ↓
orchestration policy and convergence composition
        ↓
CLI
```

`verification/`, `training/`, `evaluation/`, `serving/`, `synthesis/`, and `lineage/` must not import transition policy from the controller. Provider SDK details remain inside adapters.

## State and record ownership

- `config.py`: validation only.
- `control_plane/enums.py`: the only shared state, event, stop-reason, action, subject, and evidence-kind taxonomy.
- `control_plane/records.py`: versioned evidence, decision, state-snapshot, and transition records only.
- `control_plane/validation.py`: strict schema and canonical JSON validation; no state adjacency policy.
- `orchestration/rsi_policy.py`: pure candidate decision boundary—strict Peak comparison, parent invariant, reject/rollback, plateau, max-iteration, and budget outcomes.
- future orchestration composition: diagnose/hypothesis/stage execution and adapter sequencing.
- `models.py`: current data/result payloads; do not duplicate the control-plane taxonomy.
- `engine.py`: current supported entry point; it does not yet use `RSIDecisionPolicy`.
- `verification/`: deterministic record admission and quarantine.
- `training/`: candidate artifact production.
- `serving/`: deploy/readiness/teardown lifecycle.
- `evaluation/`: benchmark evidence and failure trajectories.
- `lineage/`: persistence and lookup; never model-quality decisions.
- `harness/`: candidate mutation and trace harvesting; never direct model weight updates.

## Control-plane schema rules

The current schema is `post-training-rsi.control/v1`.

- Import public types from `post_training_rsi.control_plane`.
- Do not accept unknown fields or alternate spellings.
- Do not use free-form strings where a shared enum exists.
- Do not serialize provider clients, file handles, credentials, benchmark bodies, or model weights into records.
- Every `DecisionRecord` and `TransitionRecord` needs evidence IDs.
- Terminal states need an explicit `StopReason`; non-terminal states must not carry one.
- Incompatible changes require a new schema version and synchronized tests/docs.

## RSI policy invariants

- Input state is `EVALUATE` and iteration numbers match.
- `active_checkpoint_id == peak_checkpoint_id`.
- Candidate parent equals the current active accepted Checkpoint.
- Candidate Checkpoint is not its own parent.
- Promotion is strict: `candidate_score > peak_score + min_improvement`.
- Equality at the boundary is rejection.
- Rejected/rolled-back candidates do not replace active or Peak IDs.
- Exact budget limits are allowed; crossing a limit aborts.
- A final-iteration improvement is recorded before the run stops.
- Plateau reason takes precedence over max-iteration when both arise from the same rejected trial.
- Pure policy code emits records only; persistence and provider side effects remain outside it.

## Change requirements

For a new or changed state/edge:

- extend the shared typed State/Event/StopReason only when an existing value cannot express the fact;
- specify entry guard, precedence, and exit evidence;
- make retries bounded and idempotent;
- add positive, negative, boundary, serialization, parent-invariant, and rollback tests where applicable;
- update `README.md`, `docs/state-machine.md`, `docs/control-plane-contracts.md`, `docs/rsi-loop-policy.md`, `docs/implementation-status.md`, and `docs/traceability-index.md` as applicable.

Never preserve backward compatibility by silently accepting ambiguous evidence. Fail closed with an explicit reason.
