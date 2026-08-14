from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from post_training_rsi.control_plane import (
    ControlState,
    DecisionAction,
    EvidenceKind,
    StateSnapshot,
    StopReason,
)
from post_training_rsi.harness.model_inner_loop import (
    ModelBenchmarkObservation,
    ModelCandidateArtifact,
    ModelInnerContractError,
    ModelInnerExecutor,
    ModelInnerLimits,
    ModelInnerPolicy,
    ModelInnerPolicyInvariantError,
    ModelPromotionCommitObservation,
    ModelReviewObservation,
    ModelRollbackCommitObservation,
    ModelServingLease,
    ModelTeardownObservation,
    ModelTrainingAlgorithm,
    ModelTrainingRequest,
)

NOW = "2026-08-14T08:00:00Z"
TRAINED_AT = "2026-08-14T08:01:00Z"
DEPLOYED_AT = "2026-08-14T08:02:00Z"
EVALUATED_AT = "2026-08-14T08:03:00Z"
TORN_DOWN_AT = "2026-08-14T08:04:00Z"
RUN_ID = "run-model-001"
OLD_CHECKPOINT = "checkpoint-model-accepted"
HARNESS_ID = "harness-active-001"
MODEL_ID = "student-model-001"
DATASET_ID = "trace-dataset-001"


