# Documentation index

This directory separates **current executable truth** from the **target PDF architecture** so an Agent can traverse the repository without treating design prose, a typed schema, or an unwired policy component as end-to-end implementation evidence.

## Read path

| Order | Document | Purpose |
|---:|---|---|
| 1 | [`../AGENTS.md`](../AGENTS.md) | Agent rules, source precedence, path ownership, and validation gates |
| 2 | [`../README.md`](../README.md) | Repository overview, directory-to-state mapping, and end-to-end data flow |
| 3 | [`implementation-status.md`](implementation-status.md) | Exact integration snapshot, reachable commands, and known gaps |
| 4 | [`state-machine.md`](state-machine.md) | Current and target RSI/Co-Evolution transition contracts |
| 5 | [`control-plane-contracts.md`](control-plane-contracts.md) | Versioned states, events, stop reasons, decisions, evidence, snapshots, and transitions |
| 6 | [`rsi-loop-policy.md`](rsi-loop-policy.md) | Pure Peak/reject/rollback/plateau/max-iteration/budget decision boundary |
| 7 | [`traceability-index.md`](traceability-index.md) | Requirement → code → config → tests → artifacts → planned PR index |
| 8 | [`stacked-pr-plan.md`](stacked-pr-plan.md) | Molecular PR decomposition and Git Town fail-closed admission |
| 9 | [`architecture.md`](architecture.md) | Target architecture derived from the source PDF |
| 10 | [`productionization.md`](productionization.md) | Remaining controls before real inference/GPU/cloud operation |

## Sources of truth

| Question | Source |
|---|---|
| What can run now? | `src/`, `tests/`, `.github/workflows/ci.yml`, then `implementation-status.md` |
| What state owns this file? | `README.md` directory/state table and `state-machine.md` |
| Which typed records may modules exchange? | `control-plane-contracts.md` and `src/post_training_rsi/control_plane/` |
| How does the candidate decision boundary behave? | `rsi-loop-policy.md`, `orchestration/rsi_policy.py`, and `test_rsi_policy.py` |
| Which requirement is satisfied? | `traceability-index.md` |
| What remains before production? | `productionization.md` |
| How should work be split into PRs? | `stacked-pr-plan.md` |
| What architecture are we trying to reach? | `architecture.md` and the user-provided PDF |

## Status vocabulary

- **Implemented:** reachable and tested.
- **Implemented component:** behavior is coded and tested, but not composed into the supported runtime.
- **Contract only:** interface or versioned record schema exists, but the supported runtime does not select or emit it.
- **Partial:** reachable behavior exists, but required transitions or evidence are missing.
- **Planned:** target only.
- **Verified:** test/CI evidence belongs to the exact commit named in the status document.

## Update rule

A change that adds, removes, or reroutes a state, event, stop reason, serialized record, or policy edge must update the following in the same PR:

1. tests for the contract or transition;
2. `README.md` state/data-flow sections;
3. `state-machine.md`;
4. `control-plane-contracts.md` when the schema changes;
5. `rsi-loop-policy.md` when candidate policy changes;
6. `implementation-status.md`;
7. `traceability-index.md`.

An incompatible record change must use a new schema version. Do not silently reinterpret `post-training-rsi.control/v1`.
