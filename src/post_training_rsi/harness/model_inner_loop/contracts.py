from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ...control_plane import JSONValue
from ...control_plane.validation import (
    canonical_json,
    normalize_json_object,
    normalize_timestamp,
    validate_finite_number,
    validate_id,
    validate_id_tuple,
    validate_nonnegative_int,
    validate_nonnegative_number,
    validate_sha256,
    validate_text,
)

_FORBIDDEN_METADATA_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "token",
}


class ModelInnerContractError(ValueError):
    """Raised when model-inner-loop lineage, integrity, or result data is invalid."""


class ModelTrainingAlgorithm(StrEnum):
    SFT = "SFT"
    DPO = "DPO"


@dataclass(frozen=True, slots=True)
class ModelTrainingRequest:
    request_id: str
    run_id: str
    cycle: int
    model_id: str
    parent_checkpoint_id: str
    dataset_id: str
    dataset_path: str
    dataset_sha256: str
    accepted_example_count: int
    algorithm: ModelTrainingAlgorithm
    requested_at: str
    evidence_ids: tuple[str, ...]
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_id",
            validate_id(self.request_id, "request_id"),
        )
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.cycle, "cycle")
        object.__setattr__(self, "model_id", validate_id(self.model_id, "model_id"))
        object.__setattr__(
            self,
            "parent_checkpoint_id",
            validate_id(self.parent_checkpoint_id, "parent_checkpoint_id"),
        )
        object.__setattr__(
            self,
            "dataset_id",
            validate_id(self.dataset_id, "dataset_id"),
        )
        object.__setattr__(
            self,
            "dataset_path",
            validate_text(self.dataset_path, "dataset_path"),
        )
        validate_sha256(self.dataset_sha256)
        validate_nonnegative_int(
            self.accepted_example_count,
            "accepted_example_count",
        )
        if self.accepted_example_count < 1:
            raise ModelInnerContractError(
                "accepted_example_count must be positive"
            )
        if not isinstance(self.algorithm, ModelTrainingAlgorithm):
            object.__setattr__(
                self,
                "algorithm",
                ModelTrainingAlgorithm(self.algorithm),
            )
        object.__setattr__(
            self,
            "requested_at",
            normalize_timestamp(self.requested_at),
        )
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ModelInnerContractError(
                "model training request requires evidence_ids"
            )
        object.__setattr__(
            self,
            "metadata",
            _safe_metadata(self.metadata, "metadata"),
        )
        expected = _content_id("model-train-request", self._content_payload())
        if self.request_id != expected:
            raise ModelInnerContractError(
                f"request_id must be content-addressed as {expected}"
            )

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        cycle: int,
        model_id: str,
        parent_checkpoint_id: str,
        dataset_id: str,
        dataset_path: str,
        dataset_sha256: str,
        accepted_example_count: int,
        algorithm: ModelTrainingAlgorithm,
        requested_at: str,
        evidence_ids: tuple[str, ...],
        metadata: Mapping[str, object] | None = None,
    ) -> ModelTrainingRequest:
        safe_metadata = _safe_metadata(metadata or {}, "metadata")
        payload: dict[str, JSONValue] = {
            "run_id": run_id,
            "cycle": cycle,
            "model_id": model_id,
            "parent_checkpoint_id": parent_checkpoint_id,
            "dataset_id": dataset_id,
            "dataset_path": dataset_path,
            "dataset_sha256": dataset_sha256,
            "accepted_example_count": accepted_example_count,
            "algorithm": algorithm.value,
            "requested_at": normalize_timestamp(requested_at),
            "evidence_ids": list(evidence_ids),
            "metadata": safe_metadata,
        }
        return cls(
            request_id=_content_id("model-train-request", payload),
            run_id=run_id,
            cycle=cycle,
            model_id=model_id,
            parent_checkpoint_id=parent_checkpoint_id,
            dataset_id=dataset_id,
            dataset_path=dataset_path,
            dataset_sha256=dataset_sha256,
            accepted_example_count=accepted_example_count,
            algorithm=algorithm,
            requested_at=requested_at,
            evidence_ids=evidence_ids,
            metadata=safe_metadata,
        )

    def _content_payload(self) -> dict[str, JSONValue]:
        return {
            "run_id": self.run_id,
            "cycle": self.cycle,
            "model_id": self.model_id,
            "parent_checkpoint_id": self.parent_checkpoint_id,
            "dataset_id": self.dataset_id,
            "dataset_path": self.dataset_path,
            "dataset_sha256": self.dataset_sha256,
            "accepted_example_count": self.accepted_example_count,
            "algorithm": self.algorithm.value,
            "requested_at": self.requested_at,
            "evidence_ids": list(self.evidence_ids),
            "metadata": dict(self.metadata),
        }

    def to_dict(self) -> dict[str, JSONValue]:
        return {"request_id": self.request_id, **self._content_payload()}


