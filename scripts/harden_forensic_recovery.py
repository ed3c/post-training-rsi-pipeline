from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "src" / "post_training_rsi" / "recovery_bundle" / "bundle.py"
CLI_TEST = ROOT / "tests" / "test_recovery_bundle_cli.py"


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise RuntimeError(f"hardening target is missing: {label}")
    return text.replace(old, new, 1)


def harden_bundle() -> bool:
    original = BUNDLE.read_text(encoding="utf-8")
    text = original
    text = text.replace(
        "from typing import Any, Literal, Mapping, cast",
        "from typing import Literal, Mapping, cast",
    )

    verify_anchor = """    manifest = load_manifest(bundle_root)\n    blobs_root = bundle_root / BLOBS_DIRECTORY\n"""
    verify_replacement = """    manifest = load_manifest(bundle_root)\n    root_items = tuple(bundle_root.iterdir())\n    for item in root_items:\n        _reject_symlink(item, \"bundle root entry\")\n    actual_root_names = {item.name for item in root_items}\n    expected_root_names = {MANIFEST_NAME, BLOBS_DIRECTORY}\n    if actual_root_names != expected_root_names:\n        missing = sorted(expected_root_names - actual_root_names)\n        extra = sorted(actual_root_names - expected_root_names)\n        raise RecoveryBundleIntegrityError(\n            f\"bundle root set mismatch: missing={missing}, extra={extra}\"\n        )\n    blobs_root = bundle_root / BLOBS_DIRECTORY\n"""
    text = _replace_once(
        text,
        verify_anchor,
        verify_replacement,
        "verify bundle root entry set",
    )

    directory_anchor = """        for entry in manifest.entries:\n            if entry.kind != \"directory\":\n                continue\n            target = _safe_destination(staging_root, entry.path)\n            target.mkdir(parents=True, exist_ok=False)\n            os.chmod(target, entry.mode)\n        blobs_root = bundle_root / BLOBS_DIRECTORY\n"""
    directory_replacement = """        directory_entries = tuple(\n            entry for entry in manifest.entries if entry.kind == \"directory\"\n        )\n        for entry in sorted(\n            directory_entries,\n            key=lambda item: (len(PurePosixPath(item.path).parts), item.path),\n        ):\n            target = _safe_destination(staging_root, entry.path)\n            target.mkdir(parents=True, exist_ok=False)\n            os.chmod(target, 0o700)\n        blobs_root = bundle_root / BLOBS_DIRECTORY\n"""
    text = _replace_once(
        text,
        directory_anchor,
        directory_replacement,
        "stage directories with private temporary modes",
    )

    file_anchor = """            target.parent.mkdir(parents=True, exist_ok=True)\n            _copy_blob_to_target(blobs_root / entry.sha256, target, entry.mode)\n        _verify_directory_against_manifest(staging_root, manifest)\n"""
    file_replacement = """            target.parent.mkdir(parents=True, exist_ok=True)\n            _copy_blob_to_target(blobs_root / entry.sha256, target, entry.mode)\n        for entry in sorted(\n            directory_entries,\n            key=lambda item: (-len(PurePosixPath(item.path).parts), item.path),\n        ):\n            os.chmod(_safe_destination(staging_root, entry.path), entry.mode)\n        _verify_directory_against_manifest(staging_root, manifest)\n"""
    text = _replace_once(
        text,
        file_anchor,
        file_replacement,
        "apply final directory modes after reconstruction",
    )

    mode_anchor = """        metadata = candidate.lstat()\n        if entry.kind == \"directory\":\n"""
    mode_replacement = """        metadata = candidate.lstat()\n        actual_mode = stat.S_IMODE(metadata.st_mode) & 0o777\n        if actual_mode != entry.mode:\n            raise RecoveryBundleIntegrityError(\n                f\"staged permission mode mismatch: {entry.path}\"\n            )\n        if entry.kind == \"directory\":\n"""
    text = _replace_once(
        text,
        mode_anchor,
        mode_replacement,
        "verify staged permission modes",
    )

    if text != original:
        BUNDLE.write_text(text, encoding="utf-8")
        return True
    return False


def harden_cli_test() -> bool:
    original = CLI_TEST.read_text(encoding="utf-8")
    pattern = re.compile(
        r"def test_unreferenced_blob_fails_closed\(tmp_path: Path\) -> None:\n"
        r".*?\n\ndef capsys_or_empty\(capsys: object \| None\) -> str:\n"
        r"    return \"\{\}\"\n?",
        flags=re.DOTALL,
    )
    replacement = """def test_unreferenced_blob_fails_closed(\n    tmp_path: Path,\n    capsys: pytest.CaptureFixture[str],\n) -> None:\n    source = tmp_path / \"workspace\"\n    source.mkdir()\n    (source / \"data.json\").write_text(\"{}\\n\", encoding=\"utf-8\")\n    bundle = tmp_path / \"bundle\"\n    create_bundle(source, bundle)\n    extra = bundle / \"blobs\" / (\"f\" * 64)\n    extra.write_bytes(b\"orphan\")\n\n    result = main([\"verify\", \"--bundle\", str(bundle)])\n\n    assert result == 2\n    value = json.loads(capsys.readouterr().out)\n    assert value[\"status\"] == \"error\"\n    assert \"blob set mismatch\" in value[\"message\"]\n"""
    if pattern.search(original):
        updated = pattern.sub(replacement, original, count=1)
    elif replacement.strip() in original:
        return False
    else:
        raise RuntimeError("CLI hardening target is missing")
    CLI_TEST.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    changed = [
        name
        for name, was_changed in (
            (str(BUNDLE.relative_to(ROOT)), harden_bundle()),
            (str(CLI_TEST.relative_to(ROOT)), harden_cli_test()),
        )
        if was_changed
    ]
    if changed:
        print("hardened: " + ", ".join(changed))
    else:
        print("forensic recovery hardening is already applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
