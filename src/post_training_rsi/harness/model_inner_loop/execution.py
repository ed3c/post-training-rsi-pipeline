from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ...adapter_runtime.integrity import sha256_path
from ...control_plane import EvidenceKind, EvidenceRecord
from .contracts import (
    ModelBenchmarkObservation,
    ModelCandidateArtifact,
    ModelExecutionBundle,
    ModelInnerContractError,
    ModelServingLease,
    ModelTeardownObservation,
    ModelTrainingRequest,
)


class ModelTrainer(Protocol):
    def train(self, request: ModelTrainingRequest) -> ModelCandidateArtifact: ...


class ModelDeployer(Protocol):
    def deploy(self, candidate: ModelCandidateArtifact) -> ModelServingLease: ...


class ModelEvaluator(Protocol):
    def evaluate(
        self,
        candidate: ModelCandidateArtifact,
        *,
        endpoint: str,
    ) -> ModelBenchmarkObservation: ...


class ModelTeardown(Protocol):
    def teardown(self, lease: ModelServingLease) -> ModelTeardownObservation: ...


@dataclass(frozen=True, slots=True)
class ModelExecutionEvidence:
    training: EvidenceRecord
    checkpoint: EvidenceRecord
    serving: EvidenceRecord
    evaluation: EvidenceRecord
    teardown: EvidenceRecord

    @property
    def records(self) -> tuple[EvidenceRecord, ...]:
        return (
            self.training,
            self.checkpoint,
            self.serving,
            self.evaluation,
            self.teardown,
        )


@dataclass(frozen=True, slots=True)
class ModelInnerExecutionResult:
    bundle: ModelExecutionBundle
    evidence: ModelExecutionEvidence


