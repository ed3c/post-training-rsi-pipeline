from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar

from .enums import (
    CONTROL_SCHEMA_VERSION,
    ControlEvent,
    ControlState,
    DecisionAction,
    DecisionSubject,
    EvidenceKind,
    StopReason,
)
from .validation import (
    ControlContractError,
    JSONValue,
    canonical_json,
    copy_json_object,
    normalize_json_object,
    normalize_timestamp,
    optional_enum,
    optional_float,
    optional_str,
    required_enum,
    required_float,
    required_id_tuple,
    required_int,
    required_json_object,
    required_str,
    validate_finite_number,
    validate_id,
    validate_id_tuple,
    validate_nonnegative_int,
    validate_nonnegative_number,
    validate_sha256,
    validate_text,
    validated_record_mapping,
)

_TERMINAL_STATES = {
    ControlState.COMPLETED,
    ControlState.STOPPED,
    ControlState.ABORTED,
    ControlState.ROLLED_BACK,
}
_STOP_ACTIONS = {DecisionAction.STOP, DecisionAction.ABORT}
_STOP_COMPATIBLE_ACTIONS = _STOP_ACTIONS | {
    DecisionAction.REJECT,
    DecisionAction.QUARANTINE,
    DecisionAction.ROLLBACK,
}


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """A durable evidence pointer emitted by exactly one producer."""

    RECORD_TYPE: ClassVar[str] = "evidence"

    evidence_id: str
    run_id: str
    iteration: int
    kind: EvidenceKind
    producer: str
    uri: str
    created_at: str
    sha256: str | None = None
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", validate_id(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.iteration, "iteration")
        object.__setattr__(self, "producer", validate_id(self.producer, "producer"))
        object.__setattr__(self, "uri", validate_text(self.uri, "uri"))
        object.__setattr__(self, "created_at", normalize_timestamp(self.created_at))
        validate_sha256(self.sha256)
        object.__setattr__(self, "metadata", normalize_json_object(self.metadata, "metadata"))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": CONTROL_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "evidence_id": self.evidence_id,
            "run_id": self.run_id,
            "iteration": self.iteration,
            "kind": self.kind.value,
            "producer": self.producer,
            "uri": self.uri,
            "created_at": self.created_at,
            "sha256": self.sha256,
            "metadata": copy_json_object(self.metadata),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvidenceRecord:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            _EVIDENCE_FIELDS,
            CONTROL_SCHEMA_VERSION,
        )
        return cls(
            evidence_id=required_str(data, "evidence_id"),
            run_id=required_str(data, "run_id"),
            iteration=required_int(data, "iteration"),
            kind=required_enum(data, "kind", EvidenceKind),
            producer=required_str(data, "producer"),
            uri=required_str(data, "uri"),
            created_at=required_str(data, "created_at"),
            sha256=optional_str(data, "sha256"),
            metadata=required_json_object(data, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """An immutable policy decision backed by durable evidence IDs."""

    RECORD_TYPE: ClassVar[str] = "decision"

    decision_id: str
    run_id: str
    iteration: int
    subject_type: DecisionSubject
    subject_id: str
    action: DecisionAction
    reason_code: str
    reason: str
    evidence_ids: tuple[str, ...]
    created_at: str
    stop_reason: StopReason | None = None
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", validate_id(self.decision_id, "decision_id"))
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.iteration, "iteration")
        object.__setattr__(self, "subject_id", validate_id(self.subject_id, "subject_id"))
        object.__setattr__(self, "reason_code", validate_id(self.reason_code, "reason_code"))
        object.__setattr__(self, "reason", validate_text(self.reason, "reason"))
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ControlContractError("decisions require at least one evidence_id")
        object.__setattr__(self, "created_at", normalize_timestamp(self.created_at))
        object.__setattr__(self, "metadata", normalize_json_object(self.metadata, "metadata"))
        if self.action in _STOP_ACTIONS and self.stop_reason is None:
            raise ControlContractError(f"{self.action.value} decisions require stop_reason")
        if self.stop_reason is not None and self.action not in _STOP_COMPATIBLE_ACTIONS:
            raise ControlContractError(
                f"stop_reason is not valid for decision action {self.action.value}"
            )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": CONTROL_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "iteration": self.iteration,
            "subject_type": self.subject_type.value,
            "subject_id": self.subject_id,
            "action": self.action.value,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "created_at": self.created_at,
            "stop_reason": self.stop_reason.value if self.stop_reason else None,
            "metadata": copy_json_object(self.metadata),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> DecisionRecord:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            _DECISION_FIELDS,
            CONTROL_SCHEMA_VERSION,
        )
        return cls(
            decision_id=required_str(data, "decision_id"),
            run_id=required_str(data, "run_id"),
            iteration=required_int(data, "iteration"),
            subject_type=required_enum(data, "subject_type", DecisionSubject),
            subject_id=required_str(data, "subject_id"),
            action=required_enum(data, "action", DecisionAction),
            reason_code=required_str(data, "reason_code"),
            reason=required_str(data, "reason"),
            evidence_ids=required_id_tuple(data, "evidence_ids"),
            created_at=required_str(data, "created_at"),
            stop_reason=optional_enum(data, "stop_reason", StopReason),
            metadata=required_json_object(data, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """Serializable control-plane state without embedding provider-specific objects."""

    RECORD_TYPE: ClassVar[str] = "state_snapshot"

    snapshot_id: str
    run_id: str
    iteration: int
    cycle: int
    state: ControlState
    entered_at: str
    active_checkpoint_id: str | None = None
    candidate_checkpoint_id: str | None = None
    peak_checkpoint_id: str | None = None
    active_harness_id: str | None = None
    candidate_harness_id: str | None = None
    candidate_score: float | None = None
    peak_score: float | None = None
    plateau_count: int = 0
    total_cost_usd: float = 0.0
    stop_reason: StopReason | None = None
    evidence_ids: tuple[str, ...] = ()
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", validate_id(self.snapshot_id, "snapshot_id"))
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.iteration, "iteration")
        validate_nonnegative_int(self.cycle, "cycle")
        object.__setattr__(self, "entered_at", normalize_timestamp(self.entered_at))
        for name in (
            "active_checkpoint_id",
            "candidate_checkpoint_id",
            "peak_checkpoint_id",
            "active_harness_id",
            "candidate_harness_id",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, validate_id(value, name))
        for name in ("candidate_score", "peak_score"):
            value = getattr(self, name)
            if value is not None:
                validate_finite_number(value, name)
        validate_nonnegative_int(self.plateau_count, "plateau_count")
        validate_nonnegative_number(self.total_cost_usd, "total_cost_usd")
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        object.__setattr__(self, "metadata", normalize_json_object(self.metadata, "metadata"))
        if self.state in _TERMINAL_STATES and self.stop_reason is None:
            raise ControlContractError(f"terminal state {self.state.value} requires stop_reason")
        if self.state not in _TERMINAL_STATES and self.stop_reason is not None:
            raise ControlContractError(
                f"non-terminal state {self.state.value} cannot carry stop_reason"
            )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": CONTROL_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "snapshot_id": self.snapshot_id,
            "run_id": self.run_id,
            "iteration": self.iteration,
            "cycle": self.cycle,
            "state": self.state.value,
            "entered_at": self.entered_at,
            "active_checkpoint_id": self.active_checkpoint_id,
            "candidate_checkpoint_id": self.candidate_checkpoint_id,
            "peak_checkpoint_id": self.peak_checkpoint_id,
            "active_harness_id": self.active_harness_id,
            "candidate_harness_id": self.candidate_harness_id,
            "candidate_score": self.candidate_score,
            "peak_score": self.peak_score,
            "plateau_count": self.plateau_count,
            "total_cost_usd": self.total_cost_usd,
            "stop_reason": self.stop_reason.value if self.stop_reason else None,
            "evidence_ids": list(self.evidence_ids),
            "metadata": copy_json_object(self.metadata),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> StateSnapshot:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            _STATE_FIELDS,
            CONTROL_SCHEMA_VERSION,
        )
        return cls(
            snapshot_id=required_str(data, "snapshot_id"),
            run_id=required_str(data, "run_id"),
            iteration=required_int(data, "iteration"),
            cycle=required_int(data, "cycle"),
            state=required_enum(data, "state", ControlState),
            entered_at=required_str(data, "entered_at"),
            active_checkpoint_id=optional_str(data, "active_checkpoint_id"),
            candidate_checkpoint_id=optional_str(data, "candidate_checkpoint_id"),
            peak_checkpoint_id=optional_str(data, "peak_checkpoint_id"),
            active_harness_id=optional_str(data, "active_harness_id"),
            candidate_harness_id=optional_str(data, "candidate_harness_id"),
            candidate_score=optional_float(data, "candidate_score"),
            peak_score=optional_float(data, "peak_score"),
            plateau_count=required_int(data, "plateau_count"),
            total_cost_usd=required_float(data, "total_cost_usd"),
            stop_reason=optional_enum(data, "stop_reason", StopReason),
            evidence_ids=required_id_tuple(data, "evidence_ids"),
            metadata=required_json_object(data, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """An idempotent transition fact; adjacency policy belongs to later controllers."""

    RECORD_TYPE: ClassVar[str] = "transition"

    transition_id: str
    run_id: str
    iteration: int
    from_state: ControlState | None
    event: ControlEvent
    to_state: ControlState
    occurred_at: str
    idempotency_key: str
    decision_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "transition_id",
            validate_id(self.transition_id, "transition_id"),
        )
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.iteration, "iteration")
        object.__setattr__(self, "occurred_at", normalize_timestamp(self.occurred_at))
        object.__setattr__(
            self,
            "idempotency_key",
            validate_id(self.idempotency_key, "idempotency_key"),
        )
        if self.decision_id is not None:
            object.__setattr__(
                self,
                "decision_id",
                validate_id(self.decision_id, "decision_id"),
            )
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ControlContractError("transitions require at least one evidence_id")
        object.__setattr__(self, "metadata", normalize_json_object(self.metadata, "metadata"))
        if self.from_state is None and self.event is not ControlEvent.START:
            raise ControlContractError("from_state may be null only for START transitions")
        if self.event is ControlEvent.START and self.from_state is not None:
            raise ControlContractError("START transitions must not declare from_state")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": CONTROL_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "transition_id": self.transition_id,
            "run_id": self.run_id,
            "iteration": self.iteration,
            "from_state": self.from_state.value if self.from_state else None,
            "event": self.event.value,
            "to_state": self.to_state.value,
            "occurred_at": self.occurred_at,
            "idempotency_key": self.idempotency_key,
            "decision_id": self.decision_id,
            "evidence_ids": list(self.evidence_ids),
            "metadata": copy_json_object(self.metadata),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> TransitionRecord:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            _TRANSITION_FIELDS,
            CONTROL_SCHEMA_VERSION,
        )
        return cls(
            transition_id=required_str(data, "transition_id"),
            run_id=required_str(data, "run_id"),
            iteration=required_int(data, "iteration"),
            from_state=optional_enum(data, "from_state", ControlState),
            event=required_enum(data, "event", ControlEvent),
            to_state=required_enum(data, "to_state", ControlState),
            occurred_at=required_str(data, "occurred_at"),
            idempotency_key=required_str(data, "idempotency_key"),
            decision_id=optional_str(data, "decision_id"),
            evidence_ids=required_id_tuple(data, "evidence_ids"),
            metadata=required_json_object(data, "metadata"),
        )