def _dataset(tmp_path: Path, *, content: bytes | None = None) -> tuple[Path, str]:
    path = tmp_path / "trace-datasets" / DATASET_ID / "accepted.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = content or (
        b'{"example_id":"trace-example-001","prompt":"task",'
        b'"response":"observable result"}\n'
    )
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def _request(
    tmp_path: Path,
    *,
    parent_checkpoint_id: str = OLD_CHECKPOINT,
    dataset_id: str = DATASET_ID,
    dataset_sha256: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ModelTrainingRequest:
    dataset_path, actual_hash = _dataset(tmp_path)
    return ModelTrainingRequest.create(
        run_id=RUN_ID,
        cycle=1,
        model_id=MODEL_ID,
        parent_checkpoint_id=parent_checkpoint_id,
        dataset_id=dataset_id,
        dataset_path=dataset_path.as_posix(),
        dataset_sha256=dataset_sha256 or actual_hash,
        accepted_example_count=1,
        algorithm=ModelTrainingAlgorithm.SFT,
        requested_at=NOW,
        evidence_ids=("ev-trace-dataset-001", "ev-trace-audit-001"),
        metadata=metadata,
    )


def _candidate(
    request: ModelTrainingRequest,
    artifact_root: Path,
    *,
    artifact_bytes: bytes = b"deterministic-model-artifact",
    artifact_sha256: str | None = None,
    training_loss: float = 0.18,
    training_cost_usd: float = 0.5,
) -> ModelCandidateArtifact:
    artifact = artifact_root / request.request_id / "weights.bin"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(artifact_bytes)
    return ModelCandidateArtifact.create(
        request=request,
        artifact_path=artifact.as_posix(),
        artifact_sha256=(
            artifact_sha256
            if artifact_sha256 is not None
            else hashlib.sha256(artifact_bytes).hexdigest()
        ),
        training_loss=training_loss,
        training_cost_usd=training_cost_usd,
        trained_at=TRAINED_AT,
        evidence_ids=("ev-trainer-result-001", "ev-candidate-artifact-001"),
        metadata={"provider": "deterministic-fixture"},
    )


class _Trainer:
    def __init__(
        self,
        artifact_root: Path,
        *,
        request_override: ModelTrainingRequest | None = None,
        artifact_sha256: str | None = None,
        artifact_path: Path | None = None,
    ) -> None:
        self.artifact_root = artifact_root
        self.request_override = request_override
        self.artifact_sha256 = artifact_sha256
        self.artifact_path = artifact_path

    def train(self, request: ModelTrainingRequest) -> ModelCandidateArtifact:
        source = self.request_override or request
        if self.artifact_path is None:
            return _candidate(
                source,
                self.artifact_root,
                artifact_sha256=self.artifact_sha256,
            )
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_path.write_bytes(b"external-artifact")
        return ModelCandidateArtifact.create(
            request=source,
            artifact_path=self.artifact_path.as_posix(),
            artifact_sha256=(
                self.artifact_sha256
                or hashlib.sha256(b"external-artifact").hexdigest()
            ),
            training_loss=0.2,
            training_cost_usd=0.4,
            trained_at=TRAINED_AT,
            evidence_ids=("ev-trainer-result-escape",),
        )


class _Deployer:
    def __init__(self, *, checkpoint_override: str | None = None) -> None:
        self.checkpoint_override = checkpoint_override

    def deploy(self, candidate: ModelCandidateArtifact) -> ModelServingLease:
        return ModelServingLease(
            deployment_id="deployment-model-001",
            checkpoint_id=self.checkpoint_override or candidate.checkpoint_id,
            endpoint="memory://model-candidate-001",
            deployed_at=DEPLOYED_AT,
            evidence_ids=("ev-serving-deploy-001",),
        )


class _Evaluator:
    def __init__(
        self,
        *,
        score: float = 0.75,
        endpoint_override: str | None = None,
        checkpoint_override: str | None = None,
        error: BaseException | None = None,
        evaluation_cost_usd: float = 0.25,
    ) -> None:
        self.score = score
        self.endpoint_override = endpoint_override
        self.checkpoint_override = checkpoint_override
        self.error = error
        self.evaluation_cost_usd = evaluation_cost_usd
        self.received_endpoint: str | None = None

    def evaluate(
        self,
        candidate: ModelCandidateArtifact,
        *,
        endpoint: str,
    ) -> ModelBenchmarkObservation:
        self.received_endpoint = endpoint
        if self.error is not None:
            raise self.error
        return ModelBenchmarkObservation(
            run_id=candidate.run_id,
            cycle=candidate.cycle,
            checkpoint_id=self.checkpoint_override or candidate.checkpoint_id,
            parent_checkpoint_id=candidate.parent_checkpoint_id,
            endpoint=self.endpoint_override or endpoint,
            benchmark_id="model-benchmark-001",
            score=self.score,
            task_family_scores={
                "tool-use": self.score,
                "state-verification": max(0.0, self.score - 0.05),
            },
            failure_trace_uris=(
                "artifact://model-failures/task-001.json",
            ),
            evaluation_cost_usd=self.evaluation_cost_usd,
            evaluated_at=EVALUATED_AT,
            evidence_ids=("ev-model-evaluation-001",),
            metadata={"suite_version": "v1"},
        )


class _Teardown:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        deployment_override: str | None = None,
    ) -> None:
        self.error = error
        self.deployment_override = deployment_override
        self.calls = 0

    def teardown(self, lease: ModelServingLease) -> ModelTeardownObservation:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ModelTeardownObservation(
            deployment_id=self.deployment_override or lease.deployment_id,
            checkpoint_id=lease.checkpoint_id,
            torn_down=True,
            completed_at=TORN_DOWN_AT,
            evidence_ids=("ev-serving-teardown-001",),
        )


def _executor(tmp_path: Path) -> tuple[ModelInnerExecutor, Path, Path]:
    artifact_root = tmp_path / "model-artifacts"
    dataset_root = tmp_path / "trace-datasets"
    executor = ModelInnerExecutor(
        artifact_root=artifact_root,
        dataset_root=dataset_root,
    )
    return executor, artifact_root, dataset_root


