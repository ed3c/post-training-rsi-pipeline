# PR #8 exact-head validation trigger

This repository-only evidence commit is made through the connected GitHub App after the fixed-model Harness outer-loop implementation passed its pre-push helper gate.

It exists to trigger pull-request CI for the final Draft PR branch state.

- Branch: `feat/harness-outer-loop`
- Parent: PR #7 / `feat/rsi-convergence`
- Component scope: fixed-model Harness mutation, validation, evaluation, strict acceptance, immutable evidence, plateau/cycle/budget termination
- Pre-push gates: compile, Ruff, full pytest coverage floor, compatibility demo, recursive RSI parent smoke
- Required remote gate: pull-request CI on the exact resulting Head

This commit changes no runtime behavior. The Draft PR remains unmerged. Trajectory harvesting, trace Dataset accumulation, model training, hot-swap, production endpoints, and Git Town remain outside this slice.
