# AGENTS.md — `src/post_training_rsi/`

This file narrows the root [`AGENTS.md`](../../AGENTS.md) for runtime code.

## Required read order

1. `../../AGENTS.md`
2. `../../README.md`
3. this file
4. the closest child `AGENTS.md`
5. `../../docs/implementation-status.md`
6. `../../docs/state-machine.md`
7. `../../docs/rsi-convergence.md`
8. the relevant component contract
9. `../../tests/AGENTS.md`

## Current supported composition

On Draft PR #7 / `feat/rsi-convergence`, `__main__.py` exposes:

```text
demo
rsi
verify
audit
approvals
review
```

`demo` uses the compatibility engine. `rsi` uses the converged controller. The branch does not expose a supported `coevolve` command.

Do not describe branch-only behavior as `main` behavior until PR #7 is merged.

## Module ownership

| Path | Owns | Forbidden responsibility |
|---|---|---|
| `__main__.py` | parsing, dispatch, JSON presentation | hidden policy, provider logic, credentials |
| `config.py` | strict immutable configuration | State transition decisions |
| `control_plane/` | exact provider-neutral types and canonical records | adjacency, persistence, SDK calls |
| `orchestration/rsi_policy.py` | Candidate promote/reject/rollback/stop policy | filesystem/provider/approval identity |
| `orchestration/converged.py` | supported stage sequencing, composition, durable resume | weakening component guards |
| `orchestration/run_state.py` | Run metadata/config identity/deterministic resume clock | model quality policy |
| `adapter_runtime/` | strict provider execution and evidence translation | Peak or human authority |
| `approval/` | immutable Dataset/Checkpoint/Harness approval authority | authentication implementation or score thresholds |
| `verification/` | data admission | model promotion |
| `training/` | Candidate creation | benchmark or Peak policy |
| `serving/` | deployment/readiness/teardown | promotion policy |
| `evaluation/` | benchmark and failure facts | direct Peak update |
| `lineage/` | immutable persistence, bundle, Peak CAS, marker, audit | deciding quality |
| `cost.py` | cost/circuit facts | deciding quality |
| `engine.py` | one-pass compatibility demo | recursive policy truth |
| `harness/` | future Harness/trace components | unsupported CLI or direct model-weight mutation |

A composition change may connect modules. It may not transfer their authority.

## Runtime invariants

Preserve exactly:

```text
active_checkpoint_id == peak_checkpoint_id
candidate.parent_checkpoint_id == active_checkpoint_id
candidate_score > peak_score + min_improvement
```

Also preserve:

- threshold equality rejects;
- rejected/rolled-back Candidate never becomes parent;
- valid final-iteration promotion precedes max-iteration stop;
- exact budget limits are allowed;
- Peak update requires committed matching `PROMOTE` Decision;
- Peak update is compare-and-swap and score/iteration monotonic;
- controller recomputes artifact SHA-256;
- path escape and symlink fail closed by default;
- approval binds Request hash, Run, iteration, Subject type/ID/hash, role, and deadline;
- missing/pending/denied/expired/invalid approval grants no authority;
- serving teardown runs in `finally`;
- cross-Run/future evidence fails closed;
- transaction manifest is the commit point;
- immutable-ID content conflict fails closed;
- resume configuration mismatch fails closed;
- secrets and raw private review content stay out of generic metadata/logs.

## Composition rules

### Before invoking a provider

- validate config;
- create deterministic request and idempotency identities;
- use bounded timeout/retry;
- do not use a shell command string;
- pass only the required environment/credentials;
- identify expected outputs, cost, teardown, and evidence.

### Before training

- accepted Dataset exists;
- exact accepted-Dataset bytes match the recorded SHA-256;
- Dataset admission evidence is committed;
- required Dataset approval is granted;
- parent Checkpoint equals active Peak;
- budget/circuit guards permit the operation.

### Before evaluation

- Candidate artifact passed controller integrity;
- endpoint is ready;
- evaluator receives the exact deployed endpoint;
- benchmark identity and score range are configured;
- teardown is guaranteed.

### Before policy evaluation

- evaluation score is finite;
- CandidateObservation has non-empty committed evidence IDs;
- current Snapshot is `EVALUATE`;
- Run, iteration, Candidate, parent, active, and Peak identities match.

### Before Peak mutation

- policy emitted `PROMOTE` for the same Checkpoint;
- required Checkpoint approval is granted;
- Decision and dependencies are committed;
- Checkpoint bundle verifies;
- caller supplies expected previous Peak;
- pointer model/score/Run/iteration/bundle hash all match.

## Failure rules

- Do not convert provider, integrity, approval, persistence, or teardown failure into ordinary Candidate rejection.
- Preserve the original failure; attach secondary teardown failure without hiding the primary one.
- Do not create a Checkpoint when training/artifact integrity failed.
- Do not call policy with a fabricated score.
- Do not advance the Peak or parent after rejection, denial, or rollback.
- Persist only allowed failure evidence; never persist secrets.
- Leave uncommitted orphan files outside the evidence graph.

## Structural code changes

Changing a State, Event, StopReason, schema field, transition guard, resume rule, artifact/approval/Peak contract, CLI command, or module owner requires:

```text
implementation
deterministic tests
closest scoped AGENTS.md
README.md
docs/implementation-status.md
docs/state-machine.md
docs/rsi-convergence.md
relevant component document
docs/traceability-index.md
docs/stacked-pr-plan.md when delivery ownership changes
```

## Validation

```bash
python -m compileall -q src tests
ruff check src tests
mypy src
python -m pytest -q \
  --cov=post_training_rsi \
  --cov-report=term-missing \
  --cov-fail-under=75
python -m post_training_rsi --workspace /tmp/rsi-demo demo
python -m post_training_rsi \
  --workspace /tmp/rsi-run \
  --run-id smoke-run \
  rsi
```

Use `audit` against any produced Checkpoint. Test approval pause/list/review/resume when approval behavior changes.

## Human-owned runtime operations

Do not autonomously:

```text
provision/rotate production secrets
change GPU/cloud quota or billing
mutate production endpoints
assign reviewer roles or approve requests
recover stale locks
allow unrestricted environment inheritance
allow arbitrary external artifact paths
change production retention/backup policy
merge/rebase/force-push shared branches
enable Git Town
claim production readiness
```