def _train_state(
    request: ModelTrainingRequest,
    *,
    active_score: float = 0.60,
    total_cost_usd: float = 0.0,
    peak_checkpoint_id: str = OLD_CHECKPOINT,
    metadata: dict[str, object] | None = None,
) -> StateSnapshot:
    state_metadata: dict[str, object] = {
        "trace_dataset_id": request.dataset_id,
        "trace_dataset_sha256": request.dataset_sha256,
        "trace_dataset_path": request.dataset_path,
        "active_model_score": active_score,
        "verified_trace_count": request.accepted_example_count,
    }
    if metadata:
        state_metadata.update(metadata)
    return StateSnapshot(
        snapshot_id="snapshot-train-model-001",
        run_id=request.run_id,
        iteration=1,
        cycle=request.cycle,
        state=ControlState.TRAIN_MODEL,
        entered_at=NOW,
        active_checkpoint_id=OLD_CHECKPOINT,
        peak_checkpoint_id=peak_checkpoint_id,
        active_harness_id=HARNESS_ID,
        peak_score=0.72,
        plateau_count=2,
        total_cost_usd=total_cost_usd,
        evidence_ids=request.evidence_ids,
        metadata=state_metadata,
    )


def _policy(**overrides: object) -> ModelInnerPolicy:
    values: dict[str, object] = {
        "min_improvement": 0.01,
        "regression_tolerance": 0.05,
        "per_stage_budget_usd": 2.0,
        "total_budget_usd": 8.0,
        "approval_required": False,
    }
    values.update(overrides)
    return ModelInnerPolicy(ModelInnerLimits(**values))  # type: ignore[arg-type]


def _evaluation(
    candidate: ModelCandidateArtifact,
    *,
    score: float,
    evaluation_cost_usd: float = 0.25,
    checkpoint_id: str | None = None,
    parent_checkpoint_id: str | None = None,
) -> ModelBenchmarkObservation:
    return ModelBenchmarkObservation(
        run_id=candidate.run_id,
        cycle=candidate.cycle,
        checkpoint_id=checkpoint_id or candidate.checkpoint_id,
        parent_checkpoint_id=parent_checkpoint_id or candidate.parent_checkpoint_id,
        endpoint="memory://model-candidate-001",
        benchmark_id="model-benchmark-001",
        score=score,
        task_family_scores={"tool-use": score},
        failure_trace_uris=(),
        evaluation_cost_usd=evaluation_cost_usd,
        evaluated_at=EVALUATED_AT,
        evidence_ids=("ev-model-policy-evaluation",),
    )


def _evaluated_state(
    policy: ModelInnerPolicy,
    request: ModelTrainingRequest,
    candidate: ModelCandidateArtifact,
) -> StateSnapshot:
    step = policy.training_completed(_train_state(request), candidate)
    assert step.final_snapshot.state is ControlState.EVALUATE_MODEL
    return step.final_snapshot


def test_training_request_and_candidate_are_content_addressed(tmp_path: Path) -> None:
    request = _request(tmp_path)
    candidate = _candidate(request, tmp_path / "model-artifacts")

    assert request.request_id.startswith("model-train-request-")
    assert candidate.checkpoint_id.startswith("model-candidate-")
    assert candidate.request_id == request.request_id
    assert candidate.parent_checkpoint_id == request.parent_checkpoint_id
    assert candidate.dataset_sha256 == request.dataset_sha256

    same = ModelTrainingRequest.create(
        run_id=request.run_id,
        cycle=request.cycle,
        model_id=request.model_id,
        parent_checkpoint_id=request.parent_checkpoint_id,
        dataset_id=request.dataset_id,
        dataset_path=request.dataset_path,
        dataset_sha256=request.dataset_sha256,
        accepted_example_count=request.accepted_example_count,
        algorithm=request.algorithm,
        requested_at=request.requested_at,
        evidence_ids=request.evidence_ids,
        metadata=request.metadata,
    )
    assert same == request


def test_contract_rejects_secret_metadata(tmp_path: Path) -> None:
    with pytest.raises(ModelInnerContractError, match="secret field"):
        _request(tmp_path, metadata={"nested": {"api_key": "do-not-store"}})


