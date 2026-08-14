from __future__ import annotations

import json
from pathlib import Path

from post_training_rsi.approval import ApprovalStore, record_sha256
from post_training_rsi.config import PipelineConfig
from post_training_rsi.control_plane import DecisionSubject
from post_training_rsi.lineage import (
    CheckpointBundleStore,
    ControlRecordStore,
    PeakPointerStore,
    QuarantineStore,
)
from post_training_rsi.orchestration import build_converged_rsi_controller

STARTED_AT = "2026-08-14T09:00:00Z"


def permissive_config(
    *,
    max_iterations: int = 3,
    dataset_review_required: bool = False,
    checkpoint_review_required: bool = False,
) -> PipelineConfig:
    return PipelineConfig.from_mapping(
        {
            "verification": {
                "min_entropy": 0.0,
                "min_distinct_2": 0.0,
                "min_type_token_ratio": 0.0,
                "max_semantic_similarity": 1.0,
                "benchmark_ngram_size": 13,
                "max_benchmark_overlap": 1.0,
                "max_lcs_ratio": 1.0,
                "min_acceptance_rate": 0.0,
            },
            "rsi": {
                "max_iterations": max_iterations,
                "plateau_patience": 2,
                "min_improvement": 0.005,
                "regression_tolerance": 0.05,
                "examples_per_iteration": 4,
                "initial_score": 0.50,
                "benchmark_id": "mock-agent-benchmark",
            },
            "approval": {
                "dataset_review_required": dataset_review_required,
                "checkpoint_review_required": checkpoint_review_required,
                "harness_review_required": False,
                "sample_rate": 1.0,
                "min_sample_items": 1,
                "max_sample_items": 10,
                "decision_ttl_seconds": 86_400,
                "allowed_reviewer_roles": [
                    "researcher",
                    "release-manager",
                ],
            },
        }
    )


def controller(config: PipelineConfig, workspace: Path, *, run_id: str):
    return build_converged_rsi_controller(
        config,
        workspace=workspace,
        run_id=run_id,
        started_at=STARTED_AT,
        now=lambda: STARTED_AT,
    )


def test_default_rsi_stops_fail_closed_and_replays_exactly(tmp_path: Path) -> None:
    config = PipelineConfig.from_mapping(
        {
            "verification": {"min_entropy": 100.0},
            "rsi": {"max_iterations": 2, "examples_per_iteration": 4},
        }
    )
    first = controller(config, tmp_path, run_id="default-run").run()
    replay = controller(config, tmp_path, run_id="default-run").run()

    assert first.to_dict() == replay.to_dict()
    assert first.state == "STOPPED"
    assert first.stop_reason == "NO_ACCEPTED_DATA"
    assert first.peak_score == 0.50
    assert first.total_cost_usd == 1.0

    quarantine_files = sorted((tmp_path / "quarantine").glob("*.json"))
    assert len(quarantine_files) == 1
    marker = json.loads(quarantine_files[0].read_text(encoding="utf-8"))
    assert marker["subject_type"] == "DATASET"
    assert marker["reason_code"] == "no_accepted_data"