class ModelInnerExecutor:
    """Verify Dataset/artifact lineage and guarantee serving teardown."""

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        dataset_root: str | Path | None = None,
    ) -> None:
        self.artifact_root = _safe_root(artifact_root, "artifact_root")
        self.dataset_root = (
            _safe_root(dataset_root, "dataset_root")
            if dataset_root is not None
            else None
        )

    def run(
        self,
        request: ModelTrainingRequest,
        *,
        trainer: ModelTrainer,
        deployer: ModelDeployer,
        evaluator: ModelEvaluator,
        teardown: ModelTeardown,
    ) -> ModelInnerExecutionResult:
        dataset_path = _resolve_file(
            request.dataset_path,
            root=self.dataset_root,
            field_name="dataset_path",
        )
        actual_dataset_sha256 = _sha256_file(dataset_path)
        if actual_dataset_sha256 != request.dataset_sha256:
            raise ModelInnerContractError(
                "Trace Dataset bytes do not match the training request SHA-256"
            )

        candidate = trainer.train(request)
        self._validate_candidate(request, candidate)
        artifact_path = _resolve_artifact(
            candidate.artifact_path,
            root=self.artifact_root,
        )
        actual_artifact_sha256 = sha256_path(artifact_path)
        if actual_artifact_sha256 != candidate.artifact_sha256:
            raise ModelInnerContractError(
                "Candidate artifact bytes do not match the controller SHA-256"
            )

        lease: ModelServingLease | None = None
        evaluation: ModelBenchmarkObservation | None = None
        teardown_observation: ModelTeardownObservation | None = None
        primary_error: BaseException | None = None
        try:
            lease = deployer.deploy(candidate)
            if lease.checkpoint_id != candidate.checkpoint_id:
                raise ModelInnerContractError(
                    "serving lease targets a different Candidate Checkpoint"
                )
            evaluation = evaluator.evaluate(candidate, endpoint=lease.endpoint)
            self._validate_evaluation(candidate, lease, evaluation)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            if lease is not None:
                try:
                    teardown_observation = teardown.teardown(lease)
                    self._validate_teardown(lease, teardown_observation)
                except Exception as teardown_error:
                    if primary_error is None:
                        raise
                    if hasattr(primary_error, "add_note"):
                        primary_error.add_note(
                            "model serving teardown also failed: "
                            f"{type(teardown_error).__name__}: {teardown_error}"
                        )

        if evaluation is None or lease is None or teardown_observation is None:
            raise ModelInnerContractError(
                "model execution completed without evaluation or teardown evidence"
            )
        bundle = ModelExecutionBundle(
            candidate=candidate,
            serving=lease,
            evaluation=evaluation,
            teardown=teardown_observation,
        )
        return ModelInnerExecutionResult(
            bundle=bundle,
            evidence=_execution_evidence(bundle),
        )

    @staticmethod
    def _validate_candidate(
        request: ModelTrainingRequest,
        candidate: ModelCandidateArtifact,
    ) -> None:
        expected = {
            "request_id": request.request_id,
            "run_id": request.run_id,
            "cycle": request.cycle,
            "model_id": request.model_id,
            "parent_checkpoint_id": request.parent_checkpoint_id,
            "dataset_id": request.dataset_id,
            "dataset_sha256": request.dataset_sha256,
        }
        actual = {
            "request_id": candidate.request_id,
            "run_id": candidate.run_id,
            "cycle": candidate.cycle,
            "model_id": candidate.model_id,
            "parent_checkpoint_id": candidate.parent_checkpoint_id,
            "dataset_id": candidate.dataset_id,
            "dataset_sha256": candidate.dataset_sha256,
        }
        mismatches = sorted(key for key in expected if expected[key] != actual[key])
        if mismatches:
            raise ModelInnerContractError(
                f"Trainer result does not echo the training request: {mismatches}"
            )

    @staticmethod
    def _validate_evaluation(
        candidate: ModelCandidateArtifact,
        lease: ModelServingLease,
        evaluation: ModelBenchmarkObservation,
    ) -> None:
        if evaluation.run_id != candidate.run_id:
            raise ModelInnerContractError("evaluation Run ID mismatch")
        if evaluation.cycle != candidate.cycle:
            raise ModelInnerContractError("evaluation cycle mismatch")
        if evaluation.checkpoint_id != candidate.checkpoint_id:
            raise ModelInnerContractError("evaluation Candidate Checkpoint mismatch")
        if evaluation.parent_checkpoint_id != candidate.parent_checkpoint_id:
            raise ModelInnerContractError("evaluation parent Checkpoint mismatch")
        if evaluation.endpoint != lease.endpoint:
            raise ModelInnerContractError(
                "evaluation did not use the exact deployed endpoint"
            )

    @staticmethod
    def _validate_teardown(
        lease: ModelServingLease,
        observation: ModelTeardownObservation,
    ) -> None:
        if observation.deployment_id != lease.deployment_id:
            raise ModelInnerContractError("teardown deployment ID mismatch")
        if observation.checkpoint_id != lease.checkpoint_id:
            raise ModelInnerContractError("teardown Checkpoint ID mismatch")


