# Co-Evolution validation evidence index

This index connects each molecular PR component to its exact-branch validation record and the final convergence gate.

## Evidence chain

| Layer | Branch / PR | State responsibility | Exact validation record |
|---|---|---|---|
| Harness outer loop | `feat/harness-outer-loop` / PR #8 | `FREEZE_MODEL → MUTATE/VALIDATE/EVALUATE_HARNESS → ACCEPT/REJECT/HARVEST_TRACES` | [`harness-outer-loop-latest.md`](harness-outer-loop-latest.md), [`harness-outer-loop-latest.json`](harness-outer-loop-latest.json) |
| Observable Trace middle loop | `feat/trace-harvesting` / PR #9 | `HARVEST_TRACES → VERIFY_TRACES → TRAIN_MODEL/quarantine/retry` | [`trace-harvesting-latest.md`](trace-harvesting-latest.md), [`trace-harvesting-latest.json`](trace-harvesting-latest.json) |
| Model inner loop | `feat/model-inner-loop` / PR #10 | `TRAIN_MODEL → EVALUATE_MODEL → review/promote/rollback → slim/freeze handoff` | [`model-inner-loop-latest.md`](model-inner-loop-latest.md), [`model-inner-loop-latest.json`](model-inner-loop-latest.json) |
| Durable convergence core | `feat/coevolution-convergence` / PR #11 | transactional composition, Run/Harness persistence, Peak/quarantine, resume | [`coevolution-convergence-latest.md`](coevolution-convergence-latest.md), [`coevolution-convergence-latest.json`](coevolution-convergence-latest.json) |
| Final CLI/docs/approval release gate | `feat/coevolution-convergence` / PR #11 | `coevolve`, HITL pause/resume/deny, docs contracts, full matrix | [`coevolution-release-latest.md`](coevolution-release-latest.md), [`coevolution-release-latest.json`](coevolution-release-latest.json) |

## Required interpretation

A validation record proves only the exact tested commit/tree and the gates listed inside that record. The record commit itself may be a later documentation-only commit that stores evidence for the tested tree.

Evidence must not be generalized into claims about:

```text
real Teacher API execution
real SFT/DPO gradients
managed GPU training
live inference serving
production benchmark validity
production Trace privacy or representativeness
enterprise identity/RBAC/MFA/quorum
distributed storage and locking
Git Town configuration
production readiness
```

## PR ancestry

```text
PR #7  feat/rsi-convergence
└── PR #8  feat/harness-outer-loop
    └── PR #9  feat/trace-harvesting
        └── PR #10 feat/model-inner-loop
            └── PR #11 feat/coevolution-convergence
```

This is ordinary GitHub ancestry, not an active Git Town stack.

## Review order

```text
component AGENTS.md
→ component architecture document
→ focused tests
→ exact component validation record
→ docs/coevolution-convergence.md
→ tests/test_coevolution*.py
→ final release validation record
→ PR #11 review
```
