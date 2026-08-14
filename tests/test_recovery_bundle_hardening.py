from __future__ import annotations

import os
from pathlib import Path

import pytest

from post_training_rsi.recovery_bundle import (
    RecoveryBundleConflictError,
    RecoveryBundleIntegrityError,
    create_bundle,
    stage_bundle,
    verify_bundle,
    verify_staged_directory,
)


def test_read_only_source_directory_can_be_staged_exactly(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    protected = source / "protected"
    protected.mkdir(parents=True)
    payload = protected / "evidence.json"
    payload.write_text('{"status":"verified"}\n', encoding="utf-8")
    os.chmod(payload, 0o440)
    os.chmod(protected, 0o550)
    bundle = tmp_path / "bundle"
    destination = tmp_path / "staged"

    try:
        create_bundle(source, bundle)
        stage_bundle(bundle, destination)

        assert (destination / "protected" / "evidence.json").read_bytes() == (
            payload.read_bytes()
        )
        assert _mode(destination / "protected") == 0o550
        assert _mode(destination / "protected" / "evidence.json") == 0o440
        verify_staged_directory(bundle, destination)
    finally:
        os.chmod(protected, 0o750)
        os.chmod(payload, 0o640)


def test_bundle_root_rejects_unmanifested_entries(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    (source / "run.json").write_text("{}\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    create_bundle(source, bundle)
    (bundle / "operator-note.txt").write_text("not in manifest", encoding="utf-8")

    with pytest.raises(RecoveryBundleIntegrityError, match="bundle root set mismatch"):
        verify_bundle(bundle)


def test_staged_file_mode_mutation_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    evidence = source / "evidence.json"
    evidence.write_text("{}\n", encoding="utf-8")
    os.chmod(evidence, 0o640)
    bundle = tmp_path / "bundle"
    destination = tmp_path / "staged"
    create_bundle(source, bundle)
    stage_bundle(bundle, destination)
    os.chmod(destination / "evidence.json", 0o600)

    with pytest.raises(RecoveryBundleIntegrityError, match="permission mode mismatch"):
        verify_staged_directory(bundle, destination)


def test_staged_directory_mode_mutation_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (nested / "evidence.json").write_text("{}\n", encoding="utf-8")
    os.chmod(nested, 0o750)
    bundle = tmp_path / "bundle"
    destination = tmp_path / "staged"
    create_bundle(source, bundle)
    stage_bundle(bundle, destination)
    os.chmod(destination / "nested", 0o700)

    with pytest.raises(RecoveryBundleIntegrityError, match="permission mode mismatch"):
        verify_staged_directory(bundle, destination)


def test_existing_empty_bundle_target_is_not_replaced(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    (source / "run.json").write_text("{}\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    with pytest.raises(RecoveryBundleConflictError, match="already exists"):
        create_bundle(source, bundle)
    assert list(bundle.iterdir()) == []


def test_stage_destination_must_be_outside_bundle(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    (source / "run.json").write_text("{}\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    create_bundle(source, bundle)

    with pytest.raises(RecoveryBundleIntegrityError, match="must be outside"):
        stage_bundle(bundle, bundle / "staged")


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777