_COMMON_FIELDS = {"schema_version", "record_type"}
_EVIDENCE_FIELDS = _COMMON_FIELDS | {
    "evidence_id",
    "run_id",
    "iteration",
    "kind",
    "producer",
    "uri",
    "created_at",
    "sha256",
    "metadata",
}
_DECISION_FIELDS = _COMMON_FIELDS | {
    "decision_id",
    "run_id",
    "iteration",
    "subject_type",
    "subject_id",
    "action",
    "reason_code",
    "reason",
    "evidence_ids",
    "created_at",
    "stop_reason",
    "metadata",
}
_STATE_FIELDS = _COMMON_FIELDS | {
    "snapshot_id",
    "run_id",
    "iteration",
    "cycle",
    "state",
    "entered_at",
    "active_checkpoint_id",
    "candidate_checkpoint_id",
    "peak_checkpoint_id",
    "active_harness_id",
    "candidate_harness_id",
    "candidate_score",
    "peak_score",
    "plateau_count",
    "total_cost_usd",
    "stop_reason",
    "evidence_ids",
    "metadata",
}
_TRANSITION_FIELDS = _COMMON_FIELDS | {
    "transition_id",
    "run_id",
    "iteration",
    "from_state",
    "event",
    "to_state",
    "occurred_at",
    "idempotency_key",
    "decision_id",
    "evidence_ids",
    "metadata",
}
