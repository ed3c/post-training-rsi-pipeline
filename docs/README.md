# Documentation index

This directory separates **current executable truth** from the **target PDF architecture** so an Agent can traverse the repository without treating design prose as implementation evidence.

## Read path

| Order | Document | Purpose |
|---:|---|---|
| 1 | [`../AGENTS.md`](../AGENTS.md) | Agent rules, source precedence, path ownership, and validation gates |
| 2 | [`../README.md`](../README.md) | Repository overview, directory-to-state mapping, and end-to-end data flow |
| 3 | [`implementation-status.md`](implementation-status.md) | Exact integration snapshot, reachable commands, and known gaps |
| 4 | [`state-machine.md`](state-machine.md) | Current and target RSI/Co-Evolution transition contracts |
| 5 | [`traceability-index.md`](traceability-index.md) | Requirement → code → config → tests → artifacts → planned PR index |
| 6 | [`stacked-pr-plan.md`](stacked-pr-plan.md) | Molecular PR decomposition and Git Town fail-closed admission |
| 7 | [`architecture.md`](architecture.md) | Target architecture derived from the source PDF |
| 8 | [`productionization.md`](productionization.md) | Remaining controls before real inference/GPU/cloud operation |

## Sources of truth

| Question | Source |
|---|---|
| What can run now? | `src/`, `tests/`, `.github/workflows/ci.yml`, then `implementation-status.md` |
| What state owns this file? | `README.md` directory/state table and `state-machine.md` |
| Which requirement is satisfied? | `traceability-index.md` |
| What remains before production? | `productionization.md` |
| How should work be split into PRs? | `stacked-pr-plan.md` |
| What architecture are we trying to reach? | `architecture.md` and the user-provided PDF |

## Status vocabulary

- **Implemented:** reachable and tested.
- **Contract only:** interface exists, but the supported runtime does not select it.
- **Partial:** reachable behavior exists, but required transitions or evidence are missing.
- **Planned:** target only.
- **Verified:** test/CI evidence belongs to the exact commit named in the status document.

## Update rule

A change that adds, removes, or reroutes a state must update the following in the same PR:

1. tests for the transition;
2. `README.md` state/data-flow sections;
3. `state-machine.md`;
4. `implementation-status.md`;
5. `traceability-index.md`.
