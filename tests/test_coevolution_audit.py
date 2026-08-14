from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from post_training_rsi.approval import ApprovalStore
from post_training_rsi.audit import AuditStatus, CoEvolutionAuditor
from post_training_rsi.config import PipelineConfig
from post_training_rsi.orchestration.coevolution import (
    build_reference_coevolution_controller,
)

FIXED_AUDIT_TIME = "2026-08-14T15:00:00Z"


def _config(*, harness_review_required: bool = False) -> PipelineConfig:
    return PipelineConfig.from_mapping(
        {
            "budget": {
                "total_limit_usd": 50.0,
                "per_iteration_limit_usd": 10.0,
                "max_consecutive_api_failures": 3,
            },
            "verification": {
                "min_entropy": 1.0,
                "min_distinct_2": 0.05,
                "min_type_token_ratio": 0.05,
                "max_semantic_similarity": 0.99,
                "benchmark_ngram_size": 5,
                "max_benchmark_overlap": 1.0,
                "max_lcs_ratio": 1.0,
                "min_acceptance_rate": 0.25,
            },
            "co_evolution": {
                "max_cycles": 2,
                "max_outer_iterations": 4,
                "plateau_patience": 1,
                "target_traces": 4,
                "harness_min_improvement": 0.005,
                "model_min_improvement": 0.005,
            },
            "approval": {
                "dataset_review_required": False,
                "checkpoint_review_required": False,
                "harness_review_required": harness_review_required,
                "sample_rate": 1.0,
                "min_sample_items": 1,
                "max_sample_items": 8,
                "decision_ttl_seconds": 86400,
                "allowed_reviewer_roles": ["release-manager"],
            },
        }
    )


def _completed_workspace(tmp_path: Path, *, run_id: str = "audit-run") -> Path:
    workspace = tmp_path / "workspace"
    result = build_reference_coevolution_controller(
        _config(),
        workspace=workspace,
        run_id=run_id,
    ).run()
    assert result.status == "STOPPED"
    return workspace


def _auditor(workspace: Path) -> CoEvolutionAuditor:
    return CoEvolutionAuditor(
        workspace,
        clock=lambda: FIXED_AUDIT_TIME,
    )


def _check(report, check_id: str):
    matches = [item for item in report.checks if item.check_id == check_id]
    assert matches, check_id
    return matches[-1]


def _file_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "reports/coevolution-audit.json":
            continue
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def test_clean_completed_workspace_passes_and_status_matches(tmp_path: Path) -> None:
    workspace = _completed_workspace(tmp_path)
    auditor = _auditor(workspace)

    status = auditor.status(expected_run_id="audit-run")
    report = auditor.audit(expected_run_id="audit-run")

    assert report.status is AuditStatus.PASS
    assert report.exit_code == 0
    assert status.runtime_status == "STOPPED"
    assert status.state == "STOPPED"
    assert status.run_id == report.run_id
    assert status.active_checkpoint_id == report.active["active_checkpoint_id"]
    assert status.active_harness_id == report.active["active_harness_id"]
    assert report.counts["transactions"] > 0
    assert report.counts["checkpoint_bundles"] >= 2
    assert report.counts["harness_snapshots"] >= 2
    assert report.counts["trace_datasets"] >= 2
    assert Path(report.report_path or "").is_file()


def test_audit_mutates_only_explicit_report(tmp_path: Path) -> None:
    workspace = _completed_workspace(tmp_path)
    before = _file_hashes(workspace)

    report = _auditor(workspace).audit()

    after = _file_hashes(workspace)
    assert report.status is AuditStatus.PASS
    assert after == before
    assert (workspace / "reports/coevolution-audit.json").is_file()


def test_run_revision_tamper_fails(tmp_path: Path) -> None:
    workspace = _completed_workspace(tmp_path)
    run_payload = json.loads((workspace / "coevolution/run.json").read_text())
    revision = int(run_payload["revision"])
    history = workspace / "coevolution/history" / f"revision-{revision:06d}.json"
    history.write_text("{}\n", encoding="utf-8")

    report = _auditor(workspace).audit(write_report=False)

    assert report.status is AuditStatus.FAIL
    assert _check(report, "audit-run-metadata").status is AuditStatus.FAIL


def test_control_record_tamper_fails(tmp_path: Path) -> None:
    workspace = _completed_workspace(tmp_path)
    evidence = next((workspace / "control/evidence").glob("*.json"))
    evidence.write_text("{}\n", encoding="utf-8")

    report = _auditor(workspace).audit(write_report=False)

    assert report.status is AuditStatus.FAIL
    assert any(
        item.check_id in {"audit-control-transaction", "audit-active-peak"}
        and item.status is AuditStatus.FAIL
        for item in report.checks
    )