def test_executor_verifies_dataset_artifact_endpoint_and_evidence(tmp_path: Path) -> None:
    request = _request(tmp_path)
    executor, artifact_root, _ = _executor(tmp_path)
    evaluator = _Evaluator(score=0.76)
    teardown = _Teardown()

    result = executor.run(
        request,
        trainer=_Trainer(artifact_root),
        deployer=_Deployer(),
        evaluator=evaluator,
        teardown=teardown,
    )

    assert evaluator.received_endpoint == "memory://model-candidate-001"
    assert teardown.calls == 1
    assert result.bundle.evaluation.endpoint == result.bundle.serving.endpoint
    assert result.bundle.teardown.torn_down
    assert {
        record.kind for record in result.evidence.records
    } == {
        EvidenceKind.TRAINING_RESULT,
        EvidenceKind.CHECKPOINT,
        EvidenceKind.SERVING_ENDPOINT,
        EvidenceKind.EVALUATION_RESULT,
        EvidenceKind.SERVING_TEARDOWN,
    }
    assert result.evidence.training.sha256 == result.bundle.candidate.artifact_sha256
    assert result.bundle.evidence_ids


def test_executor_rejects_dataset_hash_mismatch_before_training(tmp_path: Path) -> None:
    request = _request(tmp_path, dataset_sha256="0" * 64)
    executor, artifact_root, _ = _executor(tmp_path)

    with pytest.raises(ModelInnerContractError, match="Dataset bytes"):
        executor.run(
            request,
            trainer=_Trainer(artifact_root),
            deployer=_Deployer(),
            evaluator=_Evaluator(),
            teardown=_Teardown(),
        )


def test_executor_rejects_trainer_echo_substitution(tmp_path: Path) -> None:
    request = _request(tmp_path)
    different_request = _request(
        tmp_path,
        parent_checkpoint_id="checkpoint-parent-other",
    )
    executor, artifact_root, _ = _executor(tmp_path)

    with pytest.raises(ModelInnerContractError, match="echo"):
        executor.run(
            request,
            trainer=_Trainer(
                artifact_root,
                request_override=different_request,
            ),
            deployer=_Deployer(),
            evaluator=_Evaluator(),
            teardown=_Teardown(),
        )


def test_executor_rejects_artifact_hash_mismatch_and_root_escape(tmp_path: Path) -> None:
    request = _request(tmp_path)
    executor, artifact_root, _ = _executor(tmp_path)

    with pytest.raises(ModelInnerContractError, match="artifact bytes"):
        executor.run(
            request,
            trainer=_Trainer(artifact_root, artifact_sha256="0" * 64),
            deployer=_Deployer(),
            evaluator=_Evaluator(),
            teardown=_Teardown(),
        )

    outside = tmp_path / "outside" / "weights.bin"
    with pytest.raises(ModelInnerContractError, match="escapes"):
        executor.run(
            request,
            trainer=_Trainer(artifact_root, artifact_path=outside),
            deployer=_Deployer(),
            evaluator=_Evaluator(),
            teardown=_Teardown(),
        )


def test_executor_rejects_serving_and_evaluation_substitution(tmp_path: Path) -> None:
    request = _request(tmp_path)
    executor, artifact_root, _ = _executor(tmp_path)

    with pytest.raises(ModelInnerContractError, match="serving lease"):
        executor.run(
            request,
            trainer=_Trainer(artifact_root),
            deployer=_Deployer(checkpoint_override="checkpoint-substituted"),
            evaluator=_Evaluator(),
            teardown=_Teardown(),
        )

    teardown = _Teardown()
    with pytest.raises(ModelInnerContractError, match="exact deployed endpoint"):
        executor.run(
            request,
            trainer=_Trainer(artifact_root),
            deployer=_Deployer(),
            evaluator=_Evaluator(endpoint_override="memory://wrong-endpoint"),
            teardown=teardown,
        )
    assert teardown.calls == 1


def test_executor_tears_down_after_evaluation_failure(tmp_path: Path) -> None:
    request = _request(tmp_path)
    executor, artifact_root, _ = _executor(tmp_path)
    teardown = _Teardown()

    with pytest.raises(RuntimeError, match="evaluation failed"):
        executor.run(
            request,
            trainer=_Trainer(artifact_root),
            deployer=_Deployer(),
            evaluator=_Evaluator(error=RuntimeError("evaluation failed")),
            teardown=teardown,
        )
    assert teardown.calls == 1


