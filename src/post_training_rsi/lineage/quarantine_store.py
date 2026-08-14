from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..control_plane import (
    DecisionAction,
    DecisionRecord,
    DecisionSubject,
    JSONValue,
)
from ..control_plane.validation import (
    ControlContractError,
    normalize_timestamp,
    required_enum,
    required_id_tuple,
    required_int,
    required_str,
    validate_id,
    validate_id_tuple,
    validate_nonnegative_int,
    validate_text,
    validated_record_mapping,
)
from ._io import (
    LineageIntegrityError,
    canonical_json_bytes,
    exclusive_lock,
    read_json_object,
    write_immutable,
)
from .control_store import LINEAGE_SCHEMA_VERSION, ControlRecordStore

_ALLOWED_ACTIONS = {
    DecisionAction.QUARANTINE,
    DecisionAction.REJECT,
    DecisionAction.ROLLBACK,
}


@dataclass(frozen=True, slots=True)
class QuarantineMarker:
    RECORD_TYPE = "quarantine_marker"

    run_id: str
    iteration: int
    subject_type: DecisionSubject
    subject_id: str
    decision_id: str
    control_transaction_id: str
    reason_code: str
    reason: str
    evidence_ids: tuple[str, ...]
    created_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.iteration, "iteration")
        object.__setattr__(
            self,
            "subject_id",
            validate_id(self.subject_id, "subject_id"),
        )
        object.__setattr__(
            self,
            "decision_id",
            validate_id(self.decision_id, "decision_id"),
        )
        object.__setattr__(
            self,
            "control_transaction_id",
            validate_id(self.control_transaction_id, "control_transaction_id"),
        )
        object.__setattr__(
            self,
            "reason_code",
            validate_id(self.reason_code, "reason_code"),
        )
        object.__setattr__(self, "reason", validate_text(self.reason, "reason"))
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ControlContractError("quarantine marker requires evidence_ids")
        object.__setattr__(self, "created_at", normalize_timestamp(self.created_at))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": LINEAGE_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "run_id": self.run_id,
            "iteration": self.iteration,
            "subject_type": self.subject_type.value,
            "subject_id": self.subject_id,
            "decision_id": self.decision_id,
            "control_transaction_id": self.control_transaction_id,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> QuarantineMarker:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            {
                "schema_version",
                "record_type",
                "run_id",
                "iteration",
                "subject_type",
                "subject_id",
                "decision_id",
                "control_transaction_id",
                "reason_code",
                "reason",
                "evidence_ids",
                "created_at",
            },
            LINEAGE_SCHEMA_VERSION,
        )
        return cls(
            run_id=required_str(data, "run_id"),
            iteration=required_int(data, "iteration"),
            subject_type=required_enum(data, "subject_type", DecisionSubject),
            subject_id=required_str(data, "subject_id"),
            decision_id=required_str(data, "decision_id"),
            control_transaction_id=required_str(data, "control_transaction_id"),
            reason_code=required_str(data, "reason_code"),
            reason=required_str(data, "reason"),
            evidence_ids=required_id_tuple(data, "evidence_ids"),
            created_at=required_str(data, "created_at"),
        )


class QuarantineStore:
    """Persist immutable evidence-backed quarantine/reject/rollback markers."""

    def __init__(
        self,
        root: str | Path,
        control_store: ControlRecordStore,
        *,
        lock_timeout_seconds: float = 2.0,
        poll_interval_seconds: float = 0.01,
    ) -> None:
        self.root = Path(root)
        self.quarantine_root = self.root / "quarantine"
        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.quarantine_root / ".quarantine.lock"
        self.control_store = control_store
        self.lock_timeout_seconds = lock_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def commit(self, marker: QuarantineMarker) -> QuarantineMarker:
        self._verify_links(marker)
        path = self._marker_path(marker)
        content = canonical_json_bytes(marker.to_dict())
        with exclusive_lock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
        ):
            write_immutable(path, content)
        return self.load(
            iteration=marker.iteration,
            subject_type=marker.subject_type,
            subject_id=marker.subject_id,
        )

    def load(
        self,
        *,
        iteration: int,
        subject_type: DecisionSubject,
        subject_id: str,
    ) -> QuarantineMarker:
        validate_nonnegative_int(iteration, "iteration")
        subject_id = validate_id(subject_id, "subject_id")
        path = self.quarantine_root / _marker_filename(
            iteration,
            subject_type,
            subject_id,
        )
        if not path.exists():
            raise LineageIntegrityError(f"unknown quarantine marker {path.name}")
        marker = QuarantineMarker.from_dict(read_json_object(path))
        if (
            marker.iteration != iteration
            or marker.subject_type is not subject_type
            or marker.subject_id != subject_id
        ):
            raise LineageIntegrityError(
                "quarantine marker filename and payload differ"
            )
        self._verify_links(marker)
        return marker

    def _verify_links(self, marker: QuarantineMarker) -> None:
        transaction = self.control_store.load_transaction(
            marker.control_transaction_id
        )
        if transaction.run_id != marker.run_id:
            raise LineageIntegrityError(
                "quarantine marker and control transaction Run IDs differ"
            )
        if transaction.iteration != marker.iteration:
            raise LineageIntegrityError(
                "quarantine marker and control transaction iterations differ"
            )
        if not any(
            ref.record_type == DecisionRecord.RECORD_TYPE
            and ref.record_id == marker.decision_id
            for ref in transaction.records
        ):
            raise LineageIntegrityError(
                "quarantine Decision is not committed by the referenced transaction"
            )
        decision = self.control_store.load_decision(marker.decision_id)
        if decision.action not in _ALLOWED_ACTIONS:
            raise LineageIntegrityError(
                "quarantine marker requires QUARANTINE, REJECT, or ROLLBACK Decision"
            )
        if decision.run_id != marker.run_id or decision.iteration != marker.iteration:
            raise LineageIntegrityError(
                "quarantine marker and Decision lineage differ"
            )
        if (
            decision.subject_type is not marker.subject_type
            or decision.subject_id != marker.subject_id
        ):
            raise LineageIntegrityError(
                "quarantine marker and Decision subjects differ"
            )
        if decision.reason_code != marker.reason_code:
            raise LineageIntegrityError(
                "quarantine marker and Decision reason codes differ"
            )
        if decision.evidence_ids != marker.evidence_ids:
            raise LineageIntegrityError(
                "quarantine marker and Decision evidence IDs differ"
            )

    def _marker_path(self, marker: QuarantineMarker) -> Path:
        return self.quarantine_root / _marker_filename(
            marker.iteration,
            marker.subject_type,
            marker.subject_id,
        )


def _marker_filename(
    iteration: int,
    subject_type: DecisionSubject,
    subject_id: str,
) -> str:
    return (
        f"iter-{iteration:06d}-{subject_type.value.lower()}-{subject_id}.json"
    )
