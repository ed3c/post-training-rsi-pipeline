# PR #11 exact-head validation trigger

This evidence-only commit is made through the connected GitHub App after the Model/Harness Co-Evolution convergence implementation passed its pre-push helper gate.

- Branch: `feat/coevolution-convergence`
- Parent: PR #10 / `feat/model-inner-loop`
- Scope: resumable outer-middle-inner composition, exact stage journal, verified local model swap, rollback preservation, promotion-only Harness slimming/reset, cycle termination, and supported `coevolve` CLI
- Privacy boundary: only observable task/tool trajectories are eligible; hidden chain-of-thought, scratchpads, private notes, credentials, and secret-bearing material remain rejected
- Production boundary: the deterministic swap adapter updates local accepted state only; it does not mutate a production endpoint
- Pre-push gates: compile, Ruff, full pytest coverage floor, compatibility demo, recursive RSI smoke, and supported `coevolve` smoke
- Required remote gate: pull-request CI on the exact resulting Head

This commit changes no runtime behavior. The Draft PR remains unmerged and is not marked Ready. Production credentials, managed GPU or endpoint mutation, release deployment, and Git Town remain outside this slice.
