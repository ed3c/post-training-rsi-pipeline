# AGENTS.md — `docs/`

This file narrows the root [`AGENTS.md`](../AGENTS.md) for architecture and traceability documentation.

## Truth rules

- Separate Current, Implemented-component, Contract-only, Partial, and Target behavior.
- Do not convert a design diagram, enum value, record schema, or isolated policy test into a supported-runtime claim.
- Use exact repository paths, supported commands, schema versions, PR parents, and evidence filenames.
- When code and prose disagree, update prose to match code or land code/tests in the same PR.
- Record the exact baseline/head commit for status snapshots and verification claims.
- A typed record schema is Contract only until a supported runtime emits and persists it.
- A tested policy is an Implemented component until a supported runtime composes it and E2E tests assert durable evidence.

## Structural update set

A change to a State, Event, StopReason, record field, policy precedence, edge, directory owner, CLI command, artifact, or PR dependency must update all relevant documents:

- `../README.md`
- `implementation-status.md`
- `state-machine.md`
- `control-plane-contracts.md` when schema representation changes
- `rsi-loop-policy.md` when Peak/parent/rollback/stop semantics change
- `traceability-index.md`
- `stacked-pr-plan.md` when branch/merge ownership changes

`architecture.md` remains the target design. `implementation-status.md` remains current integration truth. `control-plane-contracts.md` remains the exact serialized schema. `rsi-loop-policy.md` remains the exact implemented Candidate decision boundary.

## Link discipline

All index links must be repository-relative. Do not link private benchmarks, credentials, local absolute paths, or temporary artifact URLs.
