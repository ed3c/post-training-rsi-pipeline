# AGENTS.md — `tests/`

This file narrows the root [`AGENTS.md`](../AGENTS.md) for deterministic validation.

## Required read order

1. `../AGENTS.md`
2. `../README.md`
3. `../src/post_training_rsi/AGENTS.md`
4. closest component `AGENTS.md`
5. `../docs/state-machine.md`
6. `../docs/rsi-convergence.md`
7. relevant component contract
8. this file

## Test evidence rules

- Tests are evidence for the exact code under test, not evidence for a different branch or an earlier commit.
- A component unit test proves component behavior only. Supported-runtime claims require composition or CLI evidence.
- Tests must assert durable records and hashes where persistence is part of the contract; return values alone are insufficient.
- Default tests require no network, API key, GPU, cloud account, Docker daemon, or mutable production service.
- Time, randomness, model/provider output, and filesystem roots must be deterministic or injected.
- Every rejection test should assert the specific failure boundary, not only a broad exception type.
- Do not weaken a test merely to accommodate a broken implementation. Update the contract explicitly or fix the implementation.

## Required matrices

### Control-plane schema

```text
unknown/missing fields
wrong primitive types
non-finite numbers
unsafe IDs
invalid timestamps
invalid SHA-256
terminal State without StopReason
non-terminal State with StopReason
canonical round trip
```

### RSI policy

```text
strict promotion
threshold equality rejection
ordinary rejection
regression rollback
plateau stop
max-iteration stop
final-iteration promotion before stop
per-iteration and total budget crossing
exact budget allowed
Candidate parent mismatch
active/Peak mismatch
iteration/Candidate mismatch
idempotent record identities
```

### Verification

```text
exact duplicate
entropy
Distinct-2
TTR
semantic novelty
N-gram contamination
LCS contamination
safety
Python AST/import policy
accepted/quarantine bundle and exact Dataset hash
```

### Adapter runtime

```text
strict config and unknown fields
string boolean and shell-string rejection
bounded timeout/retry
stale result deletion/replay
request/idempotency/operation mismatch
minimal secret environment
Dataset/parent/provider echo mismatch
path escape and symlink
controller artifact re-hash
worker hash mismatch
endpoint handoff
teardown on success and failure
dual evaluation/teardown failure preservation
```

### Approval

```text
deterministic sample and input-order invariance
exact Request replay
conflicting Request/Decision bytes
missing/pending/approved/denied/expired
Request SHA substitution
Run/iteration/Subject type/ID/action/hash substitution
unauthorized reviewer role
Decision before Request
malformed/symlinked/tampered records
require_approved fail-closed behavior
```

### Lineage

```text
immutable transaction replay/conflict
orphan record not committed
cross-Run evidence rejection
future evidence rejection
Decision/Transition/Snapshot lineage consistency
lock timeout
file/directory hash determinism
symlink rejection
Checkpoint bundle identity and tamper
Peak stale writer
non-PROMOTE Peak mutation
score/iteration monotonicity
quarantine action/subject/evidence conflict
```

### Converged RSI

```text
new Run creation
same Run/config resume
Run/config mismatch rejection
multi-iteration promote/reject/stop
accepted Peak remains parent
Dataset approval pause/list/review/resume
Checkpoint approval pause/list/review/resume
pending/denied/expired approval leaves Peak unchanged
provider/integrity/evaluation failure does not fabricate score or Checkpoint
control transaction committed before Peak mutation
Checkpoint audit reload
compatibility demo remains supported
CLI JSON is finite and parseable
```

A future Co-Evolution matrix belongs to PR #8–#11 and must not be marked current before `coevolve` is supported.

## Test placement

Place tests beside the owner contract:

| Behavior | Preferred test area |
|---|---|
| schema/record validation | `test_control_plane.py` |
| Candidate decision policy | `test_rsi_policy.py` |
| lineage transaction/bundle/Peak/marker | `test_lineage*.py`, Peak tests |
| adapters and lifecycle | `test_adapter_runtime.py`, `test_adapters.py` |
| approval authority | `test_approval.py` |
| compatibility engine | `test_engine.py` |
| CLI and integrated controller | `test_cli.py` and convergence-focused tests |
| verification gates | `test_verification.py` |

Do not put component policy assertions only in a large end-to-end test. Preserve fast focused tests plus a small number of high-value composition tests.

## Filesystem and replay discipline

- Use isolated temporary workspaces.
- Never share mutable workspace paths across parallel tests.
- Assert exact record IDs, transaction relationships, and hashes when deterministic.
- Exercise exact replay and conflicting replay separately.
- Simulate interruption by leaving orphan files without a transaction marker.
- Do not delete locks automatically in tests unless testing explicit human recovery tooling.
- Verify that rejection/denial/rollback leaves the accepted Peak and parent unchanged.

## CLI test discipline

CLI tests must:

```text
invoke public argv
parse stdout as JSON
assert exit code
assert no NaN/Infinity
inspect durable artifacts
avoid relying on terminal formatting
```

For `review`, pass the exact Request SHA-256 returned by `approvals`; do not bypass the authority binding in a fixture.

## Validation gate

```bash
python -m compileall -q src tests
ruff check src tests
mypy src
python -m pytest -q \
  --cov=post_training_rsi \
  --cov-report=term-missing \
  --cov-fail-under=75
```

Then smoke the supported CLI paths affected by the change. Record exact commit and environment. A prior green commit does not validate the current head.

## Prohibited shortcuts

- No live paid API in the default suite.
- No secret fixtures containing real credentials.
- No sleeping for arbitrary wall-clock intervals when an injected clock is possible.
- No broad `except Exception: pass` assertions.
- No snapshot update that hides a semantic change.
- No marking a target State reachable solely because it exists in an enum.
- No lowering coverage or deleting negative tests without an explicit reviewed rationale.

<!-- PR13_FORENSIC_RECOVERY_INDEX_START -->
## Forensic recovery tests

Recovery tests must use temporary local directories and cover deterministic identity, deduplication, exact fields, path containment, symlinks, special files, tamper, retained locks, new-destination-only staging, exact staged bytes, structured exit codes, and absence of activation. They must not require private production data, network access, API keys, cloud storage, GPU, or mutable production services.
<!-- PR13_FORENSIC_RECOVERY_INDEX_END -->
