from __future__ import annotations

import json
from pathlib import Path

import pytest

from post_training_rsi.config import PipelineConfig
from post_training_rsi.harness.coevolution_store import CoEvolutionRunStore
from post_training_rsi.harness.persistence import HarnessPointerStore, HarnessSnapshotStore
from post_training_rsi.lineage import (
    CheckpointBundleStore,
    ControlRecordStore,
    PeakPointerStore,
)
from post_training_rsi.orchestration.coevolution import (
    build_reference_coevolution_controller,
)


def _config(**overrides: object) -> PipelineConfig:
    value: dict[str, object] = {
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
            "harness_review_required": False,
        },
    }
    value.update(overrides)
    return PipelineConfig.from_mapping(value)


def test_reference_coevolution_promotes_then_rolls_back_and_stops(tmp_path: Path) -> None:
    config = _config()
    result = build_reference_coevolution_controller(
        config,
        workspace=tmp_path,
        run_id="coevolution-run-001",
    ).run()

    assert result.status == "STOPPED"
    assert result.state == "STOPPED"
    assert result.completed_cycles == 2
    assert result.current_cycle >= 3
    assert result.active_model_score > config.rsi.initial_score
    assert result.active_checkpoint_id.startswith("model-candidate-")
    assert result.active_harness_id.startswith("harness-")
    assert result.active_harness_score > 0.50
    assert result.pending_approval_request_id is None
    assert Path(result.report_path).is_file()

    payload = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    assert payload["active_checkpoint_id"] == result.active_checkpoint_id
    assert payload["completed_cycles"] == 2


def test_reference_coevolution_exact_resume_is_idempotent(tmp_path: Path) -> None:
    config = _config()
    first = build_reference_coevolution_controller(
        config,
        workspace=tmp_path,
        run_id="coevolution-run-resume",
    ).run()
    second = build_reference_coevolution_controller(
        config,
        workspace=tmp_path,
        run_id="coevolution-run-resume",
    ).run()

    assert second == first
    run_metadata = CoEvolutionRunStore(tmp_path).load(
        expected_run_id="coevolution-run-resume",
    )
    assert run_metadata.status == "STOPPED"
    assert run_metadata.completed_cycles == 2
    history = sorted((tmp_path / "coevolution" / "history").glob("*.json"))
    assert history
    assert history[-1].read_bytes() == (tmp_path / "coevolution/run.json").read_bytes()


def test_reference_coevolution_rejects_run_and_config_substitution(tmp_path: Path) -> None:
    config = _config()
    build_reference_coevolution_controller(
        config,
        workspace=tmp_path,
        run_id="coevolution-owner",
    ).run()

    with pytest.raises(Exception, match="different Run ID"):
        build_reference_coevolution_controller(
            config,
            workspace=tmp_path,
            run_id="coevolution-substituted",
        ).run()

    changed = _config(
        co_evolution={
            "max_cycles": 3,
            "max_outer_iterations": 4,
            "plateau_patience": 1,
            "target_traces": 4,
            "harness_min_improvement": 0.005,
            "model_min_improvement": 0.005,
        }
    )
    with pytest.raises(Exception, match="configuration hash mismatch"):
        build_reference_coevolution_controller(
            changed,
            workspace=tmp_path,
            run_id="coevolution-owner",
        ).run()


def test_reference_coevolution_persists_peak_harness_and_trace_lineage(tmp_path: Path) -> None:
    config = _config()
    result = build_reference_coevolution_controller(
        config,
        workspace=tmp_path,
        run_id="coevolution-lineage",
    ).run()

    control_store = ControlRecordStore(tmp_path)
    checkpoint_store = CheckpointBundleStore(tmp_path, control_store)
    peak_store = PeakPointerStore(tmp_path, control_store, checkpoint_store)
    harness_snapshot_store = HarnessSnapshotStore(tmp_path, control_store)
    harness_pointer_store = HarnessPointerStore(
        tmp_path,
        control_store,
        harness_snapshot_store,
    )

    peak = peak_store.load()
    harness = harness_pointer_store.load()
    assert peak is not None
    assert harness is not None
    assert peak.checkpoint_id == result.active_checkpoint_id
    assert harness.harness_id == result.active_harness_id
    assert checkpoint_store.load(peak.checkpoint_id).lineage_manifest.dataset_commit_hash
    assert harness_snapshot_store.load(harness.harness_id).spec.content_sha256

    trace_datasets = sorted((tmp_path / "trace-datasets").glob("*/accepted.jsonl"))
    assert len(trace_datasets) >= 2
    for dataset in trace_datasets:
        assert dataset.read_bytes()
        assert (dataset.parent / "filter_audit.jsonl").is_file()
        assert (dataset.parent / "harvest_manifest.json").is_file()

    rollback_markers = sorted((tmp_path / "quarantine").glob("*.json"))
    assert rollback_markers
    marker_payload = json.loads(rollback_markers[-1].read_text(encoding="utf-8"))
    assert marker_payload["subject_type"] == "CHECKPOINT"


def test_reference_coevolution_keeps_rejected_model_out_of_parent_chain(tmp_path: Path) -> None:
    config = _config()
    result = build_reference_coevolution_controller(
        config,
        workspace=tmp_path,
        run_id="coevolution-parent-invariant",
    ).run()

    peak_history = sorted((tmp_path / "peak_history").glob("*.json"))
    assert len(peak_history) == 2
    final_peak = json.loads(peak_history[-1].read_text(encoding="utf-8"))
    assert final_peak["checkpoint_id"] == result.active_checkpoint_id

    rollback_markers = sorted((tmp_path / "quarantine").glob("*.json"))
    rejected_ids = {
        json.loads(path.read_text(encoding="utf-8"))["subject_id"]
        for path in rollback_markers
    }
    assert result.active_checkpoint_id not in rejected_ids


def test_reference_coevolution_records_endpoint_teardown_and_model_evidence(tmp_path: Path) -> None:
    config = _config(
        co_evolution={
            "max_cycles": 1,
            "max_outer_iterations": 3,
            "plateau_patience": 1,
            "target_traces": 4,
            "harness_min_improvement": 0.005,
            "model_min_improvement": 0.005,
        }
    )
    build_reference_coevolution_controller(
        config,
        workspace=tmp_path,
        run_id="coevolution-evidence",
    ).run()

    evidence_payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "control/evidence").glob("*.json")
    ]
    kinds = {payload["kind"] for payload in evidence_payloads}
    assert {
        "TRACE_DATASET",
        "TRAINING_RESULT",
        "CHECKPOINT",
        "SERVING_ENDPOINT",
        "EVALUATION_RESULT",
        "SERVING_TEARDOWN",
        "PEAK_POINTER",
        "HARNESS_SNAPSHOT",
    } <= kinds
    teardown = [
        payload for payload in evidence_payloads if payload["kind"] == "SERVING_TEARDOWN"
    ]
    assert teardown
    assert all(payload["metadata"]["torn_down"] is True for payload in teardown)