def test_executor_preserves_primary_error_when_teardown_also_fails(tmp_path: Path) -> None:
    request = _request(tmp_path)
    executor, artifact_root, _ = _executor(tmp_path)
    teardown = _Teardown(error=RuntimeError("teardown failed"))

    with pytest.raises(RuntimeError, match="evaluation failed") as captured:
        executor.run(
            request,
            trainer=_Trainer(artifact_root),
            deployer=_Deployer(),
            evaluator=_Evaluator(error=RuntimeError("evaluation failed")),
            teardown=teardown,
        )
    assert teardown.calls == 1
    assert any(
        "teardown also failed" in note
        for note in getattr(captured.value, "__notes__", ())
    )


def test_executor_propagates_teardown_only_failure(tmp_path: Path) -> None:
    request = _request(tmp_path)
    executor, artifact_root, _ = _executor(tmp_path)

    with pytest.raises(RuntimeError, match="teardown failed"):
        executor.run(
            request,
            trainer=_Trainer(artifact_root),
            deployer=_Deployer(),
            evaluator=_Evaluator(),
            teardown=_Teardown(error=RuntimeError("teardown failed")),
        )


def test_training_policy_enters_evaluate_model_and_preserves_parent(tmp_path: Path) -> None:
    request = _request(tmp_path)
    candidate = _candidate(request, tmp_path / "model-artifacts")
    policy = _policy()

    step = policy.training_completed(_train_state(request), candidate)

    assert step.final_snapshot.state is ControlState.EVALUATE_MODEL
    assert step.final_snapshot.active_checkpoint_id == OLD_CHECKPOINT
    assert step.final_snapshot.peak_checkpoint_id == OLD_CHECKPOINT
    assert step.final_snapshot.candidate_checkpoint_id == candidate.checkpoint_id
    assert step.final_snapshot.metadata["trace_dataset_id"] == request.dataset_id
    assert step.final_snapshot.total_cost_usd == pytest.approx(0.5)


def test_strict_model_improvement_emits_promotion_then_slim_handoff(tmp_path: Path) -> None:
    request = _request(tmp_path)
    candidate = _candidate(request, tmp_path / "model-artifacts")
    policy = _policy(min_improvement=0.01)
    evaluated_state = _evaluated_state(policy, request, candidate)

    promote = policy.evaluation_completed(
        evaluated_state,
        _evaluation(candidate, score=0.70),
    )

    assert promote.final_snapshot.state is ControlState.PROMOTE_MODEL
    assert promote.decisions[-1].action is DecisionAction.PROMOTE
    assert promote.final_snapshot.active_checkpoint_id == OLD_CHECKPOINT
    assert promote.final_snapshot.peak_checkpoint_id == OLD_CHECKPOINT
    assert promote.final_snapshot.candidate_checkpoint_id == candidate.checkpoint_id

    committed = policy.promotion_committed(
        promote.final_snapshot,
        ModelPromotionCommitObservation(
            checkpoint_id=candidate.checkpoint_id,
            previous_checkpoint_id=OLD_CHECKPOINT,
            score=0.70,
            checkpoint_bundle_sha256="b" * 64,
            committed_at=TORN_DOWN_AT,
            evidence_ids=("ev-model-promotion-commit",),
        ),
    )

    assert committed.final_snapshot.state is ControlState.SLIM_HARNESS
    assert committed.slim_harness_handoff
    assert committed.final_snapshot.active_checkpoint_id == candidate.checkpoint_id
    assert committed.final_snapshot.peak_checkpoint_id == candidate.checkpoint_id
    assert committed.final_snapshot.candidate_checkpoint_id is None
    assert committed.final_snapshot.metadata["active_model_score"] == pytest.approx(0.70)


