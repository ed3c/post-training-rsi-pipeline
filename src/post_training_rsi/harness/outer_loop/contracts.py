from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

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
    validate_text,
)


class HarnessContractError(ValueError):
    """Raised when a Harness outer-loop value violates its exact contract."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 2
    initial_backoff_seconds: float = 0.25
    max_backoff_seconds: float = 2.0
    jitter_ratio: float = 0.0

    def __post_init__(self) -> None:
        validate_nonnegative_int(self.max_attempts, "max_attempts")
        if self.max_attempts < 1:
            raise HarnessContractError("max_attempts must be positive")
        validate_nonnegative_number(
            self.initial_backoff_seconds,
            "initial_backoff_seconds",
        )
        validate_nonnegative_number(
            self.max_backoff_seconds,
            "max_backoff_seconds",
        )
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise HarnessContractError(
                "max_backoff_seconds cannot be smaller than initial_backoff_seconds"
            )
        validate_nonnegative_number(self.jitter_ratio, "jitter_ratio")
        if self.jitter_ratio > 1.0:
            raise HarnessContractError("jitter_ratio must be in [0, 1]")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "max_attempts": self.max_attempts,
            "initial_backoff_seconds": self.initial_backoff_seconds,
            "max_backoff_seconds": self.max_backoff_seconds,
            "jitter_ratio": self.jitter_ratio,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RetryPolicy:
        data = _exact_mapping(
            value,
            {
                "max_attempts",
                "initial_backoff_seconds",
                "max_backoff_seconds",
                "jitter_ratio",
            },
            "retry_policy",
        )
        return cls(
            max_attempts=_required_int(data, "max_attempts"),
            initial_backoff_seconds=_required_float(
                data,
                "initial_backoff_seconds",
            ),
            max_backoff_seconds=_required_float(data, "max_backoff_seconds"),
            jitter_ratio=_required_float(data, "jitter_ratio"),
        )


@dataclass(frozen=True, slots=True)
class HarnessSpec:
    harness_id: str
    version: int
    parent_harness_id: str | None
    system_prompt: str
    tools: tuple[str, ...]
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_seconds: float = 60.0
    max_steps: int = 32
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "harness_id",
            validate_id(self.harness_id, "harness_id"),
        )
        validate_nonnegative_int(self.version, "version")
        if self.version < 1:
            raise HarnessContractError("version must be positive")
        if self.parent_harness_id is not None:
            object.__setattr__(
                self,
                "parent_harness_id",
                validate_id(self.parent_harness_id, "parent_harness_id"),
            )
        if self.parent_harness_id == self.harness_id:
            raise HarnessContractError("Harness cannot be its own parent")
        object.__setattr__(
            self,
            "system_prompt",
            validate_text(self.system_prompt, "system_prompt"),
        )
        normalized_tools: list[str] = []
        for index, tool in enumerate(self.tools):
            normalized_tools.append(validate_id(tool, f"tools[{index}]"))
        if len(normalized_tools) != len(set(normalized_tools)):
            raise HarnessContractError("tools must be unique")
        object.__setattr__(self, "tools", tuple(normalized_tools))
        validate_finite_number(self.timeout_seconds, "timeout_seconds")
        if self.timeout_seconds <= 0:
            raise HarnessContractError("timeout_seconds must be positive")
        validate_nonnegative_int(self.max_steps, "max_steps")
        if self.max_steps < 1:
            raise HarnessContractError("max_steps must be positive")
        object.__setattr__(
            self,
            "metadata",
            normalize_json_object(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "harness_id": self.harness_id,
            "version": self.version,
            "parent_harness_id": self.parent_harness_id,
            "system_prompt": self.system_prompt,
            "tools": list(self.tools),
            "retry_policy": self.retry_policy.to_dict(),
            "timeout_seconds": self.timeout_seconds,
            "max_steps": self.max_steps,
            "metadata": dict(self.metadata),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> HarnessSpec:
        data = _exact_mapping(
            value,
            {
                "harness_id",
                "version",
                "parent_harness_id",
                "system_prompt",
                "tools",
                "retry_policy",
                "timeout_seconds",
                "max_steps",
                "metadata",
            },
            "harness",
        )
        retry = data["retry_policy"]
        if not isinstance(retry, Mapping):
            raise HarnessContractError("retry_policy must be an object")
        metadata = data["metadata"]
        if not isinstance(metadata, Mapping):
            raise HarnessContractError("metadata must be an object")
        parent = data["parent_harness_id"]
        if parent is not None and not isinstance(parent, str):
            raise HarnessContractError("parent_harness_id must be a string or null")
        return cls(
            harness_id=_required_str(data, "harness_id"),
            version=_required_int(data, "version"),
            parent_harness_id=parent,
            system_prompt=_required_str(data, "system_prompt"),
            tools=_required_str_tuple(data, "tools"),
            retry_policy=RetryPolicy.from_dict(retry),
            timeout_seconds=_required_float(data, "timeout_seconds"),
            max_steps=_required_int(data, "max_steps"),
            metadata=normalize_json_object(metadata, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class HarnessMutationProposal:
    mutation_id: str
    parent_harness_id: str
    prompt_appendix: str = ""
    add_tools: tuple[str, ...] = ()
    remove_tools: tuple[str, ...] = ()
    max_attempts: int | None = None
    timeout_seconds: float | None = None
    max_steps: int | None = None
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "mutation_id",
            validate_id(self.mutation_id, "mutation_id"),
        )
        object.__setattr__(
            self,
            "parent_harness_id",
            validate_id(self.parent_harness_id, "parent_harness_id"),
        )
        if self.prompt_appendix:
            object.__setattr__(
                self,
                "prompt_appendix",
                validate_text(self.prompt_appendix, "prompt_appendix"),
            )
        object.__setattr__(
            self,
            "add_tools",
            _validated_id_tuple(self.add_tools, "add_tools"),
        )
        object.__setattr__(
            self,
            "remove_tools",
            _validated_id_tuple(self.remove_tools, "remove_tools"),
        )
        overlap = set(self.add_tools) & set(self.remove_tools)
        if overlap:
            raise HarnessContractError(
                f"tools cannot be added and removed together: {sorted(overlap)}"
            )
        if self.max_attempts is not None:
            validate_nonnegative_int(self.max_attempts, "max_attempts")
            if self.max_attempts < 1:
                raise HarnessContractError("max_attempts must be positive")
        if self.timeout_seconds is not None:
            validate_finite_number(self.timeout_seconds, "timeout_seconds")
            if self.timeout_seconds <= 0:
                raise HarnessContractError("timeout_seconds must be positive")
        if self.max_steps is not None:
            validate_nonnegative_int(self.max_steps, "max_steps")
            if self.max_steps < 1:
                raise HarnessContractError("max_steps must be positive")
        object.__setattr__(
            self,
            "metadata",
            normalize_json_object(self.metadata, "metadata"),
        )
        if not any(
            (
                self.prompt_appendix,
                self.add_tools,
                self.remove_tools,
                self.max_attempts is not None,
                self.timeout_seconds is not None,
                self.max_steps is not None,
            )
        ):
            raise HarnessContractError("mutation proposal must change at least one field")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "mutation_id": self.mutation_id,
            "parent_harness_id": self.parent_harness_id,
            "prompt_appendix": self.prompt_appendix,
            "add_tools": list(self.add_tools),
            "remove_tools": list(self.remove_tools),
            "max_attempts": self.max_attempts,
            "timeout_seconds": self.timeout_seconds,
            "max_steps": self.max_steps,
            "metadata": dict(self.metadata),
        }

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(
            canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class HarnessValidationResult:
    candidate_harness_id: str
    valid: bool
    reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    validated_at: str
    metrics: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_harness_id",
            validate_id(self.candidate_harness_id, "candidate_harness_id"),
        )
        if not isinstance(self.valid, bool):
            raise HarnessContractError("valid must be a boolean")
        normalized_reasons = _validated_id_tuple(self.reasons, "reasons")
        object.__setattr__(self, "reasons", normalized_reasons)
        if self.valid and self.reasons:
            raise HarnessContractError("valid result cannot contain rejection reasons")
        if not self.valid and not self.reasons:
            raise HarnessContractError("invalid result requires rejection reasons")
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise HarnessContractError("validation result requires evidence_ids")
        object.__setattr__(
            self,
            "validated_at",
            normalize_timestamp(self.validated_at),
        )
        object.__setattr__(
            self,
            "metrics",
            normalize_json_object(self.metrics, "metrics"),
        )


@dataclass(frozen=True, slots=True)
class HarnessTask:
    task_id: str
    task_family: str
    weight: float = 1.0
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", validate_id(self.task_id, "task_id"))
        object.__setattr__(
            self,
            "task_family",
            validate_id(self.task_family, "task_family"),
        )
        validate_finite_number(self.weight, "weight")
        if self.weight <= 0:
            raise HarnessContractError("task weight must be positive")
        object.__setattr__(
            self,
            "metadata",
            normalize_json_object(self.metadata, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class HarnessTaskResult:
    task_id: str
    task_family: str
    score: float
    success: bool
    failure_code: str | None = None
    observable_trace_uri: str | None = None
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", validate_id(self.task_id, "task_id"))
        object.__setattr__(
            self,
            "task_family",
            validate_id(self.task_family, "task_family"),
        )
        validate_finite_number(self.score, "score")
        if not 0.0 <= self.score <= 1.0:
            raise HarnessContractError("task score must be in [0, 1]")
        if not isinstance(self.success, bool):
            raise HarnessContractError("success must be a boolean")
        if self.failure_code is not None:
            object.__setattr__(
                self,
                "failure_code",
                validate_id(self.failure_code, "failure_code"),
            )
        if self.success and self.failure_code is not None:
            raise HarnessContractError("successful task result cannot have failure_code")
        if self.observable_trace_uri is not None:
            object.__setattr__(
                self,
                "observable_trace_uri",
                validate_text(self.observable_trace_uri, "observable_trace_uri"),
            )
        object.__setattr__(
            self,
            "metadata",
            normalize_json_object(self.metadata, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class HarnessBenchmarkResult:
    harness_id: str
    benchmark_id: str
    score: float
    task_family_scores: dict[str, float]
    task_results: tuple[HarnessTaskResult, ...]
    cost_usd: float
    evaluated_at: str
    evidence_ids: tuple[str, ...]
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "harness_id",
            validate_id(self.harness_id, "harness_id"),
        )
        object.__setattr__(
            self,
            "benchmark_id",
            validate_id(self.benchmark_id, "benchmark_id"),
        )
        validate_finite_number(self.score, "score")
        if not 0.0 <= self.score <= 1.0:
            raise HarnessContractError("benchmark score must be in [0, 1]")
        normalized_family_scores: dict[str, float] = {}
        for family, score in self.task_family_scores.items():
            family_id = validate_id(family, "task_family_scores key")
            validate_finite_number(score, f"task_family_scores[{family_id}]")
            if not 0.0 <= score <= 1.0:
                raise HarnessContractError("task-family score must be in [0, 1]")
            normalized_family_scores[family_id] = float(score)
        object.__setattr__(
            self,
            "task_family_scores",
            normalized_family_scores,
        )
        if not self.task_results:
            raise HarnessContractError("benchmark result requires task_results")
        task_ids = tuple(item.task_id for item in self.task_results)
        if len(task_ids) != len(set(task_ids)):
            raise HarnessContractError("task result IDs must be unique")
        validate_nonnegative_number(self.cost_usd, "cost_usd")
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
            raise HarnessContractError("benchmark result requires evidence_ids")
        object.__setattr__(
            self,
            "metadata",
            normalize_json_object(self.metadata, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class HarnessReviewObservation:
    request_id: str
    candidate_harness_id: str
    approved: bool
    reviewer_id: str
    reviewer_role: str
    evidence_ids: tuple[str, ...]
    decided_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_id",
            validate_id(self.request_id, "request_id"),
        )
        object.__setattr__(
            self,
            "candidate_harness_id",
            validate_id(self.candidate_harness_id, "candidate_harness_id"),
        )
        if not isinstance(self.approved, bool):
            raise HarnessContractError("approved must be a boolean")
        object.__setattr__(
            self,
            "reviewer_id",
            validate_id(self.reviewer_id, "reviewer_id"),
        )
        object.__setattr__(
            self,
            "reviewer_role",
            validate_id(self.reviewer_role, "reviewer_role"),
        )
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise HarnessContractError("review observation requires evidence_ids")
        object.__setattr__(self, "decided_at", normalize_timestamp(self.decided_at))


def _exact_mapping(
    value: Mapping[str, object],
    expected: set[str],
    label: str,
) -> dict[str, object]:
    if any(not isinstance(key, str) for key in value):
        raise HarnessContractError(f"{label} keys must be strings")
    actual = set(value)
    if actual != expected:
        raise HarnessContractError(
            f"{label} fields mismatch: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )
    return dict(value)


def _required_str(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str):
        raise HarnessContractError(f"{key} must be a string")
    return item


def _required_int(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int):
        raise HarnessContractError(f"{key} must be an integer")
    return item


def _required_float(value: Mapping[str, object], key: str) -> float:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise HarnessContractError(f"{key} must be a number")
    number = float(item)
    if not math.isfinite(number):
        raise HarnessContractError(f"{key} must be finite")
    return number


def _required_str_tuple(value: Mapping[str, object], key: str) -> tuple[str, ...]:
    item = value[key]
    if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)):
        raise HarnessContractError(f"{key} must be an array of strings")
    result: list[str] = []
    for index, element in enumerate(item):
        if not isinstance(element, str):
            raise HarnessContractError(f"{key}[{index}] must be a string")
        result.append(element)
    return tuple(result)


def _validated_id_tuple(value: Sequence[str], field_name: str) -> tuple[str, ...]:
    result = tuple(validate_id(item, field_name) for item in value)
    if len(result) != len(set(result)):
        raise HarnessContractError(f"{field_name} must be unique")
    return result