@dataclass(frozen=True, slots=True)
class ModelCandidateArtifact:
    checkpoint_id: str
    request_id: str
    run_id: str
    cycle: int
    model_id: str
    parent_checkpoint_id: str
    dataset_id: str
    dataset_sha256: str
    artifact_path: str
    artifact_sha256: str
    training_loss: float
    training_cost_usd: float
    trained_at: str
    evidence_ids: tuple[str, ...]
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "checkpoint_id",
            "request_id",
            "run_id",
            "model_id",
            "parent_checkpoint_id",
            "dataset_id",
        ):
            object.__setattr__(self, name, validate_id(getattr(self, name), name))
        validate_nonnegative_int(self.cycle, "cycle")
        if self.checkpoint_id == self.parent_checkpoint_id:
            raise ModelInnerContractError(
                "Candidate checkpoint cannot be its own parent"
            )
        validate_sha256(self.dataset_sha256)
        object.__setattr__(
            self,
            "artifact_path",
            validate_text(self.artifact_path, "artifact_path"),
        )
        validate_sha256(self.artifact_sha256)
        validate_finite_number(self.training_loss, "training_loss")
        if self.training_loss < 0:
            raise ModelInnerContractError("training_loss must be non-negative")
        validate_nonnegative_number(
            self.training_cost_usd,
            "training_cost_usd",
        )
        object.__setattr__(self, "trained_at", normalize_timestamp(self.trained_at))
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ModelInnerContractError(
                "model Candidate requires evidence_ids"
            )
        object.__setattr__(
            self,
            "metadata",
            _safe_metadata(self.metadata, "metadata"),
        )
        expected = _content_id("model-candidate", self._content_payload())
        if self.checkpoint_id != expected:
            raise ModelInnerContractError(
                f"checkpoint_id must be content-addressed as {expected}"
            )

    @classmethod
    def create(
        cls,
        *,
        request: ModelTrainingRequest,
        artifact_path: str,
        artifact_sha256: str,
        training_loss: float,
        training_cost_usd: float,
        trained_at: str,
        evidence_ids: tuple[str, ...],
        metadata: Mapping[str, object] | None = None,
    ) -> ModelCandidateArtifact:
        safe_metadata = _safe_metadata(metadata or {}, "metadata")
        payload: dict[str, JSONValue] = {
            "request_id": request.request_id,
            "run_id": request.run_id,
            "cycle": request.cycle,
            "model_id": request.model_id,
            "parent_checkpoint_id": request.parent_checkpoint_id,
            "dataset_id": request.dataset_id,
            "dataset_sha256": request.dataset_sha256,
            "artifact_path": artifact_path,
            "artifact_sha256": artifact_sha256,
            "training_loss": training_loss,
            "training_cost_usd": training_cost_usd,
            "trained_at": normalize_timestamp(trained_at),
            "evidence_ids": list(evidence_ids),
            "metadata": safe_metadata,
        }
        return cls(
            checkpoint_id=_content_id("model-candidate", payload),
            request_id=request.request_id,
            run_id=request.run_id,
            cycle=request.cycle,
            model_id=request.model_id,
            parent_checkpoint_id=request.parent_checkpoint_id,
            dataset_id=request.dataset_id,
            dataset_sha256=request.dataset_sha256,
            artifact_path=artifact_path,
            artifact_sha256=artifact_sha256,
            training_loss=training_loss,
            training_cost_usd=training_cost_usd,
            trained_at=trained_at,
            evidence_ids=evidence_ids,
            metadata=safe_metadata,
        )

    def _content_payload(self) -> dict[str, JSONValue]:
        return {
            "request_id": self.request_id,
            "run_id": self.run_id,
            "cycle": self.cycle,
            "model_id": self.model_id,
            "parent_checkpoint_id": self.parent_checkpoint_id,
            "dataset_id": self.dataset_id,
            "dataset_sha256": self.dataset_sha256,
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "training_loss": self.training_loss,
            "training_cost_usd": self.training_cost_usd,
            "trained_at": self.trained_at,
            "evidence_ids": list(self.evidence_ids),
            "metadata": dict(self.metadata),
        }

    def to_dict(self) -> dict[str, JSONValue]:
        return {"checkpoint_id": self.checkpoint_id, **self._content_payload()}