def _execution_evidence(bundle: ModelExecutionBundle) -> ModelExecutionEvidence:
    candidate = bundle.candidate
    evaluation = bundle.evaluation
    serving = bundle.serving
    teardown = bundle.teardown
    return ModelExecutionEvidence(
        training=EvidenceRecord(
            evidence_id=_evidence_id("model-training", candidate.checkpoint_id),
            run_id=candidate.run_id,
            iteration=candidate.cycle,
            kind=EvidenceKind.TRAINING_RESULT,
            producer="model-inner-executor",
            uri=Path(candidate.artifact_path).resolve().as_uri(),
            created_at=candidate.trained_at,
            sha256=candidate.artifact_sha256,
            metadata={
                "request_id": candidate.request_id,
                "checkpoint_id": candidate.checkpoint_id,
                "parent_checkpoint_id": candidate.parent_checkpoint_id,
                "dataset_id": candidate.dataset_id,
                "dataset_sha256": candidate.dataset_sha256,
                "training_loss": candidate.training_loss,
                "training_cost_usd": candidate.training_cost_usd,
            },
        ),
        checkpoint=EvidenceRecord(
            evidence_id=_evidence_id("model-checkpoint", candidate.checkpoint_id),
            run_id=candidate.run_id,
            iteration=candidate.cycle,
            kind=EvidenceKind.CHECKPOINT,
            producer="model-inner-executor",
            uri=Path(candidate.artifact_path).resolve().as_uri(),
            created_at=candidate.trained_at,
            sha256=candidate.artifact_sha256,
            metadata={
                "checkpoint_id": candidate.checkpoint_id,
                "model_id": candidate.model_id,
                "parent_checkpoint_id": candidate.parent_checkpoint_id,
                "dataset_id": candidate.dataset_id,
            },
        ),
        serving=EvidenceRecord(
            evidence_id=_evidence_id("model-serving", serving.deployment_id),
            run_id=candidate.run_id,
            iteration=candidate.cycle,
            kind=EvidenceKind.SERVING_ENDPOINT,
            producer="model-inner-executor",
            uri=serving.endpoint,
            created_at=serving.deployed_at,
            metadata={
                "deployment_id": serving.deployment_id,
                "checkpoint_id": serving.checkpoint_id,
            },
        ),
        evaluation=EvidenceRecord(
            evidence_id=_evidence_id("model-evaluation", candidate.checkpoint_id),
            run_id=candidate.run_id,
            iteration=candidate.cycle,
            kind=EvidenceKind.EVALUATION_RESULT,
            producer="model-inner-executor",
            uri=f"artifact://model-evaluations/{candidate.checkpoint_id}.json",
            created_at=evaluation.evaluated_at,
            metadata={
                "checkpoint_id": evaluation.checkpoint_id,
                "parent_checkpoint_id": evaluation.parent_checkpoint_id,
                "benchmark_id": evaluation.benchmark_id,
                "score": evaluation.score,
                "task_family_scores": dict(evaluation.task_family_scores),
                "failure_trace_uris": list(evaluation.failure_trace_uris),
                "evaluation_cost_usd": evaluation.evaluation_cost_usd,
                "endpoint": evaluation.endpoint,
            },
        ),
        teardown=EvidenceRecord(
            evidence_id=_evidence_id("model-teardown", serving.deployment_id),
            run_id=candidate.run_id,
            iteration=candidate.cycle,
            kind=EvidenceKind.SERVING_TEARDOWN,
            producer="model-inner-executor",
            uri=f"artifact://model-deployments/{serving.deployment_id}/teardown.json",
            created_at=teardown.completed_at,
            metadata={
                "deployment_id": teardown.deployment_id,
                "checkpoint_id": teardown.checkpoint_id,
                "torn_down": teardown.torn_down,
            },
        ),
    )


def _safe_root(value: str | Path, field_name: str) -> Path:
    path = Path(value)
    if path.exists() and path.is_symlink():
        raise ModelInnerContractError(f"{field_name} must not be a symlink")
    resolved = path.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _resolve_file(
    value: str | Path,
    *,
    root: Path | None,
    field_name: str,
) -> Path:
    candidate = Path(value)
    if candidate.is_symlink():
        raise ModelInnerContractError(f"{field_name} must not be a symlink")
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise ModelInnerContractError(f"{field_name} must be an existing file")
    if root is not None:
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ModelInnerContractError(
                f"{field_name} escapes its configured root"
            ) from exc
    return resolved


def _resolve_artifact(value: str | Path, *, root: Path) -> Path:
    candidate = Path(value)
    if candidate.is_symlink():
        raise ModelInnerContractError("Candidate artifact must not be a symlink")
    resolved = candidate.resolve()
    if not resolved.exists():
        raise ModelInnerContractError("Candidate artifact does not exist")
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ModelInnerContractError(
            "Candidate artifact escapes the configured artifact root"
        ) from exc
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence_id(prefix: str, subject_id: str) -> str:
    digest = hashlib.sha256(f"{prefix}|{subject_id}".encode("utf-8")).hexdigest()
    return f"ev-{prefix}-{digest[:20]}"
