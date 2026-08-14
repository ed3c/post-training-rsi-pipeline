# AGENTS.md — `src/post_training_rsi/`

This file narrows the root [`AGENTS.md`](../../AGENTS.md) for control-plane source code. Root policy remains authoritative.

## Read before editing

1. `../../AGENTS.md`
2. `../../README.md` directory/state ownership table
3. `../../docs/implementation-status.md`
4. `../../docs/state-machine.md`
5. `../../docs/control-plane-contracts.md`
6. `../../docs/traceability-index.md`
7. the tests covering the module being changed

## Dependency direction

```text
config + control_plane + portable data records
        ↓
provider-neutral protocols and deterministic components
        ↓
state-machine controller/composition
        ↓
CLI
```

`verification/`, `training/`, `evaluation/`, `serving/`, `synthesis/`, and `lineage/` must not import transition policy from the controller. Provider SDK details remain inside adapters.

## State and record ownership

- `config.py`: validation only.
- `control_plane/enums.py`: the only shared state, event, stop-reason, action, subject, and evidence-kind taxonomy.
- `control_plane/records.py`: versioned evidence, decision, state-snapshot, and transition records only.
- `control_plane/validation.py`: strict schema and canonical JSON validation; no state adjacency policy.
- `models.py`: current data/result payloads; do not duplicate the control-plane taxonomy.
- `engine.py` or future `orchestration/`: transition order, guards, stop reasons, promotion/rollback policy.
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
- Adjacency and promotion thresholds belong to orchestration PRs, not the record package.
- Incompatible changes require a new schema version and synchronized tests/docs.

## Change requirements

For a new or changed state/edge:

- extend the shared typed state/event/stop reason only when an existing value cannot express the fact;
- specify entry guard and exit evidence;
- make retries bounded and idempotent;
- add positive, negative, serialization, and rollback tests where applicable;
- update `README.md`, `docs/state-machine.md`, `docs/control-plane-contracts.md`, `docs/implementation-status.md`, and `docs/traceability-index.md` in the same PR.

Never preserve backward compatibility by silently accepting ambiguous evidence. Fail closed with an explicit reason.
