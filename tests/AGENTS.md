# AGENTS.md — `tests/`

This file narrows the root [`AGENTS.md`](../AGENTS.md) for deterministic test evidence.

## Test contract

- Tests must run without network, API keys, GPUs, Docker daemons, or mutable cloud services.
- Use deterministic fixtures for Teacher, trainer, evaluator, serving, approval, and clock/ID behavior.
- Every state transition needs assertions for the state, reason, and emitted artifact/evidence.
- Every promotion path needs a rejection or rollback counterpart.
- Every external command adapter needs stale-result, malformed-result, mismatch, timeout, and non-zero-exit coverage as applicable.
- Never mark a test `xfail` merely because a required transition is not implemented; leave the feature status as Planned/Partial instead.

## Required matrices

For RSI policy, cover:

```text
promote | reject | rollback | plateau | max-iterations
per-trial-budget | total-budget | provider-circuit
empty/low-acceptance data | malformed evidence | parent invariant
```

For Co-Evolution, cover:

```text
Harness accept | Harness reject | Harness plateau
trace target reached | trace target not reached
model promote | model rollback | hot-swap teardown | cycle stop
```

Tests are the highest-precedence evidence for implementation claims.