@dataclass(frozen=True, slots=True)
class ModelServingLease:
    deployment_id: str
    checkpoint_id: str
    endpoint: str
    deployed_at: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "deployment_id",
            validate_id(self.deployment_id, "deployment_id"),
        )
        object.__setattr__(
            self,
            "checkpoint_id",
            validate_id(self.checkpoint_id, "checkpoint_id"),
        )
        object.__setattr__(self, "endpoint", validate_text(self.endpoint, "endpoint"))
        object.__setattr__(self, "deployed_at", normalize_timestamp(self.deployed_at))
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ModelInnerContractError("serving lease requires evidence_ids")


@dataclass(frozen=True, slots=True)
class ModelTeardownObservation:
    deployment_id: str
    checkpoint_id: str
    torn_down: bool
    completed_at: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "deployment_id",
            validate_id(self.deployment_id, "deployment_id"),
        )
        object.__setattr__(
            self,
            "checkpoint_id",
            validate_id(self.checkpoint_id, "checkpoint_id"),
        )
        if self.torn_down is not True:
            raise ModelInnerContractError(
                "successful teardown observation requires torn_down=true"
            )
        object.__setattr__(
            self,
            "completed_at",
            normalize_timestamp(self.completed_at),
        )
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ModelInnerContractError(
                "teardown observation requires evidence_ids"
            )


@dataclass(frozen=True, slots=True)
class ModelBenchmarkObservation:
    run_id: str
    cycle: int
    checkpoint_id: str
    parent_checkpoint_id: str
    endpoint: str
    benchmark_id: str
    score: float
    task_family_scores: dict[str, float]
    failure_trace_uris: tuple[str, ...]
    evaluation_cost_usd: float
    evaluated_at: str
    evidence_ids: tuple[str, ...]
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.cycle, "cycle")
        object.__setattr__(
            self,
            "checkpoint_id",
            validate_id(self.checkpoint_id, "checkpoint_id"),
        )
        object.__setattr__(
            self,
            "parent_checkpoint_id",
            validate_id(self.parent_checkpoint_id, "parent_checkpoint_id"),
        )
        object.__setattr__(self, "endpoint", validate_text(self.endpoint, "endpoint"))
        object.__setattr__(
            self,
            "benchmark_id",
            validate_id(self.benchmark_id, "benchmark_id"),
        )
        validate_finite_number(self.score, "score")
        if not 0.0 <= self.score <= 1.0:
            raise ModelInnerContractError("benchmark score must be in [0, 1]")
        normalized_scores: dict[str, float] = {}
        for family, score in self.task_family_scores.items():
            family_id = validate_id(family, "task_family_scores key")
            validate_finite_number(score, f"task_family_scores[{family_id}]")
            if not 0.0 <= score <= 1.0:
                raise ModelInnerContractError(
                    "task-family score must be in [0, 1]"
                )
            normalized_scores[family_id] = float(score)
        object.__setattr__(self, "task_family_scores", normalized_scores)
        normalized_uris = tuple(
            validate_text(uri, "failure_trace_uri")
            for uri in self.failure_trace_uris
        )
        object.__setattr__(self, "failure_trace_uris", normalized_uris)
        validate_nonnegative_number(
            self.evaluation_cost_usd,
            "evaluation_cost_usd",
        )
        object.__setattr__(
            self,
            "evaluated_at",
            normalize_timestamp(self.evaluated_at),
        )
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ModelInnerContractError(
                "model benchmark observation requires evidence_ids"
            )
        object.__setattr__(
            self,
            "metadata",
            _safe_metadata(self.metadata, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class ModelReviewObservation:
    request_id: str
    checkpoint_id: str
    approved: bool
    reviewer_id: str
    reviewer_role: str
    decided_at: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "request_id",
            "checkpoint_id",
            "reviewer_id",
            "reviewer_role",
        ):
            object.__setattr__(self, name, validate_id(getattr(self, name), name))
        if not isinstance(self.approved, bool):
            raise ModelInnerContractError("approved must be a boolean")
        object.__setattr__(self, "decided_at", normalize_timestamp(self.decided_at))
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ModelInnerContractError("model review requires evidence_ids")


@dataclass(frozen=True, slots=True)
class ModelPromotionCommitObservation:
    checkpoint_id: str
    previous_checkpoint_id: str
    score: float
    checkpoint_bundle_sha256: str
    committed_at: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "checkpoint_id",
            validate_id(self.checkpoint_id, "checkpoint_id"),
        )
        object.__setattr__(
            self,
            "previous_checkpoint_id",
            validate_id(self.previous_checkpoint_id, "previous_checkpoint_id"),
        )
        if self.checkpoint_id == self.previous_checkpoint_id:
            raise ModelInnerContractError(
                "promotion must replace a different previous Checkpoint"
            )
        validate_finite_number(self.score, "score")
        validate_sha256(self.checkpoint_bundle_sha256)
        object.__setattr__(
            self,
            "committed_at",
            normalize_timestamp(self.committed_at),
        )
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ModelInnerContractError(
                "promotion commit observation requires evidence_ids"
            )