def test_peak_artifact_tamper_fails(tmp_path: Path) -> None:
    workspace = _completed_workspace(tmp_path)
    peak = json.loads((workspace / "peak_checkpoint.json").read_text())
    checkpoint_id = peak["checkpoint_id"]
    manifest = json.loads(
        (
            workspace
            / "checkpoints"
            / checkpoint_id
            / "bundle_manifest.json"
        ).read_text()
    )
    uri = str(manifest["artifact_uri"])
    assert uri.startswith("file://")
    artifact = Path(uri.removeprefix("file://"))
    artifact.write_bytes(b"tampered-model-artifact")

    report = _auditor(workspace).audit(write_report=False)

    assert report.status is AuditStatus.FAIL
    assert _check(report, "audit-active-peak").status is AuditStatus.FAIL


def test_harness_snapshot_tamper_fails(tmp_path: Path) -> None:
    workspace = _completed_workspace(tmp_path)
    pointer = json.loads((workspace / "active_harness.json").read_text())
    harness_path = (
        workspace
        / "harness"
        / "snapshots"
        / str(pointer["harness_id"])
        / "harness.json"
    )
    value = json.loads(harness_path.read_text())
    value["system_prompt"] = "tampered Harness content"
    harness_path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    report = _auditor(workspace).audit(write_report=False)

    assert report.status is AuditStatus.FAIL
    assert _check(report, "audit-active-harness").status is AuditStatus.FAIL


def test_trace_dataset_missing_file_and_hash_tamper_fail(tmp_path: Path) -> None:
    workspace = _completed_workspace(tmp_path)
    dataset = next((workspace / "trace-datasets").iterdir())
    (dataset / "filter_audit.jsonl").unlink()

    missing = _auditor(workspace).audit(write_report=False)
    assert missing.status is AuditStatus.FAIL
    assert any(
        item.check_id == "audit-trace-dataset"
        and item.status is AuditStatus.FAIL
        for item in missing.checks
    )

    workspace = _completed_workspace(tmp_path / "hash-case", run_id="audit-hash")
    dataset = next((workspace / "trace-datasets").iterdir())
    accepted = dataset / "accepted.jsonl"
    accepted.write_text(
        accepted.read_text(encoding="utf-8")
        + '{"example_id":"tampered","prompt":"x","response":"y"}\n',
        encoding="utf-8",
    )

    hashed = _auditor(workspace).audit(write_report=False)
    assert hashed.status is AuditStatus.FAIL
    assert any(
        item.check_id == "audit-trace-dataset"
        and item.status is AuditStatus.FAIL
        for item in hashed.checks
    )


def test_approval_request_tamper_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "approval-workspace"
    result = build_reference_coevolution_controller(
        _config(harness_review_required=True),
        workspace=workspace,
        run_id="audit-approval",
    ).run()
    assert result.status == "AWAITING_HARNESS_APPROVAL"
    assert result.pending_approval_request_id is not None
    store = ApprovalStore(workspace)
    request_path = store.request_path(result.pending_approval_request_id)
    request_path.write_text("{}\n", encoding="utf-8")

    report = _auditor(workspace).audit(write_report=False)

    assert report.status is AuditStatus.FAIL
    assert any(
        item.check_id in {"audit-approval-record", "audit-pending-approval"}
        and item.status is AuditStatus.FAIL
        for item in report.checks
    )


def test_orphan_record_and_retained_lock_warn_and_strict_fails(tmp_path: Path) -> None:
    workspace = _completed_workspace(tmp_path)
    orphan = workspace / "control/evidence/ev-orphan-forensic.json"
    orphan.write_text('{"forensic":"orphan"}\n', encoding="utf-8")
    lock = workspace / "harness/.active.lock"
    lock.write_text("unknown-writer\n", encoding="utf-8")

    normal = _auditor(workspace).audit(write_report=False)
    strict = _auditor(workspace).audit(strict=True, write_report=False)

    assert normal.status is AuditStatus.WARN
    assert normal.exit_code == 0
    assert strict.status is AuditStatus.FAIL
    assert strict.exit_code == 2
    assert _check(normal, "audit-control-orphans").status is AuditStatus.WARN
    assert _check(normal, "audit-lock-files").status is AuditStatus.WARN
    assert orphan.is_file()
    assert lock.is_file()


def test_report_is_deterministic_with_injected_clock(tmp_path: Path) -> None:
    workspace = _completed_workspace(tmp_path)
    first = _auditor(workspace).audit(write_report=False)
    second = _auditor(workspace).audit(write_report=False)

    assert first == second
    assert first.generated_at == FIXED_AUDIT_TIME


def test_missing_workspace_fails_without_creating_it(tmp_path: Path) -> None:
    workspace = tmp_path / "missing"

    report = _auditor(workspace).audit(write_report=False)

    assert report.status is AuditStatus.FAIL
    assert not workspace.exists()