def test_multi_iteration_rsi_preserves_historical_peak_and_auditable_lineage(
    tmp_path: Path,
) -> None:
    config = permissive_config(max_iterations=3)
    first = controller(config, tmp_path, run_id="multi-run").run()
    replay = controller(config, tmp_path, run_id="multi-run").run()

    assert first.to_dict() == replay.to_dict()
    assert first.state == "STOPPED"
    assert first.stop_reason == "MAX_ITERATIONS"
    assert first.iteration == 3
    assert first.peak_score == 0.64
    assert first.peak_checkpoint_id is not None
    assert "iter-002" in first.peak_checkpoint_id
    assert first.total_cost_usd == 3.0

    control_store = ControlRecordStore(tmp_path)
    checkpoint_store = CheckpointBundleStore(tmp_path, control_store)
    peak_store = PeakPointerStore(tmp_path, control_store, checkpoint_store)
    peak = peak_store.load()
    assert peak is not None
    assert peak.checkpoint_id == first.peak_checkpoint_id
    assert peak.iteration == 2
    assert peak.score == 0.64

    bundle = checkpoint_store.load(peak.checkpoint_id)
    assert bundle.lineage_manifest.iteration == 2
    assert bundle.lineage_manifest.benchmark_score == 0.64
    assert bundle.lineage_manifest.parent_checkpoint_id is not None
    assert bundle.manifest.control_transaction_id == peak.control_transaction_id

    rejected_id = next(
        path.name
        for path in (tmp_path / "model_artifacts").iterdir()
        if "iter-003" in path.name
    )
    quarantine_store = QuarantineStore(tmp_path, control_store)
    rejected = quarantine_store.load(
        iteration=3,
        subject_type=DecisionSubject.CHECKPOINT,
        subject_id=rejected_id,
    )
    assert rejected.reason_code == "candidate_not_above_peak"

    # A continue snapshot from iteration 2 and a terminal snapshot from
    # iteration 3 both carry iteration=3, but their immutable IDs must differ.
    iteration_three_snapshots = [
        control_store.load_snapshot(path.stem)
        for path in (tmp_path / "control" / "snapshots").glob("*.json")
        if json.loads(path.read_text(encoding="utf-8"))["iteration"] == 3
    ]
    assert len({item.snapshot_id for item in iteration_three_snapshots}) == len(
        iteration_three_snapshots
    )
    assert {item.state.value for item in iteration_three_snapshots} >= {
        "DIAGNOSE",
        "STOPPED",
    }


def test_dataset_and_model_approvals_pause_resume_and_promote(tmp_path: Path) -> None:
    config = permissive_config(
        max_iterations=1,
        dataset_review_required=True,
        checkpoint_review_required=True,
    )
    rsi = controller(config, tmp_path, run_id="approval-run")

    dataset_pending = rsi.run()
    assert dataset_pending.status == "awaiting_approval"
    assert dataset_pending.state == "DATA_REVIEW_PENDING"
    assert dataset_pending.pending_approval_request_id is not None

    approval_store = ApprovalStore(tmp_path)
    dataset_request = approval_store.load_request(
        dataset_pending.pending_approval_request_id
    )
    dataset_decision = rsi.approval_service.review(
        request_id=dataset_request.request_id,
        expected_request_sha256=record_sha256(dataset_request.to_dict()),
        approved=True,
        reviewer_id="dataset-reviewer",
        reviewer_role="researcher",
        reason="Dataset sample and verification evidence reviewed.",
    )
    # Logical RSI timestamps may be ahead of wall time; default review time
    # must still be valid and deterministic at or after the request.
    assert dataset_decision.decision.decided_at >= dataset_request.requested_at

    model_pending = controller(config, tmp_path, run_id="approval-run").run()
    assert model_pending.status == "awaiting_approval"
    assert model_pending.state == "MODEL_REVIEW_PENDING"
    assert model_pending.pending_approval_request_id is not None

    model_request = approval_store.load_request(
        model_pending.pending_approval_request_id
    )
    model_decision = rsi.approval_service.review(
        request_id=model_request.request_id,
        expected_request_sha256=record_sha256(model_request.to_dict()),
        approved=True,
        reviewer_id="release-reviewer",
        reviewer_role="release-manager",
        reason="Candidate score, artifact hash, and lineage reviewed.",
    )
    assert model_decision.decision.decided_at >= model_request.requested_at

    completed = controller(config, tmp_path, run_id="approval-run").run()
    assert completed.status == "completed"
    assert completed.state == "STOPPED"
    assert completed.stop_reason == "MAX_ITERATIONS"
    assert completed.peak_score == 0.58
    assert completed.peak_checkpoint_id is not None
    assert "iter-001" in completed.peak_checkpoint_id

    replay = controller(config, tmp_path, run_id="approval-run").run()
    assert replay.to_dict() == completed.to_dict()
