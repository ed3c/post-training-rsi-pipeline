from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from ..control_plane import (
    DecisionAction,
    DecisionSubject,
    JSONValue,
)
from ..control_plane.validation import (
    canonical_json,
    copy_json_object,
    normalize_json_object,
    normalize_timestamp,
    required_id_tuple,
    required_int,
    required_json_object,
    required_str,
    validate_id,
    validate_id_tuple,
    validate_nonnegative_int,
    validate_sha256,
    validate_text,
    validated_record_mapping,
)
from .errors import ApprovalContractError

APPROVAL_SCHEMA_VERSION = "post-training-rsi.approval/v1"
_ALLOWED_SUBJECTS = {
    DecisionSubject.DATASET,
    DecisionSubject.CHECKPOINT,
    DecisionSubject.HARNESS,
}
_ALLOWED_ACTIONS = {DecisionAction.ACCEPT, DecisionAction.PROMOTE}


@dataclass(frozen=True, slots=True)
class ApprovalSampleItem:
    item_id: str
    content_sha256: str
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", validate_id(self.item_id, "item_id"))
        validate_sha256(self.content_sha256)
        object.__setattr__(
            self,
            "metadata",
            normalize_json_object(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "item_id": self.item_id,
            "content_sha256": self.content_sha256,
            "metadata": copy_json_object(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ApprovalSampleItem:
        data = _exact_mapping(
            value,
            {"item_id", "content_sha256", "metadata"},
            "sample_item",
        )
        return cls(
            item_id=required_str(data, "item_id"),
            content_sha256=required_str(data, "content_sha256"),
            metadata=required_json_object(data, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class ApprovalSampleManifest:
    RECORD_TYPE: ClassVar[str] = "approval_sample"

    request_id: str
    run_id: str
    iteration: int
    subject_type: DecisionSubject
    subject_id: str
    selection_algorithm: str
    selection_seed: str
    sample_rate: float
    population_count: int
    selected_count: int
    items: tuple[ApprovalSampleItem, ...]
    created_at: str
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_id",
            validate_id(self.request_id, "request_id"),
        )
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.iteration, "iteration")
        _validate_subject(self.subject_type)
        object.__setattr__(
            self,
            "subject_id",
            validate_id(self.subject_id, "subject_id"),
        )
        object.__setattr__(
            self,
            "selection_algorithm",
            validate_id(self.selection_algorithm, "selection_algorithm"),
        )
        object.__setattr__(
            self,
            "selection_seed",
            validate_id(self.selection_seed, "selection_seed"),
        )
        if isinstance(self.sample_rate, bool) or not isinstance(
            self.sample_rate,
            (int, float),
        ):
            raise ApprovalContractError("sample_rate must be a number")
        if not 0.0 < float(self.sample_rate) <= 1.0:
            raise ApprovalContractError("sample_rate must be in (0, 1]")
        validate_nonnegative_int(self.population_count, "population_count")
        validate_nonnegative_int(self.selected_count, "selected_count")
        if self.population_count < 1:
            raise ApprovalContractError("population_count must be positive")
        if self.selected_count < 1:
            raise ApprovalContractError("selected_count must be positive")
        if self.selected_count > self.population_count:
            raise ApprovalContractError(
                "selected_count cannot exceed population_count"
            )
        if len(self.items) != self.selected_count:
            raise ApprovalContractError(
                "selected_count must equal the number of sample items"
            )
        item_ids = tuple(item.item_id for item in self.items)
        if len(item_ids) != len(set(item_ids)):
            raise ApprovalContractError("sample item IDs must be unique")
        object.__setattr__(self, "created_at", normalize_timestamp(self.created_at))
        object.__setattr__(
            self,
            "metadata",
            normalize_json_object(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "iteration": self.iteration,
            "subject_type": self.subject_type.value,
            "subject_id": self.subject_id,
            "selection_algorithm": self.selection_algorithm,
            "selection_seed": self.selection_seed,
            "sample_rate": float(self.sample_rate),
            "population_count": self.population_count,
            "selected_count": self.selected_count,
            "items": [item.to_dict() for item in self.items],
            "created_at": self.created_at,
            "metadata": copy_json_object(self.metadata),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> ApprovalSampleManifest:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            _SAMPLE_FIELDS,
            APPROVAL_SCHEMA_VERSION,
        )
        items_value = data["items"]
        if not isinstance(items_value, Sequence) or isinstance(
            items_value,
            (str, bytes, bytearray),
        ):
            raise ApprovalContractError("items must be a JSON array")
        items: list[ApprovalSampleItem] = []
        for item in items_value:
            if not isinstance(item, Mapping):
                raise ApprovalContractError("sample items must be JSON objects")
            items.append(ApprovalSampleItem.from_dict(item))
        return cls(
            request_id=required_str(data, "request_id"),
            run_id=required_str(data, "run_id"),
            iteration=required_int(data, "iteration"),
            subject_type=_subject_from_value(data["subject_type"]),
            subject_id=required_str(data, "subject_id"),
            selection_algorithm=required_str(data, "selection_algorithm"),
            selection_seed=required_str(data, "selection_seed"),
            sample_rate=_required_float(data, "sample_rate"),
            population_count=required_int(data, "population_count"),
            selected_count=required_int(data, "selected_count"),
            items=tuple(items),
            created_at=required_str(data, "created_at"),
            metadata=required_json_object(data, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    RECORD_TYPE: ClassVar[str] = "approval_request"

    request_id: str
    run_id: str
    iteration: int
    subject_type: DecisionSubject
    subject_id: str
    requested_action: DecisionAction
    policy_id: str
    requested_at: str
    expires_at: str | None
    source_evidence_ids: tuple[str, ...]
    sample_uri: str
    sample_sha256: str
    sample_count: int
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_id",
            validate_id(self.request_id, "request_id"),
        )
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.iteration, "iteration")
        _validate_subject(self.subject_type)
        _validate_action(self.subject_type, self.requested_action)
        object.__setattr__(
            self,
            "subject_id",
            validate_id(self.subject_id, "subject_id"),
        )
        object.__setattr__(
            self,
            "policy_id",
            validate_id(self.policy_id, "policy_id"),
        )
        requested_at = normalize_timestamp(self.requested_at)
        object.__setattr__(self, "requested_at", requested_at)
        if self.expires_at is not None:
            expires_at = normalize_timestamp(self.expires_at)
            if _parse_timestamp(expires_at) <= _parse_timestamp(requested_at):
                raise ApprovalContractError(
                    "expires_at must be later than requested_at"
                )
            object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(
            self,
            "source_evidence_ids",
            validate_id_tuple(self.source_evidence_ids, "source_evidence_ids"),
        )
        if not self.source_evidence_ids:
            raise ApprovalContractError(
                "approval requests require source_evidence_ids"
            )
        object.__setattr__(
            self,
            "sample_uri",
            validate_text(self.sample_uri, "sample_uri"),
        )
        validate_sha256(self.sample_sha256)
        validate_nonnegative_int(self.sample_count, "sample_count")
        if self.sample_count < 1:
            raise ApprovalContractError("sample_count must be positive")
        object.__setattr__(
            self,
            "metadata",
            normalize_json_object(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "iteration": self.iteration,
            "subject_type": self.subject_type.value,
            "subject_id": self.subject_id,
            "requested_action": self.requested_action.value,
            "policy_id": self.policy_id,
            "requested_at": self.requested_at,
            "expires_at": self.expires_at,
            "source_evidence_ids": list(self.source_evidence_ids),
            "sample_uri": self.sample_uri,
            "sample_sha256": self.sample_sha256,
            "sample_count": self.sample_count,
            "metadata": copy_json_object(self.metadata),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ApprovalRequest:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            _REQUEST_FIELDS,
            APPROVAL_SCHEMA_VERSION,
        )
        expires_at = data["expires_at"]
        if expires_at is not None and not isinstance(expires_at, str):
            raise ApprovalContractError("expires_at must be a string or null")
        return cls(
            request_id=required_str(data, "request_id"),
            run_id=required_str(data, "run_id"),
            iteration=required_int(data, "iteration"),
            subject_type=_subject_from_value(data["subject_type"]),
            subject_id=required_str(data, "subject_id"),
            requested_action=_action_from_value(data["requested_action"]),
            policy_id=required_str(data, "policy_id"),
            requested_at=required_str(data, "requested_at"),
            expires_at=expires_at,
            source_evidence_ids=required_id_tuple(data, "source_evidence_ids"),
            sample_uri=required_str(data, "sample_uri"),
            sample_sha256=required_str(data, "sample_sha256"),
            sample_count=required_int(data, "sample_count"),
            metadata=required_json_object(data, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    RECORD_TYPE: ClassVar[str] = "approval_decision"

    decision_id: str
    request_id: str
    request_sha256: str
    run_id: str
    iteration: int
    subject_type: DecisionSubject
    subject_id: str
    requested_action: DecisionAction
    approved: bool
    reviewer_id: str
    reviewer_role: str
    reason: str
    decided_at: str
    evidence_ids: tuple[str, ...]
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "decision_id",
            validate_id(self.decision_id, "decision_id"),
        )
        object.__setattr__(
            self,
            "request_id",
            validate_id(self.request_id, "request_id"),
        )
        validate_sha256(self.request_sha256)
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.iteration, "iteration")
        _validate_subject(self.subject_type)
        _validate_action(self.subject_type, self.requested_action)
        object.__setattr__(
            self,
            "subject_id",
            validate_id(self.subject_id, "subject_id"),
        )
        if not isinstance(self.approved, bool):
            raise ApprovalContractError("approved must be a boolean")
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
        object.__setattr__(self, "reason", validate_text(self.reason, "reason"))
        object.__setattr__(self, "decided_at", normalize_timestamp(self.decided_at))
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ApprovalContractError(
                "approval decisions require evidence_ids"
            )
        object.__setattr__(
            self,
            "metadata",
            normalize_json_object(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "decision_id": self.decision_id,
            "request_id": self.request_id,
            "request_sha256": self.request_sha256,
            "run_id": self.run_id,
            "iteration": self.iteration,
            "subject_type": self.subject_type.value,
            "subject_id": self.subject_id,
            "requested_action": self.requested_action.value,
            "approved": self.approved,
            "reviewer_id": self.reviewer_id,
            "reviewer_role": self.reviewer_role,
            "reason": self.reason,
            "decided_at": self.decided_at,
            "evidence_ids": list(self.evidence_ids),
            "metadata": copy_json_object(self.metadata),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ApprovalDecision:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            _DECISION_FIELDS,
            APPROVAL_SCHEMA_VERSION,
        )
        approved = data["approved"]
        if not isinstance(approved, bool):
            raise ApprovalContractError("approved must be a boolean")
        return cls(
            decision_id=required_str(data, "decision_id"),
            request_id=required_str(data, "request_id"),
            request_sha256=required_str(data, "request_sha256"),
            run_id=required_str(data, "run_id"),
            iteration=required_int(data, "iteration"),
            subject_type=_subject_from_value(data["subject_type"]),
            subject_id=required_str(data, "subject_id"),
            requested_action=_action_from_value(data["requested_action"]),
            approved=approved,
            reviewer_id=required_str(data, "reviewer_id"),
            reviewer_role=required_str(data, "reviewer_role"),
            reason=required_str(data, "reason"),
            decided_at=required_str(data, "decided_at"),
            evidence_ids=required_id_tuple(data, "evidence_ids"),
            metadata=required_json_object(data, "metadata"),
        )


def _validate_subject(subject: DecisionSubject) -> None:
    if subject not in _ALLOWED_SUBJECTS:
        raise ApprovalContractError(
            f"approval subject {subject.value!r} is unsupported"
        )


def _validate_action(
    subject: DecisionSubject,
    action: DecisionAction,
) -> None:
    if action not in _ALLOWED_ACTIONS:
        raise ApprovalContractError(
            f"requested action {action.value!r} is unsupported"
        )
    if subject is DecisionSubject.CHECKPOINT and action is not DecisionAction.PROMOTE:
        raise ApprovalContractError("Checkpoint approval must request PROMOTE")
    if subject in {DecisionSubject.DATASET, DecisionSubject.HARNESS} and (
        action is not DecisionAction.ACCEPT
    ):
        raise ApprovalContractError(
            f"{subject.value} approval must request ACCEPT"
        )


def _subject_from_value(value: object) -> DecisionSubject:
    if not isinstance(value, str):
        raise ApprovalContractError("subject_type must be a string")
    try:
        subject = DecisionSubject(value)
    except ValueError as exc:
        raise ApprovalContractError(
            f"unsupported subject_type: {value!r}"
        ) from exc
    _validate_subject(subject)
    return subject


def _action_from_value(value: object) -> DecisionAction:
    if not isinstance(value, str):
        raise ApprovalContractError("requested_action must be a string")
    try:
        return DecisionAction(value)
    except ValueError as exc:
        raise ApprovalContractError(
            f"unsupported requested_action: {value!r}"
        ) from exc


def _required_float(data: Mapping[str, object], key: str) -> float:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApprovalContractError(f"{key} must be a number")
    return float(value)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _exact_mapping(
    value: Mapping[str, object],
    expected: set[str],
    field_name: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ApprovalContractError(f"{field_name} must be a JSON object")
    data = dict(value)
    missing = sorted(expected - set(data))
    unknown = sorted(set(data) - expected)
    if missing or unknown:
        raise ApprovalContractError(
            f"{field_name} fields mismatch: missing={missing}, unknown={unknown}"
        )
    return data


_SAMPLE_FIELDS = {
    "schema_version",
    "record_type",
    "request_id",
    "run_id",
    "iteration",
    "subject_type",
    "subject_id",
    "selection_algorithm",
    "selection_seed",
    "sample_rate",
    "population_count",
    "selected_count",
    "items",
    "created_at",
    "metadata",
}
_REQUEST_FIELDS = {
    "schema_version",
    "record_type",
    "request_id",
    "run_id",
    "iteration",
    "subject_type",
    "subject_id",
    "requested_action",
    "policy_id",
    "requested_at",
    "expires_at",
    "source_evidence_ids",
    "sample_uri",
    "sample_sha256",
    "sample_count",
    "metadata",
}
_DECISION_FIELDS = {
    "schema_version",
    "record_type",
    "decision_id",
    "request_id",
    "request_sha256",
    "run_id",
    "iteration",
    "subject_type",
    "subject_id",
    "requested_action",
    "approved",
    "reviewer_id",
    "reviewer_role",
    "reason",
    "decided_at",
    "evidence_ids",
    "metadata",
}
