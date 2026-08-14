# AGENTS.md — `src/post_training_rsi/adapter_runtime/`

This scope narrows the repository and `src/post_training_rsi/` Agent contracts for provider execution, integrity checks, lifecycle composition, and evidence translation.

## Read order

1. `../../../AGENTS.md`
2. `../AGENTS.md`
3. `../../../docs/control-plane-contracts.md`
4. `../../../docs/adapter-runtime.md`
5. the adapter implementation and focused tests being changed
6. the current PR task packet and sibling ownership map

## Ownership

This directory owns:

- bounded, no-shell external command execution;
- exact `post-training-rsi.adapter/v1` result envelopes;
- deterministic idempotency keys;
- dataset and checkpoint artifact integrity checks;
- strict adapter construction from validated configuration;
- candidate serving/evaluation/teardown composition;
- translation from adapter results to `post-training-rsi.control/v1` evidence.

This directory must not own:

- RSI score thresholds, promotion, rollback, or plateau policy;
- checkpoint/Peak persistence transactions;
- reviewer authority or approval storage;
- Benchmark business logic;
- provider credentials inside serialized evidence;
- implicit Git, cloud, GPU, or production endpoint mutation.

## Hard invariants

1. Never invoke a configurable command through a shell.
2. Never accept stale result files, unknown fields, mismatched schema/type, or mismatched idempotency keys.
3. Retry only bounded operations with the same semantic idempotency key.
4. Dataset hash must match the exact bytes passed to training.
5. Controller-computed artifact SHA-256 outranks a worker-reported hash.
6. External checkpoint paths remain inside the configured output root unless a reviewed opt-in explicitly allows otherwise.
7. Symlinks are rejected at Dataset and Checkpoint trust boundaries.
8. Evaluation receives the exact deployed endpoint.
9. Teardown runs even when evaluation fails; dual failures preserve both error facts.
10. API keys, Authorization headers, benchmark bodies, and model weights never enter control-plane metadata.

## Change evidence

For every adapter contract change, add deterministic coverage for the applicable matrix:

```text
success | timeout | non-zero exit | malformed JSON | unknown field
stale result | idempotency mismatch | provider echo mismatch
path escape | symlink | artifact hash mismatch | non-finite number
endpoint handoff | teardown success | teardown failure | evaluation+teardown failure
```

Tests must remain no-network, no-GPU, and no-production-endpoint. Use injected transports and short-lived local command fixtures.

## Sibling boundaries

- PR #3 owns RSI decision policy.
- PR #4 owns transactional lineage and Peak persistence.
- PR #5 owns this adapter runtime.
- PR #6 owns immutable HITL approvals.
- PR #7 is the only owner allowed to compose these siblings into the supported RSI CLI/runtime and rewrite shared root documentation after convergence.
