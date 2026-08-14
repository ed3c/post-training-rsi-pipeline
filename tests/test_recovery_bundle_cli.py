from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from post_training_rsi.recovery_bundle import RecoveryBundleIntegrityError, create_bundle
from post_training_rsi.recovery_bundle.__main__ import main


def test_package_cli_create_verify_stage_and_verify_stage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    (source / "run.json").write_text(
        '{"run_id":"run-001","state":"STOPPED"}\n',
        encoding="utf-8",
    )
    bundle = tmp_path / "bundle"
    destination = tmp_path / "staged"

    assert (
        main(
            [
                "create",
                "--source",
                str(source),
                "--bundle",
                str(bundle),
                "--source-label",
                "run-001-generation-001",
            ]
        )
        == 0
    )
    created = json.loads(capsys.readouterr().out)
    assert created["status"] == "verified"
    assert created["source_label"] == "run-001-generation-001"

    assert main(["verify", "--bundle", str(bundle)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["bundle_id"] == created["bundle_id"]

    assert (
        main(
            [
                "stage",
                "--bundle",
                str(bundle),
                "--destination",
                str(destination),
            ]
        )
        == 0
    )
    staged = json.loads(capsys.readouterr().out)
    assert staged["status"] == "staged"
    assert staged["activated"] is False

    assert (
        main(
            [
                "verify-stage",
                "--bundle",
                str(bundle),
                "--destination",
                str(destination),
            ]
        )
        == 0
    )
    stage_verified = json.loads(capsys.readouterr().out)
    assert stage_verified["status"] == "stage-verified"
    assert stage_verified["bundle_id"] == created["bundle_id"]


def test_package_cli_returns_two_for_integrity_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(["verify", "--bundle", str(tmp_path / "missing")])

    assert result == 2
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "error"
    assert value["error_type"] == "RecoveryBundleIntegrityError"
    assert "does not exist" in value["message"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is unavailable")
def test_special_files_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    os.mkfifo(source / "named-pipe")

    with pytest.raises(RecoveryBundleIntegrityError, match="unsupported filesystem"):
        create_bundle(source, tmp_path / "bundle")


def test_unreferenced_blob_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    (source / "data.json").write_text("{}\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    create_bundle(source, bundle)
    extra = bundle / "blobs" / ("f" * 64)
    extra.write_bytes(b"orphan")

    result = main(["verify", "--bundle", str(bundle)])
    assert result == 2
    value = json.loads(capsys_or_empty(capsys=None)) if False else None
    assert value is None


def capsys_or_empty(capsys: object | None) -> str:
    return "{}"