@dataclass(frozen=True, slots=True)
class ModelRollbackCommitObservation:
    rejected_checkpoint_id: str
    active_checkpoint_id: str
    completed_at: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rejected_checkpoint_id",
            validate_id(self.rejected_checkpoint_id, "rejected_checkpoint_id"),
        )
        object.__setattr__(
            self,
            "active_checkpoint_id",
            validate_id(self.active_checkpoint_id, "active_checkpoint_id"),
        )
        if self.rejected_checkpoint_id == self.active_checkpoint_id:
            raise ModelInnerContractError(
                "rollback cannot keep the rejected Candidate active"
            )
        object.__setattr__(
            self,
            "completed_at",
            normalize_timestamp(self.completed_at),
        )
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ModelInnerContractError(
                "rollback commit observation requires evidence_ids"
            )


@dataclass(frozen=True, slots=True)
class ModelExecutionBundle:
    candidate: ModelCandidateArtifact
    serving: ModelServingLease
    evaluation: ModelBenchmarkObservation
    teardown: ModelTeardownObservation

    def __post_init__(self) -> None:
        checkpoint_ids = {
            self.candidate.checkpoint_id,
            self.serving.checkpoint_id,
            self.evaluation.checkpoint_id,
            self.teardown.checkpoint_id,
        }
        if len(checkpoint_ids) != 1:
            raise ModelInnerContractError(
                "training, serving, evaluation, and teardown Checkpoint IDs differ"
            )
        if self.serving.deployment_id != self.teardown.deployment_id:
            raise ModelInnerContractError(
                "serving and teardown deployment IDs differ"
            )
        if self.serving.endpoint != self.evaluation.endpoint:
            raise ModelInnerContractError(
                "evaluation did not use the exact deployed endpoint"
            )
        if _timestamp(self.teardown.completed_at) < _timestamp(
            self.serving.deployed_at
        ):
            raise ModelInnerContractError(
                "teardown cannot complete before deployment"
            )

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *self.candidate.evidence_ids,
                    *self.serving.evidence_ids,
                    *self.evaluation.evidence_ids,
                    *self.teardown.evidence_ids,
                )
            )
        )


def _content_id(prefix: str, payload: Mapping[str, JSONValue]) -> str:
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:24]}"


def _safe_metadata(value: Mapping[str, object], field_name: str) -> dict[str, JSONValue]:
    normalized = normalize_json_object(value, field_name)
    _reject_forbidden_metadata(normalized, path=field_name)
    return normalized


def _reject_forbidden_metadata(value: JSONValue, *, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = key.casefold().replace("-", "_").replace(" ", "_")
            if normalized_key in _FORBIDDEN_METADATA_KEYS:
                raise ModelInnerContractError(
                    f"model metadata must not contain secret field {path}.{key}"
                )
            _reject_forbidden_metadata(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden_metadata(item, path=f"{path}[{index}]")


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _exact_mapping(
    value: Mapping[str, object],
    expected: set[str],
    label: str,
) -> dict[str, object]:
    if any(not isinstance(key, str) for key in value):
        raise ModelInnerContractError(f"{label} keys must be strings")
    actual = set(value)
    if actual != expected:
        raise ModelInnerContractError(
            f"{label} fields mismatch: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )
    return dict(value)


def _required_str(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str):
        raise ModelInnerContractError(f"{key} must be a string")
    return item


def _required_int(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int):
        raise ModelInnerContractError(f"{key} must be an integer")
    return item


def _required_float(value: Mapping[str, object], key: str) -> float:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise ModelInnerContractError(f"{key} must be a number")
    number = float(item)
    if not math.isfinite(number):
        raise ModelInnerContractError(f"{key} must be finite")
    return number


def _required_str_tuple(value: Mapping[str, object], key: str) -> tuple[str, ...]:
    item = value[key]
    if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)):
        raise ModelInnerContractError(f"{key} must be an array of strings")
    result: list[str] = []
    for index, element in enumerate(item):
        if not isinstance(element, str):
            raise ModelInnerContractError(f"{key}[{index}] must be a string")
        result.append(element)
    return tuple(result)
