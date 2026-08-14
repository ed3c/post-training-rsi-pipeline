from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _replace_once(path: Path, old: str, new: str, label: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return False
    if old not in text:
        raise RuntimeError(f"reconciliation target is missing: {label}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


def _harden_sources() -> list[str]:
    changed: list[str] = []
    cli = ROOT / "src/post_training_rsi/recovery_activation/__main__.py"
    if _replace_once(
        cli,
        '''    if not isinstance(value, Mapping):\n        raise RecoveryActivationContractError(f"{label} must be a JSON object")\n    if any(not isinstance(key, str) for key in value):\n        raise RecoveryActivationContractError(f"{label} keys must be strings")\n    return value\n''',
        '''    if not isinstance(value, dict):\n        raise RecoveryActivationContractError(f"{label} must be a JSON object")\n    result: dict[str, object] = {}\n    for key, item in value.items():\n        if not isinstance(key, str):\n            raise RecoveryActivationContractError(f"{label} keys must be strings")\n        result[key] = item\n    return result\n''',
        "CLI JSON object normalization",
    ):
        changed.append(str(cli.relative_to(ROOT)))

    contracts = ROOT / "src/post_training_rsi/recovery_activation/contracts.py"
    if _replace_once(
        contracts,
        '''    if not isinstance(item, Mapping):\n        raise RecoveryActivationContractError(f"{field_name} must be an object")\n    if any(not isinstance(key, str) for key in item):\n        raise RecoveryActivationContractError(f"{field_name} keys must be strings")\n    return item\n''',
        '''    if not isinstance(item, Mapping):\n        raise RecoveryActivationContractError(f"{field_name} must be an object")\n    result: dict[str, object] = {}\n    for key, nested in item.items():\n        if not isinstance(key, str):\n            raise RecoveryActivationContractError(f"{field_name} keys must be strings")\n        result[key] = nested\n    return result\n''',
        "contract nested mapping normalization",
    ):
        changed.append(str(contracts.relative_to(ROOT)))

    workflow = ROOT / ".github/workflows/recovery-activation-plan.yml"
    if _replace_once(
        workflow,
        '''      - name: No-activation CLI assertion\n        shell: bash\n        run: |\n          set -euo pipefail\n          python -m post_training_rsi.recovery_activation --help > /tmp/recovery-help.txt\n          grep -q "build" /tmp/recovery-help.txt\n          grep -q "verify" /tmp/recovery-help.txt\n          grep -q "preflight" /tmp/recovery-help.txt\n          if grep -Eq "activate|apply|switch|resume|rollback" /tmp/recovery-help.txt; then\n            echo "Forbidden recovery execution command found in package CLI help"\n            exit 1\n          fi\n''',
        '''      - name: No-activation CLI assertion\n        run: |\n          python - <<'PY'\n          import argparse\n\n          from post_training_rsi.recovery_activation.__main__ import _parser\n\n          parser = _parser()\n          subparsers = [\n              action\n              for action in parser._actions\n              if isinstance(action, argparse._SubParsersAction)\n          ]\n          if len(subparsers) != 1:\n              raise SystemExit("recovery CLI subparser contract is ambiguous")\n          commands = set(subparsers[0].choices)\n          expected = {"build", "verify", "preflight"}\n          if commands != expected:\n              raise SystemExit(\n                  f"recovery CLI commands mismatch: expected={sorted(expected)}, "\n                  f"actual={sorted(commands)}"\n              )\n          PY\n''',
        "focused workflow command assertion",
    ):
        changed.append(str(workflow.relative_to(ROOT)))
    return changed


def _sync_indexes() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/sync_recovery_activation_index.py"],
        cwd=ROOT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("PR-14 index synchronization failed")
    checked = subprocess.run(
        [sys.executable, "scripts/sync_recovery_activation_index.py", "--check"],
        cwd=ROOT,
        check=False,
    )
    if checked.returncode != 0:
        raise RuntimeError("PR-14 index verification failed")


def _normalize_scope() -> list[str]:
    changed: list[str] = []
    manifest_path = ROOT / "docs/recovery-activation-manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    desired_paths = [
        ".github/workflows/recovery-activation-plan.yml",
        "scripts/reconcile_recovery_activation.py",
        "scripts/sync_recovery_activation_index.py",
        "src/post_training_rsi/recovery_activation",
        "tests/test_recovery_activation.py",
        "tests/test_recovery_activation_cli.py",
        "tests/test_recovery_activation_manifest.py",
        "docs/recovery-activation-plan.md",
        "docs/recovery-activation-manifest.json",
        "docs/validation/recovery-activation-plan-latest.json",
        "docs/validation/recovery-activation-plan-latest.md",
    ]
    if value.get("owned_paths") != desired_paths:
        value["owned_paths"] = desired_paths
        manifest_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        changed.append(str(manifest_path.relative_to(ROOT)))

    document_path = ROOT / "docs/recovery-activation-plan.md"
    document = document_path.read_text(encoding="utf-8")
    anchor = "  - .github/workflows/recovery-activation-plan.yml\n"
    addition = (
        "  - .github/workflows/recovery-activation-plan.yml\n"
        "  - scripts/reconcile_recovery_activation.py\n"
        "  - scripts/sync_recovery_activation_index.py\n"
    )
    if addition not in document:
        if anchor not in document:
            raise RuntimeError("PR-14 allowed-path anchor is missing")
        document_path.write_text(document.replace(anchor, addition, 1), encoding="utf-8")
        changed.append(str(document_path.relative_to(ROOT)))
    return changed


def main() -> int:
    changed = _harden_sources()
    _sync_indexes()
    changed.extend(_normalize_scope())
    if changed:
        print("reconciled: " + ", ".join(sorted(set(changed))))
    else:
        print("recovery activation branch is already reconciled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