def test_threshold_equality_rolls_back_and_keeps_accepted_model(tmp_path: Path) -> None:
    request = _request(tmp_path)
    candidate = _candidate(request, tmp_path / "model-artifacts")
    policy = _policy(min_improvement=0.10)
    evaluated_state = _evaluated_state(policy, request, candidate)

    rollback = policy.evaluation_completed(
        evaluated_state,
        _evaluation(candidate, score=0.70),
    )

    assert rollback.final_snapshot.state is ControlState.ROLLBACK_MODEL
    assert rollback.decisions[-1].action is DecisionAction.ROLLBACK
    assert rollback.decisions[-1].stop_reason is None
    assert rollback.final_snapshot.active_checkpoint_id == OLD_CHECKPOINT
    assert rollback.final_snapshot.peak_checkpoint_id == OLD_CHECKPOINT


def test_regression_emits_explicit_rollback_reason(tmp_path: Path) -> None:
    request = _request(tmp_path)
    candidate = _candidate(request, tmp_path / "model-artifacts")
    policy = _policy(regression_tolerance=0.05)
    evaluated_state = _evaluated_state(policy, request, candidate)

    rollback = policy.evaluation_completed(
        evaluated_state,
        _evaluation(candidate, score=0.40),
    )

    assert rollback.final_snapshot.state is ControlState.ROLLBACK_MODEL
    assert rollback.decisions[-1].stop_reason is StopReason.REGRESSION_ROLLBACK
    assert rollback.decisions[-1].reason_code == "model_regressed_beyond_tolerance"
    assert rollback.final_snapshot.stop_reason is None


def test_rollback_commit_hands_next_cycle_to_freeze_model(tmp_path: Path) -> None:
    request = _request(tmp_path)
    candidate = _candidate(request, tmp_path / "model-artifacts")
    policy = _policy()
    evaluated_state = _evaluated_state(policy, request, candidate)
    rollback = policy.evaluation_completed(
        evaluated_state,
        _evaluation(candidate, score=0.60),
    )

    committed = policy.rollback_committed(
        rollback.final_snapshot,
        ModelRollbackCommitObservation(
            rejected_checkpoint_id=candidate.checkpoint_id,
            active_checkpoint_id=OLD_CHECKPOINT,
            completed_at=TORN_DOWN_AT,
            evidence_ids=("ev-model-rollback-commit",),
        ),
    )

    assert committed.final_snapshot.state is ControlState.FREEZE_MODEL
    assert committed.next_cycle_handoff
    assert committed.final_snapshot.cycle == 2
    assert committed.final_snapshot.iteration == 0
    assert committed.final_snapshot.active_checkpoint_id == OLD_CHECKPOINT
    assert committed.final_snapshot.peak_checkpoint_id == OLD_CHECKPOINT
    assert committed.final_snapshot.candidate_checkpoint_id is None


def test_approval_required_enters_pending_and_approved_candidate_promotes(tmp_path: Path) -> None:
    request = _request(tmp_path)
    candidate = _candidate(request, tmp_path / "model-artifacts")
    policy = _policy(approval_required=True)
    evaluated_state = _evaluated_state(policy, request, candidate)
    pending = policy.evaluation_completed(
        evaluated_state,
        _evaluation(candidate, score=0.75),
    )

    assert pending.final_snapshot.state is ControlState.MODEL_REVIEW_PENDING
    assert pending.decisions[-1].action is DecisionAction.REQUEST_APPROVAL
    assert pending.final_snapshot.active_checkpoint_id == OLD_CHECKPOINT

    approved = policy.review_completed(
        pending.final_snapshot,
        ModelReviewObservation(
            request_id="approval-model-001",
            checkpoint_id=candidate.checkpoint_id,
            approved=True,
            reviewer_id="reviewer-001",
            reviewer_role="release-manager",
            decided_at=TORN_DOWN_AT,
            evidence_ids=("ev-model-approval-001",),
        ),
    )

    assert approved.final_snapshot.state is ControlState.PROMOTE_MODEL
    assert approved.decisions[-1].action is DecisionAction.PROMOTE


