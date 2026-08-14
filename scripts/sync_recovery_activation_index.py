from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BLOCKS = {
    "README.md": """
<!-- PR14_RECOVERY_ACTIVATION_INDEX_START -->
## Recovery activation planning successor — Draft PR #14

The recovery stack now separates diagnosis, byte preservation, and activation planning:

```text
PR #12  read-only audit and recovery diagnosis
└── PR #13  content-addressed bundle + inactive staged restore
    └── PR #14  content-bound activation plan + explicit preflight
```

PR #14 terminates at:

```text
READY_FOR_HUMAN_EXECUTION
executed=false
```

It has no `activate`, `apply`, `switch`, `resume`, or `rollback` command. See [`docs/recovery-activation-plan.md`](docs/recovery-activation-plan.md) and [`docs/recovery-activation-manifest.json`](docs/recovery-activation-manifest.json).
<!-- PR14_RECOVERY_ACTIVATION_INDEX_END -->
""",
    "AGENTS.md": """
<!-- PR14_RECOVERY_ACTIVATION_INDEX_START -->
## Recovery activation planning boundary

For changes under `src/post_training_rsi/recovery_activation/`, read:

```text
src/post_training_rsi/recovery_activation/AGENTS.md
  → docs/forensic-recovery-bundle.md
  → docs/recovery-activation-plan.md
  → docs/recovery-activation-manifest.json
```

A successful preflight report is not activation authority. Live compare-and-swap execution, activation receipt publication, rollback, and writer resume remain separate human-owned operations.
<!-- PR14_RECOVERY_ACTIVATION_INDEX_END -->
""",
    "docs/README.md": """
<!-- PR14_RECOVERY_ACTIVATION_INDEX_START -->
## Recovery activation planning

| Document | Purpose |
|---|---|
| [`recovery-activation-plan.md`](recovery-activation-plan.md) | expected-live/target/rollback, authority, TTL, and exact preflight contracts |
| [`recovery-activation-manifest.json`](recovery-activation-manifest.json) | machine-readable PR-14 identity, State Machine, commands, paths, and non-claims |

PR #14 reports `READY_FOR_HUMAN_EXECUTION` with `executed=false`; it implements no live pointer mutation.
<!-- PR14_RECOVERY_ACTIVATION_INDEX_END -->
""",
    "docs/implementation-status.md": """
<!-- PR14_RECOVERY_ACTIVATION_INDEX_START -->
## PR-14 recovery activation planning

Status: **Draft implemented component; no activation side effect**

```text
Branch: feat/recovery-activation-plan
Parent: feat/forensic-recovery-bundle / PR #13
CLI: python -m post_training_rsi.recovery_activation
Commands: build, verify, preflight
Terminal status: READY_FOR_HUMAN_EXECUTION
Executed: false
```

Implemented:

```text
content-addressed authority receipt and plan
expected-live / target / rollback binding
requester-reviewer separation
finite TTL and reviewer-role policy
staged bundle and strict-audit hash binding
stale live pointer rejection
exact observation preflight
```

Not implemented:

```text
activate/apply/switch/resume/rollback command
live pointer mutation
identity-provider authentication
reviewer quorum/MFA
automatic recovery
production readiness
```
<!-- PR14_RECOVERY_ACTIVATION_INDEX_END -->
""",
    "docs/stacked-pr-plan.md": """
<!-- PR14_RECOVERY_ACTIVATION_INDEX_START -->
## PR-14 recovery activation planning successor

```text
PR #11  Co-Evolution convergence
└── PR #12  read-only audit/recovery boundary
    └── PR #13  forensic bundle and inactive staging
        └── PR #14  activation plan and preflight only
```

PR #14 owns `src/post_training_rsi/recovery_activation/**`, focused tests/workflow, and its component documentation. A future execution PR must be separate and require fresh expected-generation compare-and-swap, exact plan/authority/evidence hashes, one atomic pointer update, immutable activation receipt, rollback pointer, post-switch strict audit, and explicit writer-resume authority.

Git Town remains disabled until its exact version, repository configuration, complete parent graph, worktree leases, non-interactive rehearsal, no-push evidence, and active `stack.tsv` are committed.
<!-- PR14_RECOVERY_ACTIVATION_INDEX_END -->
""",
    "docs/traceability-index.md": """
<!-- PR14_RECOVERY_ACTIVATION_INDEX_START -->
## Recovery activation planning traceability

| Requirement ID | Requirement | Code | Test / evidence | Status |
|---|---|---|---|---|
| `REC-PLAN-01` | deterministic authority receipt and activation plan identity | `recovery_activation/contracts.py` | round-trip and identity tests | Implemented component |
| `REC-PLAN-02` | expected-live, target, and rollback binding | `RecoveryActivationPlan` | rollback/target tests | Implemented component |
| `REC-PLAN-03` | separation of duties and reviewer-role policy | receipt + `RecoveryActivationPolicy` | self-approval/role tests | Implemented component |
| `REC-PLAN-04` | finite plan and authority validity | contracts + `verify_plan` | TTL/expiry tests | Implemented component |
| `REC-PLAN-05` | bundle, staged audit, decision, and target evidence binding | `run_preflight` | substitution matrix | Implemented component |
| `REC-PLAN-06` | stale live generation fails compare-and-swap preflight | `run_preflight` | stale pointer test | Implemented component |
| `REC-PLAN-07` | no activation side effect | command surface + terminal report | CLI/manifest tests | Verified by contract tests |
| `REC-PLAN-08` | machine-readable ownership and non-claims | `recovery-activation-manifest.json` | manifest tests | Implemented component |
<!-- PR14_RECOVERY_ACTIVATION_INDEX_END -->
""",
    "docs/forensic-recovery-bundle.md": """
<!-- PR14_RECOVERY_ACTIVATION_INDEX_START -->
## Successor: content-bound activation planning

PR #14 consumes the verified inactive stage produced by PR #13 and adds:

```text
expected live pointer
  + audited staged target
  + rollback pointer
  + immutable authority receipt
  + finite validity
  + explicit observations
  → READY_FOR_HUMAN_EXECUTION
  → executed=false
```

It does not activate the staged copy. See [`recovery-activation-plan.md`](recovery-activation-plan.md).
<!-- PR14_RECOVERY_ACTIVATION_INDEX_END -->
""",
    "docs/coevolution-audit-recovery.md": """
<!-- PR14_RECOVERY_ACTIVATION_INDEX_START -->
## Activation planning after a verified staged copy

After PR #13 reconstructs and verifies an inactive generation, PR #14 can bind that exact bundle, strict staged audit, expected live generation, rollback pointer, and existing approval decision into a finite activation plan. A successful preflight is terminal evidence for a later human operation and always reports `executed=false`.

No pointer change or writer resume is implemented by PR #14. See [`recovery-activation-plan.md`](recovery-activation-plan.md).
<!-- PR14_RECOVERY_ACTIVATION_INDEX_END -->
""",
    "src/post_training_rsi/AGENTS.md": """
<!-- PR14_RECOVERY_ACTIVATION_INDEX_START -->
## `recovery_activation/` ownership

`recovery_activation/` owns content-bound activation requests, authority receipts, plans, local policy verification, explicit preflight observations, and non-executing readiness reports. It must not authenticate identities, create approvals, mutate live pointers, activate a staged workspace, remove locks, resume writers, or perform provider/Git operations. Read `recovery_activation/AGENTS.md` before modifying this package.
<!-- PR14_RECOVERY_ACTIVATION_INDEX_END -->
""",
    "tests/AGENTS.md": """
<!-- PR14_RECOVERY_ACTIVATION_INDEX_START -->
## Recovery activation planning tests

Tests must cover plan/receipt identity, exact fields, separation of duties, role allowlists, finite TTL, rollback equality, bundle/audit/decision/target binding, stale pointer rejection, unknown fields, secret metadata, network URI rejection, structured exit code 2, existing output preservation, absence of execution commands, and `executed=false`. Tests must use only local deterministic fixtures.
<!-- PR14_RECOVERY_ACTIVATION_INDEX_END -->
""",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize idempotent PR-14 recovery activation indexes"
    )
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    failures: list[str] = []
    changed: list[str] = []
    for relative, block in BLOCKS.items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"required index target is missing: {relative}")
            continue
        current = path.read_text(encoding="utf-8")
        normalized = block.strip() + "\n"
        marker = normalized.splitlines()[0]
        if normalized in current:
            continue
        if marker in current:
            failures.append(f"conflicting PR-14 marker exists: {relative}")
            continue
        if args.check:
            failures.append(f"PR-14 index block is absent: {relative}")
            continue
        separator = "" if current.endswith("\n\n") else "\n"
        path.write_text(current + separator + normalized, encoding="utf-8")
        changed.append(relative)
    if failures:
        for failure in failures:
            print(failure)
        return 2
    if args.check:
        print(f"verified {len(BLOCKS)} PR-14 index blocks")
    else:
        print(f"updated {len(changed)} PR-14 index targets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
