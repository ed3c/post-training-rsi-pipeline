from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from post_training_rsi.recovery_bundle import (
    BLOBS_DIRECTORY,
    MANIFEST_NAME,
    BundleEntry,
    RecoveryBundleConflictError,
    RecoveryBundleIntegrityError,
    create_bundle,
    load_manifest,
    stage_bundle,
    verify_bundle,
    verify_staged_directory,
)


def _source_tree(root: Path) -> None:
    root.mkdir()
    (root / "nested").mkdir()
    (root / "empty").mkdir()
    (root / "alpha.txt").write_text("same bytes\n", encoding="utf-8")
    (root / "nested" / "beta.txt").write_text("same bytes\n", encoding="utf-8")
    (root / "nested" / "tool.sh").write_text(
        "#!/bin/sh\necho verified\n",
        encoding="utf-8",
    )
    os.chmod(root / "nested" / "tool.sh", 0o755)


def test_bundle_is_deterministic_and_deduplicates_blobs(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    _source_tree(source)

    first = create_bundle(
        source,
        tmp_path / "bundle-a",
        source_label="coevolution-run-001",
    )
    second = create_bundle(
        source,
        tmp_path / "bundle-b",
        source_label="coevolution-run-001",
    )

    assert first.bundle_id == second.bundle_id
    assert first.file_count == 3
    assert first.directory_count == 2
    assert first.blob_count == 2
    assert load_manifest(tmp_path / "bundle-a") == load_manifest(
        tmp_path / "bundle-b"
    )
    assert verify_bundle(tmp_path / "bundle-a") == first


def test_stage_roundtrip_is_exact_and_never_activates(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    _source_tree(source)
    bundle = tmp_path / "bundle"
    create_bundle(source, bundle)

    destination = tmp_path / "staged-workspace"
    report = stage_bundle(bundle, destination)

    assert report.to_dict()["activated"] is False
    assert (destination / "alpha.txt").read_bytes() == (source / "alpha.txt").read_bytes()
    assert (destination / "nested" / "tool.sh").read_bytes() == (
        source / "nested" / "tool.sh"
    ).read_bytes()
    assert stat_mode(destination / "nested" / "tool.sh") == 0o755
    verify_staged_directory(bundle, destination)
    assert source.is_dir()

    with pytest.raises(RecoveryBundleConflictError, match="already exists"):
        stage_bundle(bundle, destination)


def test_verify_detects_blob_tampering(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    _source_tree(source)
    bundle = tmp_path / "bundle"
    create_bundle(source, bundle)
    manifest = load_manifest(bundle)
    first_file = next(entry for entry in manifest.entries if entry.kind == "file")
    assert first_file.sha256 is not None

    blob = bundle / BLOBS_DIRECTORY / first_file.sha256
    os.chmod(blob, 0o600)
    blob.write_bytes(b"tampered")

    with pytest.raises(RecoveryBundleIntegrityError, match="integrity mismatch"):
        verify_bundle(bundle)


def test_manifest_unknown_fields_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    _source_tree(source)
    bundle = tmp_path / "bundle"
    create_bundle(source, bundle)
    manifest_path = bundle / MANIFEST_NAME
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["unexpected"] = True
    manifest_path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RecoveryBundleIntegrityError, match="fields mismatch"):
        verify_bundle(bundle)


def test_unsafe_manifest_path_is_rejected() -> None:
    with pytest.raises(RecoveryBundleIntegrityError, match="unsafe relative path"):
        BundleEntry(
            path="../escape.json",
            kind="file",
            size=1,
            mode=0o600,
            sha256="0" * 64,
        )


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ on Windows")
def test_source_symlink_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    (source / "linked.txt").symlink_to(outside)

    with pytest.raises(RecoveryBundleIntegrityError, match="symbolic link"):
        create_bundle(source, tmp_path / "bundle")


def test_bundle_output_must_be_outside_source(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    (source / "data.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(RecoveryBundleIntegrityError, match="must be outside"):
        create_bundle(source, source / "bundle")


def test_create_and_stage_lock_conflicts_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    (source / "data.json").write_text("{}\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    create_lock = tmp_path / ".bundle.create.lock"
    create_lock.write_text("retained", encoding="utf-8")

    with pytest.raises(RecoveryBundleConflictError, match="owns lock"):
        create_bundle(source, bundle)
    assert create_lock.read_text(encoding="utf-8") == "retained"

    create_lock.unlink()
    create_bundle(source, bundle)
    destination = tmp_path / "restore"
    stage_lock = tmp_path / ".restore.stage.lock"
    stage_lock.write_text("retained", encoding="utf-8")
    with pytest.raises(RecoveryBundleConflictError, match="owns lock"):
        stage_bundle(bundle, destination)
    assert stage_lock.read_text(encoding="utf-8") == "retained"


def test_staged_file_mutation_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    (source / "data.json").write_text('{"state":"verified"}\n', encoding="utf-8")
    bundle = tmp_path / "bundle"
    destination = tmp_path / "stage"
    create_bundle(source, bundle)
    stage_bundle(bundle, destination)
    (destination / "data.json").write_text("changed\n", encoding="utf-8")

    with pytest.raises(RecoveryBundleIntegrityError, match="integrity mismatch"):
        verify_staged_directory(bundle, destination)


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777