def test_denied_model_approval_rolls_back_and_candidate_substitution_fails(tmp_path: Path) -> None:
    request = _request(tmp_path)
    candidate = _candidate(request, tmp_path / "model-artifacts")
    policy = _policy(approval_required=True)
    pending = policy.evaluation_completed(
        _evaluated_state(policy, request, candidate),
        _evaluation(candidate, score=0.75),
    )

    denied = policy.review_completed(
        pending.final_snapshot,
        ModelReviewObservation(
            request_id="approval-model-denied",
            checkpoint_id=candidate.checkpoint_id,
            approved=False,
            reviewer_id="reviewer-002",
            reviewer_role="release-manager",
            decided_at=TORN_DOWN_AT,
            evidence_ids=("ev-model-denial-001",),
        ),
    )
    assert denied.final_snapshot.state is ControlState.ROLLBACK_MODEL
    assert denied.decisions[-1].stop_reason is StopReason.APPROVAL_NOT_GRANTED
    assert denied.final_snapshot.active_checkpoint_id == OLD_CHECKPOINT

    with pytest.raises(ModelInnerPolicyInvariantError, match="active Candidate"):
        policy.review_completed(
            pending.final_snapshot,
            ModelReviewObservation(
                request_id="approval-model-substitution",
                checkpoint_id="checkpoint-substituted",
                approved=True,
                reviewer_id="reviewer-002",
                reviewer_role="release-manager",
                decided_at=TORN_DOWN_AT,
                evidence_ids=("ev-model-substitution",),
            ),
        )


def test_exact_stage_budget_is_allowed_and_crossing_aborts(tmp_path: Path) -> None:
    request = _request(tmp_path)
    exact_candidate = _candidate(
        request,
        tmp_path / "model-artifacts-exact",
        training_cost_usd=1.0,
    )
    policy = _policy(per_stage_budget_usd=1.0, total_budget_usd=2.0)
    exact = policy.training_completed(_train_state(request), exact_candidate)
    assert exact.final_snapshot.state is ControlState.EVALUATE_MODEL

    crossed_candidate = _candidate(
        request,
        tmp_path / "model-artifacts-crossed",
        training_cost_usd=1.01,
    )
    crossed = policy.training_completed(_train_state(request), crossed_candidate)
    assert crossed.final_snapshot.state is ControlState.ABORTED
    assert crossed.final_snapshot.stop_reason is StopReason.PER_ITERATION_BUDGET_EXCEEDED


def test_total_budget_crossing_during_evaluation_aborts(tmp_path: Path) -> None:
    request = _request(tmp_path)
    candidate = _candidate(
        request,
        tmp_path / "model-artifacts",
        training_cost_usd=0.4,
    )
    policy = _policy(per_stage_budget_usd=1.0, total_budget_usd=1.0)
    state = policy.training_completed(
        _train_state(request, total_cost_usd=0.2),
        candidate,
    ).final_snapshot
    step = policy.evaluation_completed(
        state,
        _evaluation(candidate, score=0.75, evaluation_cost_usd=0.5),
    )

    assert step.final_snapshot.state is ControlState.ABORTED
    assert step.final_snapshot.stop_reason is StopReason.TOTAL_BUDGET_EXCEEDED
    assert step.final_snapshot.active_checkpoint_id == OLD_CHECKPOINT


