# PR #10 exact-head validation trigger

This evidence-only commit is made through the connected GitHub App after the verified-trace Model inner-loop implementation passed its pre-push helper gate.

- Branch: `feat/model-inner-loop`
- Parent: PR #9 / `feat/trajectory-harvesting`
- Scope: verified Trace Dataset provenance and SHA-256 admission, injected Candidate training/evaluation, Candidate artifact integrity, strict promote/rollback recommendation, regression and budget handling, immutable evidence and terminal records
- Safety boundary: a `PROMOTE_MODEL` recommendation does not mutate the global Peak pointer or hot-swap production state
- Pre-push gates: compile, Ruff, full pytest coverage floor, compatibility demo, recursive RSI parent smoke
- Required remote gate: pull-request CI on the exact resulting Head

This commit changes no runtime behavior. The Draft PR remains unmerged. Production hot-swap, Peak compare-and-swap, Harness slimming/reset, a new outer-loop cycle, credentials, infrastructure mutation, and Git Town remain outside this slice.
