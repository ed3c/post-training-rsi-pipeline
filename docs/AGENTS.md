# AGENTS.md — `docs/`

This file narrows the root [`AGENTS.md`](../AGENTS.md) for architecture and traceability documentation.

## Truth rules

- Separate Current, Contract-only, Partial, and Target behavior.
- Do not convert a design diagram into an implementation claim.
- Use exact repository paths, supported commands, and evidence filenames.
- When code and prose disagree, update prose to match code or land code/tests in the same PR.
- Record the exact baseline commit for status snapshots.

## Structural update set

A change to a state, edge, directory owner, CLI command, artifact, or PR dependency must update all relevant documents:

- `../README.md`
- `implementation-status.md`
- `state-machine.md`
- `traceability-index.md`
- `stacked-pr-plan.md` when branch/merge ownership changes

`architecture.md` remains the target design; `implementation-status.md` remains the current truth.

## Link discipline

All index links must be repository-relative. Do not link private benchmarks, credentials, local absolute paths, or temporary artifact URLs.
