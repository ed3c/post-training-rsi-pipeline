# PR #7 exact-head validation trigger

This file records the external trigger used to start pull-request CI after the validated convergence patch was applied by a GitHub Actions helper.

- Pull request: `#7`
- Target branch: `feat/rsi-convergence`
- Validated patch SHA-256: `d39098b8f17abbd9f15c712cbf611e2b3177c0195f1c4083c5e4f313cf68b09a`
- Compressed patch SHA-256: `bc2c0944d3cde838d465b3d82ecffbee82dcde514fe8ccac2597e7ba490cc314`
- Local reference commit: `605b91707fd0d3d9b94ae96769585ff4f9dfd7ec`
- Local deterministic gate: 98 tests passed, coverage 87.72%, compatibility demo passed, recursive RSI smoke passed, wheel install passed.

The helper workflow verifies both patch checksums, applies the patch without force-pushing, runs compile, Ruff, the full coverage gate, the compatibility demo, and the recursive RSI smoke before it may fast-forward the target branch.

This commit is made through the connected GitHub App rather than the workflow `GITHUB_TOKEN`, so GitHub emits a fresh pull-request synchronization event and evaluates CI for the resulting exact head.

The pull request remains Draft and unmerged. Model/Harness Co-Evolution is not part of PR #7.
