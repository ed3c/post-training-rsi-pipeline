# Versioned control-plane contracts

Status: **Contract only** on `feat/state-domain-contracts`.

The package in `src/post_training_rsi/control_plane/` freezes the provider-neutral records that later RSI, lineage, adapter, approval, and Co-Evolution PRs must exchange. It does not change the reachable `demo` flow and it does not define transition adjacency or promotion policy.

## Package ownership

```text
src/post_training_rsi/control_plane/
├── __init__.py      public imports; no runtime composition
├── enums.py         states, events, stop reasons, actions, subjects, evidence kinds
├── records.py       EvidenceRecord, DecisionRecord, StateSnapshot, TransitionRecord
└── validation.py    strict schema validation and canonical JSON encoding
```

The schema identifier is:

```text
post-training-rsi.control/v1
```

Any incompatible field or semantic change requires a new schema version. Do not silently reinterpret a v1 record.

## Enum contracts

| Type | Responsibility | Must not decide |
|---|---|---|
| `ControlState` | Names current executable states, target five-stage RSI states, and target outer/middle/inner Co-Evolution states | Which state is legal after another state |
| `ControlEvent` | Names facts that may request a transition | Whether the event is sufficient to promote or stop |
| `StopReason` | Finite terminal taxonomy for completion, budgets, plateau, verification, approval, failures, regression, and cycle limits | Free-form diagnosis text |
| `DecisionAction` | `CONTINUE`, approval request, accept, promote, reject, quarantine, rollback, stop, or abort | Score thresholds and approval policy |
| `DecisionSubject` | Run, Dataset, Checkpoint, Harness, Trace Batch, or Serving Endpoint | Storage location |
| `EvidenceKind` | Stable evidence categories from config through regression audit | Artifact contents or provider SDK types |

`ControlState` intentionally contains both the current past-tense states such as `SYNTHESIZED` and the target command-style states such as `SYNTHESIZE`. This preserves exact traceability to the current and target diagrams while the controller migration is still incomplete.

## Record contracts

### `EvidenceRecord`

A durable pointer produced by one component. It binds:

```text
evidence_id
run_id
iteration
kind
producer
uri
created_at
optional sha256
JSON metadata
```

It does not embed model weights, benchmark contents, provider clients, or mutable file handles.

### `DecisionRecord`

An evidence-backed policy result for one subject. It binds:

```text
decision_id
run_id / iteration
subject_type / subject_id
action
reason_code / reason
evidence_ids
optional stop_reason
created_at
JSON metadata
```

Every decision requires at least one evidence ID. `STOP` and `ABORT` require an explicit `StopReason`. A stop reason cannot be attached to an unrelated action such as `PROMOTE` or `CONTINUE`.

### `StateSnapshot`

A serializable state projection that can be persisted or handed to another controller without provider-specific objects. It carries iteration/cycle counters, active/candidate/Peak Checkpoint IDs, active/candidate Harness IDs, scores, plateau count, total cost, evidence references, and an optional terminal reason.

Terminal states `COMPLETED`, `STOPPED`, `ABORTED`, and `ROLLED_BACK` require a stop reason. Non-terminal states reject one.

### `TransitionRecord`

An idempotent transition fact:

```text
transition_id
run_id / iteration
from_state
ControlEvent
to_state
occurred_at
idempotency_key
optional decision_id
evidence_ids
JSON metadata
```

Every transition requires evidence. Only a `START` event may use `from_state = null`, and a `START` event must not declare a previous state.

The record deliberately does **not** validate the state adjacency graph. `PR-03` owns RSI transition policy, `PR-08` owns Harness outer-loop policy, and convergence PRs own composition. Keeping adjacency out of the data contract prevents provider, persistence, and approval modules from redefining orchestration policy.

## Fail-closed serialization rules

All four records enforce:

- an exact field set; unknown and missing fields are rejected;
- exact `schema_version` and `record_type` matching;
- safe non-path identifiers with no slash traversal;
- timezone-aware ISO-8601 timestamps normalized to UTC;
- finite numeric values; no `NaN` or infinity;
- non-negative iteration, cycle, plateau, and cost counters where applicable;
- unique evidence IDs;
- lowercase 64-character SHA-256 when a content hash is supplied;
- JSON-only metadata with deterministic key ordering;
- canonical UTF-8 JSON using sorted keys and compact separators.

`to_dict()` returns a detached JSON structure. `to_json()` produces deterministic bytes for hashing, audit comparison, and idempotency tests.

## Integration obligations for successor PRs

| Successor | Required use of this contract |
|---|---|
| `PR-03` RSI loop policy | Emit `StateSnapshot`, `TransitionRecord`, and `DecisionRecord` for promote/reject/rollback/plateau/budget paths |
| `PR-04` lineage runtime | Persist records atomically and verify referenced evidence/artifact hashes |
| `PR-05` adapter runtime | Convert provider outputs into `EvidenceRecord`; never store SDK objects in state |
| `PR-06` HITL approval | Represent immutable approval requests/decisions as evidence-backed decisions without weakening fail-closed semantics |
| `PR-07` RSI convergence | Wire the supported CLI/runtime to these records and assert exact evidence files |
| `PR-08` to `PR-11` Co-Evolution | Reuse the same records for Harness mutation, trace harvesting, model promotion/rollback, hot-swap, slim, and cycle stop |

A successor must not add an alternate state/event/stop taxonomy in its own module. New values require review in `control_plane/enums.py`, tests, and synchronized documentation.

## Verification evidence

Deterministic tests are in `tests/test_control_plane.py`. They cover:

- canonical round-trip serialization;
- detached metadata;
- unknown fields and unsupported schema versions;
- invalid enums, hashes, timestamps, costs, and JSON values;
- evidence requirements and duplicate evidence rejection;
- terminal/non-terminal stop reason rules;
- `START` transition guards;
- explicit proof that adjacency policy is not embedded in the record layer.

The reachable engine does not import these contracts yet. Until a supported runtime emits them, their status remains **Contract only**, not Implemented.
