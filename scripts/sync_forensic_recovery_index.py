from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BLOCKS = {
    "README.md": """
<!-- PR13_FORENSIC_RECOVERY_INDEX_START -->
## Forensic recovery successor — Draft PR #13

The read-only Co-Evolution audit boundary is followed by a content-addressed, local-only recovery slice:

```text
PR #12  read-only Co-Evolution audit and recovery diagnosis
└── PR #13  deterministic forensic bundle + inactive staged restore
```

PR #13 owns only:

```text
local workspace scan
  → content-addressed blobs
  → canonical recovery manifest
  → exact bundle verification
  → reconstruction into a new directory
  → exact staged-copy verification
  → STAGED_INACTIVE
```

It has no `ACTIVATE` transition and never overwrites the live workspace. See [`docs/forensic-recovery-bundle.md`](docs/forensic-recovery-bundle.md) and the machine-readable [`docs/forensic-recovery-manifest.json`](docs/forensic-recovery-manifest.json).
<!-- PR13_FORENSIC_RECOVERY_INDEX_END -->
""",
    "AGENTS.md": """
<!-- PR13_FORENSIC_RECOVERY_INDEX_START -->
## Forensic recovery bundle successor

For work under `src/post_training_rsi/recovery_bundle/`, read:

```text
src/post_training_rsi/recovery_bundle/AGENTS.md
  → docs/coevolution-audit-recovery.md
  → docs/forensic-recovery-bundle.md
  → docs/forensic-recovery-manifest.json
```

A verified bundle or staged directory is not activation authority. Retained recovery lock removal, storage destination authorization, encryption, retention, strict staged audit, production pointer switching, rollback, and writer resume remain human-owned operations.
<!-- PR13_FORENSIC_RECOVERY_INDEX_END -->
""",
    "docs/README.md": """
<!-- PR13_FORENSIC_RECOVERY_INDEX_START -->
## Forensic recovery successor

| Document | Purpose |
|---|---|
| [`forensic-recovery-bundle.md`](forensic-recovery-bundle.md) | content-addressed local export, exact verification, and inactive staged restore |
| [`forensic-recovery-manifest.json`](forensic-recovery-manifest.json) | machine-readable PR-13 identity, State Machine, paths, invariants, and human-owned boundary |

PR #13 follows the read-only audit boundary. It implements no automatic repair or production activation.
<!-- PR13_FORENSIC_RECOVERY_INDEX_END -->
""",
    "docs/implementation-status.md": """
<!-- PR13_FORENSIC_RECOVERY_INDEX_START -->
## PR-13 forensic recovery bundle

Status: **Draft implemented component; not production activation**

```text
Branch: feat/forensic-recovery-bundle
Parent: feat/coevolution-audit-recovery / PR #12
Schema: post-training-rsi.recovery-bundle/v1
CLI: python -m post_training_rsi.recovery_bundle
```

Implemented:

```text
create
verify
stage into an absent directory
verify-stage
content-addressed deduplication
manifest/blob/hash/path/symlink checks
retained create/stage locks
activated=false terminal result
```

Not implemented:

```text
remote backup
encryption/key management
retention/legal hold
distributed writer exclusion
automatic recovery
production pointer activation
RPO/RTO guarantees
```
<!-- PR13_FORENSIC_RECOVERY_INDEX_END -->
""",
    "docs/stacked-pr-plan.md": """
<!-- PR13_FORENSIC_RECOVERY_INDEX_START -->
## PR-13 recovery successor

```text
PR #11  Co-Evolution convergence
└── PR #12  read-only audit/recovery boundary
    └── PR #13  forensic bundle and inactive staged restore
```

PR #13 owns `src/post_training_rsi/recovery_bundle/**`, focused tests/workflows, and its component documentation. It must not change live model/Harness/Peak pointers. A future activation PR requires explicit human authority, expected-generation compare-and-swap, strict audit PASS, rollback pointer, and post-switch audit.

Git Town remains disabled until the repository commits the exact version, configuration, verified parent graph, worktree leases, non-interactive rehearsal, no-push evidence, and active `stack.tsv`.
<!-- PR13_FORENSIC_RECOVERY_INDEX_END -->
""",
    "docs/traceability-index.md": """
<!-- PR13_FORENSIC_RECOVERY_INDEX_START -->
## Recovery bundle traceability

| Requirement ID | Requirement | Code | Test / evidence | Status |
|---|---|---|---|---|
| `REC-BUNDLE-01` | deterministic content-addressed local export | `recovery_bundle/bundle.py:create_bundle` | `test_bundle_is_deterministic_and_deduplicates_blobs` | Implemented component |
| `REC-BUNDLE-02` | canonical manifest and exact blob verification | `verify_bundle`, `load_manifest` | blob/manifest tamper tests | Implemented component |
| `REC-BUNDLE-03` | reject path traversal, symlink, special file, and nested output | path/source guards | focused negative matrix | Implemented component |
| `REC-BUNDLE-04` | restore only into a new inactive directory | `stage_bundle` | round-trip and existing-destination tests | Implemented component |
| `REC-BUNDLE-05` | verify staged path set and bytes | `verify_staged_directory` | staged mutation test | Implemented component |
| `REC-BUNDLE-06` | no automatic activation | no `ACTIVATE` edge or command | `test_forensic_recovery_state_machine_has_no_activation_edge` | Verified by contract test |
| `REC-BUNDLE-07` | retained writer locks fail closed | create/stage lock files | retained-lock test | Implemented component |
| `REC-BUNDLE-08` | machine-readable ownership and non-claims | `forensic-recovery-manifest.json` | `test_forensic_recovery_manifest.py` | Implemented component |
<!-- PR13_FORENSIC_RECOVERY_INDEX_END -->
""",
    "docs/coevolution-audit-recovery.md": """
<!-- PR13_FORENSIC_RECOVERY_INDEX_START -->
## Successor: deterministic bundle and inactive stage

PR #13 implements the byte-preservation portion of this playbook:

```text
strict audit diagnosis
  → human selects a consistent workspace generation
  → create content-addressed recovery bundle
  → verify bundle
  → stage into a new inactive directory
  → verify staged copy
```

It does not select the generation automatically and cannot activate the staged copy. Continue to require a strict staged audit and separate human-authorized compare-and-swap before any production pointer change. See [`forensic-recovery-bundle.md`](forensic-recovery-bundle.md).
<!-- PR13_FORENSIC_RECOVERY_INDEX_END -->
""",
    "src/post_training_rsi/AGENTS.md": """
<!-- PR13_FORENSIC_RECOVERY_INDEX_START -->
## `recovery_bundle/` ownership

`recovery_bundle/` owns deterministic local export, content-addressed blobs, manifest verification, inactive reconstruction, and staged-copy verification. It must not own semantic Co-Evolution audit, approval authority, live pointer mutation, provider operations, remote storage credentials, retained-lock recovery, or automatic disaster recovery. Read `recovery_bundle/AGENTS.md` before modifying this package.
<!-- PR13_FORENSIC_RECOVERY_INDEX_END -->
""",
    "tests/AGENTS.md": """
<!-- PR13_FORENSIC_RECOVERY_INDEX_START -->
## Forensic recovery tests

Recovery tests must use temporary local directories and cover deterministic identity, deduplication, exact fields, path containment, symlinks, special files, tamper, retained locks, new-destination-only staging, exact staged bytes, structured exit codes, and absence of activation. They must not require private production data, network access, API keys, cloud storage, GPU, or mutable production services.
<!-- PR13_FORENSIC_RECOVERY_INDEX_END -->
""",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize the idempotent PR-13 recovery index blocks"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that all exact blocks are present without writing",
    )
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
        normalized_block = block.strip() + "\n"
        start_marker = normalized_block.splitlines()[0]
        if normalized_block in current:
            continue
        if start_marker in current:
            failures.append(
                f"index target contains a conflicting PR-13 marker: {relative}"
            )
            continue
        if args.check:
            failures.append(f"PR-13 index block is absent: {relative}")
            continue
        separator = "" if current.endswith("\n\n") else "\n"
        path.write_text(
            current + separator + normalized_block,
            encoding="utf-8",
        )
        changed.append(relative)
    if failures:
        for failure in failures:
            print(failure)
        return 2
    if args.check:
        print(f"verified {len(BLOCKS)} PR-13 index blocks")
    else:
        print(f"updated {len(changed)} PR-13 index targets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
