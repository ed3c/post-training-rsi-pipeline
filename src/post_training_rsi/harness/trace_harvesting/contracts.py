from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

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
    "analysis",
    "chain_of_thought",
    "cot",
    "hidden_reasoning",
    "hidden_state",
    "internal_reasoning",
    "private_reasoning",
    "reasoning",
    "scratchpad",
    "thought",
    "thoughts",
}


class TraceContractError(ValueError):
    """Raised when an observable trace or Dataset value violates its contract."""


class TraceEventType(StrEnum):
    """Observable trajectory events; hidden internal reasoning is intentionally absent."""

    TASK_INPUT = "TASK_INPUT"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    STATE_OBSERVATION = "STATE_OBSERVATION"
    FINAL_OUTPUT = "FINAL_OUTPUT"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class ObservableTraceStep:
    step_index: int
    event_type: TraceEventType
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    status: str | None = None
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_nonnegative_int(self.step_index, "step_index")
        if not isinstance(self.event_type, TraceEventType):
            object.__setattr__(self, "event_type", TraceEventType(self.event_type))
        object.__setattr__(self, "content", validate_text(self.content, "content"))
        if self.tool_name is not None:
            object.__setattr__(
                self,
                "tool_name",
                validate_id(self.tool_name, "tool_name"),
            )
        if self.tool_call_id is not None:
            object.__setattr__(
                self,
                "tool_call_id",
                validate_id(self.tool_call_id, "tool_call_id"),
            )
        if self.status is not None:
            object.__setattr__(self, "status", validate_id(self.status, "status"))
        object.__setattr__(
            self,
            "metadata",
            _safe_metadata(self.metadata, "metadata"),
        )

        is_tool_event = self.event_type in {
            TraceEventType.TOOL_CALL,
            TraceEventType.TOOL_RESULT,
        }
        if is_tool_event and (self.tool_name is None or self.tool_call_id is None):
            raise TraceContractError(
                "TOOL_CALL and TOOL_RESULT require tool_name and tool_call_id"
            )
        if not is_tool_event and (
            self.tool_name is not None or self.tool_call_id is not None
        ):
            raise TraceContractError(
                "non-tool observable events must not declare tool identity"
            )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "step_index": self.step_index,
            "event_type": self.event_type.value,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "status": self.status,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ObservableTraceStep:
        data = _exact_mapping(
            value,
            {
                "step_index",
                "event_type",
                "content",
                "tool_name",
                "tool_call_id",
                "status",
                "metadata",
            },
            "trace_step",
        )
        metadata = data["metadata"]
        if not isinstance(metadata, Mapping):
            raise TraceContractError("metadata must be an object")
        return cls(
            step_index=_required_int(data, "step_index"),
            event_type=TraceEventType(_required_str(data, "event_type")),
            content=_required_str(data, "content"),
            tool_name=_optional_str(data, "tool_name"),
            tool_call_id=_optional_str(data, "tool_call_id"),
            status=_optional_str(data, "status"),
            metadata=_safe_metadata(metadata, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class ObservableTrajectory:
    trace_id: str
    run_id: str
    cycle: int
    task_id: str
    task_family: str
    model_checkpoint_id: str
    harness_id: str
    success: bool
    score: float
    started_at: str
    completed_at: str
    steps: tuple[ObservableTraceStep, ...]
    evidence_ids: tuple[str, ...]
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace_id", validate_id(self.trace_id, "trace_id"))
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.cycle, "cycle")
        object.__setattr__(self, "task_id", validate_id(self.task_id, "task_id"))
        object.__setattr__(
            self,
            "task_family",
            validate_id(self.task_family, "task_family"),
        )
        object.__setattr__(
            self,
            "model_checkpoint_id",
            validate_id(self.model_checkpoint_id, "model_checkpoint_id"),
        )
        object.__setattr__(
            self,
            "harness_id",
            validate_id(self.harness_id, "harness_id"),
        )
        if not isinstance(self.success, bool):
            raise TraceContractError("success must be a boolean")
        validate_finite_number(self.score, "score")
        if not 0.0 <= self.score <= 1.0:
            raise TraceContractError("score must be in [0, 1]")
        object.__setattr__(self, "started_at", normalize_timestamp(self.started_at))
        object.__setattr__(
            self,
            "completed_at",
            normalize_timestamp(self.completed_at),
        )
        if _timestamp(self.completed_at) < _timestamp(self.started_at):
            raise TraceContractError("completed_at cannot precede started_at")
        if not self.steps:
            raise TraceContractError("observable trajectory requires at least one step")
        indexes = tuple(step.step_index for step in self.steps)
        if indexes != tuple(range(len(self.steps))):
            raise TraceContractError(
                "observable trajectory step indexes must be contiguous from zero"
            )
        if self.success and not any(
            step.event_type is TraceEventType.FINAL_OUTPUT for step in self.steps
        ):
            raise TraceContractError(
                "successful observable trajectory requires FINAL_OUTPUT"
            )
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise TraceContractError("observable trajectory requires evidence_ids")
        object.__setattr__(
            self,
            "metadata",
            _safe_metadata(self.metadata, "metadata"),
        )
        expected_trace_id = self._content_trace_id()
        if self.trace_id != expected_trace_id:
            raise TraceContractError(
                f"trace_id must be content-addressed as {expected_trace_id}"
            )

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        cycle: int,
        task_id: str,
        task_family: str,
        model_checkpoint_id: str,
        harness_id: str,
        success: bool,
        score: float,
        started_at: str,
        completed_at: str,
        steps: tuple[ObservableTraceStep, ...],
        evidence_ids: tuple[str, ...],
        metadata: Mapping[str, object] | None = None,
    ) -> ObservableTrajectory:
        normalized_metadata = _safe_metadata(metadata or {}, "metadata")
        payload: dict[str, JSONValue] = {
            "run_id": run_id,
            "cycle": cycle,
            "task_id": task_id,
            "task_family": task_family,
            "model_checkpoint_id": model_checkpoint_id,
            "harness_id": harness_id,
            "success": success,
            "score": score,
            "started_at": normalize_timestamp(started_at),
            "completed_at": normalize_timestamp(completed_at),
            "steps": [step.to_dict() for step in steps],
            "evidence_ids": list(evidence_ids),
            "metadata": normalized_metadata,
        }
        trace_id = _content_id("trace", payload)
        return cls(trace_id=trace_id, metadata=normalized_metadata, **{
            "run_id": run_id,
            "cycle": cycle,
            "task_id": task_id,
            "task_family": task_family,
            "model_checkpoint_id": model_checkpoint_id,
            "harness_id": harness_id,
            "success": success,
            "score": score,
            "started_at": started_at,
            "completed_at": completed_at,
            "steps": steps,
            "evidence_ids": evidence_ids,
        })

    def _content_payload(self) -> dict[str, JSONValue]:
        return {
            "run_id": self.run_id,
            "cycle": self.cycle,
            "task_id": self.task_id,
            "task_family": self.task_family,
            "model_checkpoint_id": self.model_checkpoint_id,
            "harness_id": self.harness_id,
            "success": self.success,
            "score": self.score,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "steps": [step.to_dict() for step in self.steps],
            "evidence_ids": list(self.evidence_ids),
            "metadata": dict(self.metadata),
        }

    def _content_trace_id(self) -> str:
        return _content_id("trace", self._content_payload())

    @property
    def trace_sha256(self) -> str:
        return hashlib.sha256(
            canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, JSONValue]:
        return {"trace_id": self.trace_id, **self._content_payload()}

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ObservableTrajectory:
        data = _exact_mapping(
            value,
            {
                "trace_id",
                "run_id",
                "cycle",
                "task_id",
                "task_family",
                "model_checkpoint_id",
                "harness_id",
                "success",
                "score",
                "started_at",
                "completed_at",
                "steps",
                "evidence_ids",
                "metadata",
            },
            "observable_trajectory",
        )
        raw_steps = data["steps"]
        if not isinstance(raw_steps, Sequence) or isinstance(
            raw_steps,
            (str, bytes, bytearray),
        ):
            raise TraceContractError("steps must be an array")
        steps: list[ObservableTraceStep] = []
        for item in raw_steps:
            if not isinstance(item, Mapping):
                raise TraceContractError("steps must contain objects")
            steps.append(ObservableTraceStep.from_dict(item))
        metadata = data["metadata"]
        if not isinstance(metadata, Mapping):
            raise TraceContractError("metadata must be an object")
        return cls(
            trace_id=_required_str(data, "trace_id"),
            run_id=_required_str(data, "run_id"),
            cycle=_required_int(data, "cycle"),
            task_id=_required_str(data, "task_id"),
            task_family=_required_str(data, "task_family"),
            model_checkpoint_id=_required_str(data, "model_checkpoint_id"),
            harness_id=_required_str(data, "harness_id"),
            success=_required_bool(data, "success"),
            score=_required_float(data, "score"),
            started_at=_required_str(data, "started_at"),
            completed_at=_required_str(data, "completed_at"),
            steps=tuple(steps),
            evidence_ids=_required_str_tuple(data, "evidence_ids"),
            metadata=_safe_metadata(metadata, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class TraceRejection:
    trace_id: str
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace_id", validate_id(self.trace_id, "trace_id"))
        normalized = tuple(sorted({validate_id(reason, "reason") for reason in self.reasons}))
        if not normalized:
            raise TraceContractError("Trace rejection requires reasons")
        object.__setattr__(self, "reasons", normalized)

    def to_dict(self) -> dict[str, JSONValue]:
        return {"trace_id": self.trace_id, "reasons": list(self.reasons)}


@dataclass(frozen=True, slots=True)
class HarvestedTraceBatch:
    batch_id: str
    run_id: str
    cycle: int
    model_checkpoint_id: str
    harness_id: str
    target_count: int
    selected: tuple[ObservableTrajectory, ...]
    rejected: tuple[TraceRejection, ...]
    selection_seed: str
    cost_usd: float
    created_at: str
    evidence_ids: tuple[str, ...]
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "batch_id", validate_id(self.batch_id, "batch_id"))
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.cycle, "cycle")
        object.__setattr__(
            self,
            "model_checkpoint_id",
            validate_id(self.model_checkpoint_id, "model_checkpoint_id"),
        )
        object.__setattr__(
            self,
            "harness_id",
            validate_id(self.harness_id, "harness_id"),
        )
        validate_nonnegative_int(self.target_count, "target_count")
        if self.target_count < 1:
            raise TraceContractError("target_count must be positive")
        selected_ids = tuple(trace.trace_id for trace in self.selected)
        if len(selected_ids) != len(set(selected_ids)):
            raise TraceContractError("selected Trace IDs must be unique")
        for trace in self.selected:
            if not trace.success:
                raise TraceContractError("selected trace must be successful")
            if trace.run_id != self.run_id or trace.cycle != self.cycle:
                raise TraceContractError("selected trace Run/cycle mismatch")
            if trace.model_checkpoint_id != self.model_checkpoint_id:
                raise TraceContractError("selected trace model mismatch")
            if trace.harness_id != self.harness_id:
                raise TraceContractError("selected trace Harness mismatch")
        object.__setattr__(
            self,
            "selection_seed",
            validate_id(self.selection_seed, "selection_seed"),
        )
        validate_nonnegative_number(self.cost_usd, "cost_usd")
        object.__setattr__(self, "created_at", normalize_timestamp(self.created_at))
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise TraceContractError("Harvested Trace batch requires evidence_ids")
        object.__setattr__(
            self,
            "metadata",
            _safe_metadata(self.metadata, "metadata"),
        )
        expected_id = _content_id("trace-batch", self._content_payload())
        if self.batch_id != expected_id:
            raise TraceContractError(f"batch_id must be content-addressed as {expected_id}")

    @property
    def selected_count(self) -> int:
        return len(self.selected)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def _content_payload(self) -> dict[str, JSONValue]:
        return {
            "run_id": self.run_id,
            "cycle": self.cycle,
            "model_checkpoint_id": self.model_checkpoint_id,
            "harness_id": self.harness_id,
            "target_count": self.target_count,
            "selected": [trace.to_dict() for trace in self.selected],
            "rejected": [item.to_dict() for item in self.rejected],
            "selection_seed": self.selection_seed,
            "cost_usd": self.cost_usd,
            "created_at": self.created_at,
            "evidence_ids": list(self.evidence_ids),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        cycle: int,
        model_checkpoint_id: str,
        harness_id: str,
        target_count: int,
        selected: tuple[ObservableTrajectory, ...],
        rejected: tuple[TraceRejection, ...],
        selection_seed: str,
        cost_usd: float,
        created_at: str,
        evidence_ids: tuple[str, ...],
        metadata: Mapping[str, object] | None = None,
    ) -> HarvestedTraceBatch:
        safe_metadata = _safe_metadata(metadata or {}, "metadata")
        payload: dict[str, JSONValue] = {
            "run_id": run_id,
            "cycle": cycle,
            "model_checkpoint_id": model_checkpoint_id,
            "harness_id": harness_id,
            "target_count": target_count,
            "selected": [trace.to_dict() for trace in selected],
            "rejected": [item.to_dict() for item in rejected],
            "selection_seed": selection_seed,
            "cost_usd": cost_usd,
            "created_at": normalize_timestamp(created_at),
            "evidence_ids": list(evidence_ids),
            "metadata": safe_metadata,
        }
        return cls(
            batch_id=_content_id("trace-batch", payload),
            run_id=run_id,
            cycle=cycle,
            model_checkpoint_id=model_checkpoint_id,
            harness_id=harness_id,
            target_count=target_count,
            selected=selected,
            rejected=rejected,
            selection_seed=selection_seed,
            cost_usd=cost_usd,
            created_at=created_at,
            evidence_ids=evidence_ids,
            metadata=safe_metadata,
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {"batch_id": self.batch_id, **self._content_payload()}


@dataclass(frozen=True, slots=True)
class TraceTrainingExample:
    example_id: str
    trace_id: str
    task_id: str
    task_family: str
    prompt: str
    response: str
    code: str
    metadata: dict[str, JSONValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "example_id",
            validate_id(self.example_id, "example_id"),
        )
        object.__setattr__(self, "trace_id", validate_id(self.trace_id, "trace_id"))
        object.__setattr__(self, "task_id", validate_id(self.task_id, "task_id"))
        object.__setattr__(
            self,
            "task_family",
            validate_id(self.task_family, "task_family"),
        )
        object.__setattr__(self, "prompt", validate_text(self.prompt, "prompt"))
        object.__setattr__(
            self,
            "response",
            validate_text(self.response, "response"),
        )
        if not isinstance(self.code, str):
            raise TraceContractError("code must be a string")
        object.__setattr__(
            self,
            "metadata",
            _safe_metadata(self.metadata, "metadata"),
        )

    @property
    def text(self) -> str:
        return f"{self.prompt}\n{self.response}"

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "example_id": self.example_id,
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "task_family": self.task_family,
            "prompt": self.prompt,
            "response": self.response,
            "code": self.code,
            "source": "observable_success_trace",
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class TraceDatasetResult:
    batch_id: str
    dataset_id: str
    run_id: str
    cycle: int
    model_checkpoint_id: str
    harness_id: str
    dataset_path: str
    dataset_sha256: str
    audit_path: str
    raw_count: int
    accepted_count: int
    rejected_count: int
    acceptance_rate: float
    rejection_counts: dict[str, int]
    accepted_example_ids: tuple[str, ...]
    created_at: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "batch_id",
            "dataset_id",
            "run_id",
            "model_checkpoint_id",
            "harness_id",
        ):
            object.__setattr__(self, name, validate_id(getattr(self, name), name))
        validate_nonnegative_int(self.cycle, "cycle")
        object.__setattr__(
            self,
            "dataset_path",
            validate_text(self.dataset_path, "dataset_path"),
        )
        validate_sha256(self.dataset_sha256)
        object.__setattr__(
            self,
            "audit_path",
            validate_text(self.audit_path, "audit_path"),
        )
        for name in ("raw_count", "accepted_count", "rejected_count"):
            validate_nonnegative_int(getattr(self, name), name)
        if self.accepted_count + self.rejected_count != self.raw_count:
            raise TraceContractError(
                "accepted_count + rejected_count must equal raw_count"
            )
        validate_finite_number(self.acceptance_rate, "acceptance_rate")
        if not 0.0 <= self.acceptance_rate <= 1.0:
            raise TraceContractError("acceptance_rate must be in [0, 1]")
        normalized_rejections: dict[str, int] = {}
        for reason, count in self.rejection_counts.items():
            reason_id = validate_id(reason, "rejection reason")
            validate_nonnegative_int(count, f"rejection_counts[{reason_id}]")
            normalized_rejections[reason_id] = count
        object.__setattr__(self, "rejection_counts", normalized_rejections)
        object.__setattr__(
            self,
            "accepted_example_ids",
            validate_id_tuple(self.accepted_example_ids, "accepted_example_ids"),
        )
        if len(self.accepted_example_ids) != self.accepted_count:
            raise TraceContractError(
                "accepted_example_ids length must equal accepted_count"
            )
        object.__setattr__(self, "created_at", normalize_timestamp(self.created_at))
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise TraceContractError("Trace Dataset result requires evidence_ids")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "batch_id": self.batch_id,
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "cycle": self.cycle,
            "model_checkpoint_id": self.model_checkpoint_id,
            "harness_id": self.harness_id,
            "dataset_path": self.dataset_path,
            "dataset_sha256": self.dataset_sha256,
            "audit_path": self.audit_path,
            "raw_count": self.raw_count,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "acceptance_rate": self.acceptance_rate,
            "rejection_counts": dict(self.rejection_counts),
            "accepted_example_ids": list(self.accepted_example_ids),
            "created_at": self.created_at,
            "evidence_ids": list(self.evidence_ids),
        }


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
                raise TraceContractError(
                    f"observable metadata must not contain hidden reasoning field {path}.{key}"
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
        raise TraceContractError(f"{label} keys must be strings")
    actual = set(value)
    if actual != expected:
        raise TraceContractError(
            f"{label} fields mismatch: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )
    return dict(value)


def _required_str(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str):
        raise TraceContractError(f"{key} must be a string")
    return item


def _optional_str(value: Mapping[str, object], key: str) -> str | None:
    item = value[key]
    if item is None:
        return None
    if not isinstance(item, str):
        raise TraceContractError(f"{key} must be a string or null")
    return item


def _required_int(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int):
        raise TraceContractError(f"{key} must be an integer")
    return item


def _required_float(value: Mapping[str, object], key: str) -> float:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise TraceContractError(f"{key} must be a number")
    number = float(item)
    if not math.isfinite(number):
        raise TraceContractError(f"{key} must be finite")
    return number


def _required_bool(value: Mapping[str, object], key: str) -> bool:
    item = value[key]
    if not isinstance(item, bool):
        raise TraceContractError(f"{key} must be a boolean")
    return item


def _required_str_tuple(value: Mapping[str, object], key: str) -> tuple[str, ...]:
    item = value[key]
    if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)):
        raise TraceContractError(f"{key} must be an array of strings")
    result: list[str] = []
    for index, element in enumerate(item):
        if not isinstance(element, str):
            raise TraceContractError(f"{key}[{index}] must be a string")
        result.append(element)
    return tuple(result)
