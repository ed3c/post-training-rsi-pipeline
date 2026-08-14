from __future__ import annotations

import json
from pathlib import Path

from post_training_rsi.__main__ import main
from post_training_rsi.config import PipelineConfig
from post_training_rsi.orchestration.coevolution import (
    build_reference_coevolution_controller,
)


def _config_mapping() -> dict[str, object]:
    return {
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
            "max_cycles": 1,
            "max_outer_iterations": 3,
            "plateau_patience": 1,
            "target_traces": 4,
            "harness_min_improvement": 0.005,
            "model_min_improvement": 0.005,
        },
        "approval": {
            "dataset_review_required": True,
            "checkpoint_review_required": True,
            "harness_review_required": True,
            "sample_rate": 1.0,
            "min_sample_items": 1,
            "max_sample_items": 8,
            "decision_ttl_seconds": 86400,
            "allowed_reviewer_roles": ["release-manager"],
        },
    }


def _config_file(tmp_path: Path) -> Path:
    path = tmp_path / "approval-config.json"
    path.write_text(
        json.dumps(_config_mapping(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _pending_request(
    *,
    config_path: Path,
    workspace: Path,
    capsys,
) -> dict[str, object]:
    exit_code = main(
        [
            "--config",
            str(config_path),
            "--workspace",
            str(workspace),
            "approvals",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    requests = payload["requests"]
    assert len(requests) == 1
    request = requests[0]
    assert request["state"] == "PENDING"
    return request


def _review(
    *,
    config_path: Path,
    workspace: Path,
    request: dict[str, object],
    approved: bool,
    capsys,
) -> None:
    decision_flag = "--approve" if approved else "--deny"
    exit_code = main(
        [
            "--config",
            str(config_path),
            "--workspace",
            str(workspace),
            "review",
            "--request-id",
            str(request["request_id"]),
            "--expected-request-sha256",
            str(request["request_sha256"]),
            decision_flag,
            "--reviewer",
            "reviewer-001",
            "--role",
            "release-manager",
            "--reason",
            "Deterministic fixture evidence reviewed.",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["decision"]["approved"] is approved


def test_coevolution_pauses_and_resumes_all_three_approval_boundaries(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "workspace"
    config_path = _config_file(tmp_path)
    config = PipelineConfig.load(config_path)

    def controller():
        return build_reference_coevolution_controller(
            config,
            workspace=workspace,
            run_id="coevolution-approval-run",
        )

    harness_pending = controller().run()
    assert harness_pending.status == "AWAITING_HARNESS_APPROVAL"
    assert harness_pending.state == "HARNESS_REVIEW_PENDING"
    harness_request = _pending_request(
        config_path=config_path,
        workspace=workspace,
        capsys=capsys,
    )
    assert harness_request["subject_type"] == "HARNESS"

    exact_replay = controller().run()
    assert exact_replay.pending_approval_request_id == harness_pending.pending_approval_request_id
    replay_request = _pending_request(
        config_path=config_path,
        workspace=workspace,
        capsys=capsys,
    )
    assert replay_request["request_id"] == harness_request["request_id"]

    _review(
        config_path=config_path,
        workspace=workspace,
        request=harness_request,
        approved=True,
        capsys=capsys,
    )
    dataset_pending = controller().run()
    assert dataset_pending.status == "AWAITING_DATASET_APPROVAL"
    assert dataset_pending.state == "DATA_REVIEW_PENDING"
    dataset_request = _pending_request(
        config_path=config_path,
        workspace=workspace,
        capsys=capsys,
    )
    assert dataset_request["subject_type"] == "DATASET"

    _review(
        config_path=config_path,
        workspace=workspace,
        request=dataset_request,
        approved=True,
        capsys=capsys,
    )
    model_pending = controller().run()
    assert model_pending.status == "AWAITING_MODEL_APPROVAL"
    assert model_pending.state == "MODEL_REVIEW_PENDING"
    model_request = _pending_request(
        config_path=config_path,
        workspace=workspace,
        capsys=capsys,
    )
    assert model_request["subject_type"] == "CHECKPOINT"

    _review(
        config_path=config_path,
        workspace=workspace,
        request=model_request,
        approved=True,
        capsys=capsys,
    )
    completed = controller().run()
    assert completed.status == "STOPPED"
    assert completed.state == "STOPPED"
    assert completed.completed_cycles == 1
    assert completed.active_checkpoint_id.startswith("model-candidate-")
    assert completed.pending_approval_request_id is None


def test_dataset_denial_stops_before_model_training(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "workspace"
    config_path = _config_file(tmp_path)
    config = PipelineConfig.load(config_path)

    def run():
        return build_reference_coevolution_controller(
            config,
            workspace=workspace,
            run_id="coevolution-denial-run",
        ).run()

    harness_pending = run()
    assert harness_pending.status == "AWAITING_HARNESS_APPROVAL"
    harness_request = _pending_request(
        config_path=config_path,
        workspace=workspace,
        capsys=capsys,
    )
    _review(
        config_path=config_path,
        workspace=workspace,
        request=harness_request,
        approved=True,
        capsys=capsys,
    )

    dataset_pending = run()
    assert dataset_pending.status == "AWAITING_DATASET_APPROVAL"
    dataset_request = _pending_request(
        config_path=config_path,
        workspace=workspace,
        capsys=capsys,
    )
    _review(
        config_path=config_path,
        workspace=workspace,
        request=dataset_request,
        approved=False,
        capsys=capsys,
    )

    denied = run()
    assert denied.status == "STOPPED"
    assert denied.state == "STOPPED"
    # A denial and a completed run share status and state, so only the terminal
    # reason keeps a refusal from reading as success.
    assert denied.stop_reason == "APPROVAL_NOT_GRANTED"
    assert denied.active_checkpoint_id.startswith("checkpoint-bootstrap-")
    assert not (workspace / "model-candidates").exists()
