# PR-14 exact-head validation request

This connector-authored commit follows the deterministic reconciliation workflow and triggers the normal read-only Pull Request check set on the current source, tests, documentation, machine manifest, and validation evidence.

Required exact-head gates:

```text
Python 3.11 / 3.12
compileall
Ruff
mypy
focused recovery activation matrix
full pytest coverage floor
package command surface: build / verify / preflight only
```

The command surface must contain no `activate`, `apply`, `switch`, `resume`, or `rollback` operation. Every successful planning or preflight result must report `executed=false`.

A green check set proves only the local reference contracts. It does not establish live pointer mutation, automatic recovery, authenticated reviewer identity, quorum/MFA, remote backup, encryption/retention, RPO, RTO, or production readiness.