def test_policy_rejects_active_parent_dataset_and_evaluation_substitution(tmp_path: Path) -> None:
    request = _request(tmp_path)
    candidate = _candidate(request, tmp_path / "model-artifacts")
    policy = _policy()

    with pytest.raises(ModelInnerPolicyInvariantError, match="accepted Peak"):
        policy.training_completed(
            _train_state(request, peak_checkpoint_id="checkpoint-other"),
            candidate,
        )

    wrong_parent_request = _request(
        tmp_path,
        parent_checkpoint_id="checkpoint-other",
    )
    wrong_parent_candidate = _candidate(
        wrong_parent_request,
        tmp_path / "model-artifacts-parent",
    )
    with pytest.raises(ModelInnerPolicyInvariantError, match="parent"):
        policy.training_completed(_train_state(request), wrong_parent_candidate)

    wrong_dataset_request = _request(
        tmp_path,
        dataset_id="trace-dataset-other",
    )
    wrong_dataset_candidate = _candidate(
        wrong_dataset_request,
        tmp_path / "model-artifacts-dataset",
    )
    with pytest.raises(ModelInnerPolicyInvariantError, match="Dataset ID"):
        policy.training_completed(_train_state(request), wrong_dataset_candidate)

    state = _evaluated_state(policy, request, candidate)
    with pytest.raises(ModelInnerPolicyInvariantError, match="active Candidate"):
        policy.evaluation_completed(
            state,
            _evaluation(
                candidate,
                score=0.75,
                checkpoint_id="checkpoint-substituted",
            ),
        )


def test_policy_requires_finite_active_model_score(tmp_path: Path) -> None:
    request = _request(tmp_path)
    candidate = _candidate(request, tmp_path / "model-artifacts")
    policy = _policy()
    current = _train_state(request, metadata={"active_model_score": "unknown"})

    with pytest.raises(ModelInnerPolicyInvariantError, match="must be a number"):
        policy.training_completed(current, candidate)


def test_promotion_and_rollback_commit_identity_fail_closed(tmp_path: Path) -> None:
    request = _request(tmp_path)
    candidate = _candidate(request, tmp_path / "model-artifacts")
    policy = _policy()
    promote = policy.evaluation_completed(
        _evaluated_state(policy, request, candidate),
        _evaluation(candidate, score=0.75),
    )

    with pytest.raises(ModelInnerPolicyInvariantError, match="different Candidate"):
        policy.promotion_committed(
            promote.final_snapshot,
            ModelPromotionCommitObservation(
                checkpoint_id="checkpoint-other",
                previous_checkpoint_id=OLD_CHECKPOINT,
                score=0.75,
                checkpoint_bundle_sha256="b" * 64,
                committed_at=TORN_DOWN_AT,
                evidence_ids=("ev-promotion-other",),
            ),
        )
    with pytest.raises(ModelInnerPolicyInvariantError, match="score"):
        policy.promotion_committed(
            promote.final_snapshot,
            ModelPromotionCommitObservation(
                checkpoint_id=candidate.checkpoint_id,
                previous_checkpoint_id=OLD_CHECKPOINT,
                score=0.74,
                checkpoint_bundle_sha256="b" * 64,
                committed_at=TORN_DOWN_AT,
                evidence_ids=("ev-promotion-score",),
            ),
        )

    rollback = policy.evaluation_completed(
        _evaluated_state(policy, request, candidate),
        _evaluation(candidate, score=0.50),
    )
    with pytest.raises(ModelInnerPolicyInvariantError, match="different Candidate"):
        policy.rollback_committed(
            rollback.final_snapshot,
            ModelRollbackCommitObservation(
                rejected_checkpoint_id="checkpoint-other",
                active_checkpoint_id=OLD_CHECKPOINT,
                completed_at=TORN_DOWN_AT,
                evidence_ids=("ev-rollback-other",),
            ),
        )


def test_policy_records_are_deterministic_and_paired(tmp_path: Path) -> None:
    request = _request(tmp_path)
    candidate = _candidate(request, tmp_path / "model-artifacts")
    policy = _policy()
    current = _train_state(request)

    first = policy.training_completed(current, candidate)
    replay = policy.training_completed(current, candidate)

    assert replay == first
    for decision, transition, snapshot in zip(
        first.decisions,
        first.transitions,
        first.snapshots,
        strict=True,
    ):
        assert transition.decision_id == decision.decision_id
        assert snapshot.metadata["decision_id"] == decision.decision_id
        assert transition.evidence_ids
        assert snapshot.evidence_ids
