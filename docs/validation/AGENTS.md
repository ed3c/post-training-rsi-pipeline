# AGENTS.md — `docs/validation/`

Read the repository and `docs/AGENTS.md` contracts first.

This directory owns exact-head validation records. It does not own runtime policy or production-certification claims.

Rules:

- Never hand-edit a generated PASS record to change its commit, tree, run, timestamp, or gates.
- A later code/config/test/build change requires a new exact-head record.
- Preserve explicit non-claims.
- Do not store secrets, raw private review content, model weights, or proprietary benchmark bodies.
- A failed or skipped gate cannot be represented as PASS.
- Documentation-only commits may retain the previous code validation record, but must still pass documentation/link/CLI-contract checks.
