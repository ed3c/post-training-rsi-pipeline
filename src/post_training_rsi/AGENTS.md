# AGENTS.md — `src/post_training_rsi/`

This file narrows the root [`AGENTS.md`](../../AGENTS.md) for control-plane source code. Root policy remains authoritative.

## Read before editing

1. `../../AGENTS.md`
2. `../../README.md` directory/state ownership table
3. `../../docs/implementation-status.md`
4. `../../docs/state-machine.md`
5. `../../docs/traceability-index.md`
6. the tests covering the module being changed

## Dependency direction

```text
config/models/domain records
        ↓
provider-neutral protocols and deterministic components
        ↓
state-machine controller/composition
        ↓
CLI
```

`verification/`, `training/`, `evaluation/`, `serving/`, `synthesis/`, and `lineage/` must not import transition policy from the controller. Provider SDK details remain inside adapters.

## State ownership

- `config.py`: validation only.
- `models.py` or future `domain.py`: serializable records and enums only.
- `engine.py` or future `orchestration/`: transition order, guards, stop reasons, promotion/rollback policy.
- `verification/`: deterministic record admission and quarantine.
- `training/`: candidate artifact production.
- `serving/`: deploy/readiness/teardown lifecycle.
- `evaluation/`: benchmark evidence and failure trajectories.
- `lineage/`: persistence and lookup; never model-quality decisions.
- `harness/`: candidate mutation and trace harvesting; never direct model weight updates.

## Change requirements

For a new or changed state/edge:

- define a typed state/event/stop reason;
- specify entry guard and exit evidence;
- make retries bounded and idempotent;
- add positive, negative, and rollback tests where applicable;
- update `README.md`, `docs/state-machine.md`, `docs/implementation-status.md`, and `docs/traceability-index.md` in the same PR.

Never preserve backward compatibility by silently accepting ambiguous evidence. Fail closed with an explicit reason.
