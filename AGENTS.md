# AGENTS.md — repository operating contract

This file is the first instruction source for every coding or documentation agent working in this repository. It describes the **current branch truth**, the target PDF architecture, safe change boundaries, and the evidence required before claiming integration.

## 1. Mandatory read order

Read these sources in order before editing:

1. `AGENTS.md` — repository-wide invariants and agent protocol.
2. `README.md` — current/target state machines, directory ownership, and data flow.
3. `docs/README.md` — documentation index and source-of-truth map.
4. `docs/implementation-status.md` — exact implementation snapshot and known gaps.
5. `docs/state-machine.md` — transition contracts and evidence emitted by each state.
6. `docs/control-plane-contracts.md` — versioned state/event/decision/evidence schema.
7. `docs/traceability-index.md` — requirement → code → test → evidence → planned PR mapping.
8. `docs/stacked-pr-plan.md` — review/merge decomposition and Git Town admission status.
9. The nearest path-scoped `AGENTS.md`, if one is added later.
10. The issue/task packet and the current branch/PR graph.

Do not infer completion from architecture diagrams. Executable code and tests outrank prose.

## 2. Source precedence

When sources disagree, use this order:

1. checked-in executable code and deterministic tests;
2. current branch configuration and CI workflow;
3. `docs/implementation-status.md`;
4. `docs/state-machine.md`, `docs/control-plane-contracts.md`, and `docs/traceability-index.md`;
5. target architecture in `docs/architecture.md`;
6. the source PDF and external design notes.

The source PDF defines the intended architecture. It does not prove that a feature is implemented.

## 3. Current repository truth

At the documentation baseline, `feat/pdf-architecture` is package version `0.2.0`. Its latest verified CI run is green, but the runnable CLI exposes only `demo`, and `RSIEngine.run()` executes one deterministic iteration. Peak comparison, multi-iteration plateau handling, full lineage persistence, `audit`, `coevolve`, Harness mutation, trajectory harvesting, HITL approval, and production adapter selection are not yet wired end to end.

`feat/state-domain-contracts` adds the versioned `post-training-rsi.control/v1` control-plane schema. The schema and its tests are **Contract only** until a supported controller emits and persists these records.

Any change that alters this statement must update all of:

- `README.md` current-state table;
- `docs/implementation-status.md`;
- `docs/control-plane-contracts.md` when the schema changes;
- `docs/traceability-index.md`;
- the relevant transition tests.

## 4. Non-negotiable invariants

1. Never promote a checkpoint merely because it is the latest.
2. Never train on a record without one explicit verification decision.
3. Never treat architecture-only or placeholder modules as implemented runtime behavior.
4. Never execute generated code in the core process; only static checks belong there.
5. Never exceed budget, retry, recursion, or wall-time limits silently.
6. Never make a rejected checkpoint the parent of a later candidate.
7. Never delete lineage required to reproduce a decision.
8. Never expose credentials, private benchmark content, model weights, or provider tokens.
9. Never mutate production endpoints, Git history, or cloud infrastructure implicitly.
10. Human approval gates must fail closed: missing, malformed, mismatched, pending, or denied decisions are not approvals.
11. Never redefine shared state, event, stop-reason, decision, or evidence semantics outside `control_plane/`.
12. Never accept unknown fields or silently reinterpret an incompatible control schema version.

## 5. Directory ownership and state-machine boundaries

| Path | Owns | Must not own |
|---|---|---|
| `src/post_training_rsi/config.py` | validated configuration and thresholds | runtime transition policy |
| `src/post_training_rsi/control_plane/` | versioned provider-neutral state/event/stop/decision/evidence records | adjacency, score thresholds, provider SDK calls, persistence side effects |
| `src/post_training_rsi/models.py` | current portable data/result payloads | provider SDK calls or duplicate control taxonomies |
| `src/post_training_rsi/engine.py` | current orchestration entry point | hidden provider-specific behavior |
| `src/post_training_rsi/generation.py` | deterministic synthesis fixture | production Teacher transport |
| `src/post_training_rsi/synthesis/` | Teacher protocols, prompts, provider-facing synthesis | promotion decisions |
| `src/post_training_rsi/verification/` | deterministic admission/quarantine decisions | model-quality promotion |
| `src/post_training_rsi/training/` | trainer protocol and checkpoint production | benchmark decisions |
| `src/post_training_rsi/serving/` | deployment readiness contract | model promotion |
| `src/post_training_rsi/evaluation/` | benchmark evidence and failure traces | lineage mutation outside its result |
| `src/post_training_rsi/lineage/` | immutable evidence, manifests, artifact lookup | deciding whether a score is good enough |
| `src/post_training_rsi/harness/` | future Harness mutation and trace harvesting | model weight updates |
| `tests/` | deterministic state/contract evidence | network-, API-key-, or GPU-dependent tests |
| `docs/` | current status, architecture, operations, traceability | claims unsupported by code/tests |

Provider adapters must implement stable protocols. Controllers depend on protocols and `control_plane/` records, not provider SDKs.

## 6. Required task packet

Before implementation, record these fields in the issue or PR body:

```yaml
allowed_paths: []
excluded_paths: []
dependencies: []
parallel_safe_siblings: []
required_evals: []
evidence_boundary: ""
rollback_subject: ""
human_owned_operations: []
```

Path-disjoint work should be sibling PRs. Serial parent/child branches are only for real interface or data dependency.

## 7. Validation contract

Run the smallest relevant checks first, then the full local gate:

```bash
python -m pytest tests/test_control_plane.py
make install
make lint
make typecheck
make test
make demo
```

`make coevolve` is currently a **known red target** because the checked-in CLI does not expose `coevolve`. Do not claim it passes until the command and deterministic E2E test exist.

For every changed transition or control record:

- use the shared `control_plane/` enums and record types;
- add a deterministic regression test;
- assert the transition reason and emitted evidence;
- preserve canonical serialization and strict schema rejection;
- update `docs/state-machine.md`, `docs/control-plane-contracts.md`, and `docs/traceability-index.md`;
- keep tests runnable without network, API keys, GPUs, or mutable external services.

## 8. Git and PR protocol

- One issue, branch, and PR per independently reviewable outcome.
- Every PR records parent, children, merge order, collision paths, and rebase owner.
- Child PRs target their actual parent until the parent merges.
- Do not force-push shared branches or auto-resolve semantic conflicts.
- Do not infer stack hierarchy from branch names.
- A convergence PR has one explicit owner and contains integration-only changes.

### Git Town admission

Git Town is **not configured** at this baseline. Do not run `git town` commands until all admission gates in `docs/stacked-pr-plan.md` pass: exact version pin, repository config, verified parent graph, isolated worktrees, non-interactive mode, dry-run/no-push evidence, and an active stack manifest.

## 9. Completion language

Use these labels consistently:

- **Implemented** — reachable from a supported CLI/runtime path and covered by deterministic tests.
- **Contract only** — protocol, record schema, or adapter exists but is not selected/emitted by the supported runtime.
- **Partial** — some states exist but required transitions/evidence are missing.
- **Planned** — documented target with no reachable implementation.
- **Verified** — CI or local command evidence is recorded for the exact commit.

Never collapse these labels into “done.”
