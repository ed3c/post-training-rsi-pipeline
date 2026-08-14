from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from ..control_plane import (
    DecisionRecord,
    EvidenceRecord,
    JSONValue,
    StateSnapshot,
    TransitionRecord,
)
from ..control_plane.validation import (
    ControlContractError,
    normalize_timestamp,
    required_int,
    required_str,
    validate_id,
    validate_nonnegative_int,
    validate_sha256,
    validated_record_mapping,
)
from ._io import (
    LineageConflictError,
    LineageIntegrityError,
    canonical_json_bytes,
    exclusive_lock,
    read_json_object,
    sha256_bytes,
    verify_file_hash,
    write_immutable,
)

LINEAGE_SCHEMA_VERSION = "post-training-rsi.lineage/v1"

ControlRecord: TypeAlias = (
    EvidenceRecord | DecisionRecord | TransitionRecord | StateSnapshot
)

_RECORD_DIRECTORIES = {
    EvidenceRecord.RECORD_TYPE: "evidence",
    DecisionRecord.RECORD_TYPE: "decisions",
    TransitionRecord.RECORD_TYPE: "transitions",
    StateSnapshot.RECORD_TYPE: "snapshots",
}
_RECORD_CLASSES = {
    EvidenceRecord.RECORD_TYPE: EvidenceRecord,
    DecisionRecord.RECORD_TYPE: DecisionRecord,
    TransitionRecord.RECORD_TYPE: TransitionRecord,
    StateSnapshot.RECORD_TYPE: StateSnapshot,
}
_RECORD_ID_FIELDS = {
    EvidenceRecord.RECORD_TYPE: "evidence_id",
    DecisionRecord.RECORD_TYPE: "decision_id",
    TransitionRecord.RECORD_TYPE: "transition_id",
    StateSnapshot.RECORD_TYPE: "snapshot_id",
}


@dataclass(frozen=True, slots=True)
class StoredRecordRef:
    record_type: str
    record_id: str
    relative_path: str
    sha256: str

    def __post_init__(self) -> None:
        if self.record_type not in _RECORD_DIRECTORIES:
            raise ControlContractError(
                f"unsupported control record_type: {self.record_type!r}"
            )
        object.__setattr__(
            self,
            "record_id",
            validate_id(self.record_id, "record_id"),
        )
        expected = (
            Path(_RECORD_DIRECTORIES[self.record_type]) / f"{self.record_id}.json"
        ).as_posix()
        if self.relative_path != expected:
            raise ControlContractError(
                f"relative_path must be {expected!r} for {self.record_type}"
            )
        validate_sha256(self.sha256)

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "record_type": self.record_type,
            "record_id": self.record_id,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> StoredRecordRef:
        fields = {"record_type", "record_id", "relative_path", "sha256"}
        if set(value) != fields:
            raise ControlContractError(
                f"stored record fields mismatch: expected={sorted(fields)}, "
                f"actual={sorted(value)}"
            )
        return cls(
            record_type=_required_mapping_str(value, "record_type"),
            record_id=_required_mapping_str(value, "record_id"),
            relative_path=_required_mapping_str(value, "relative_path"),
            sha256=_required_mapping_str(value, "sha256"),
        )


@dataclass(frozen=True, slots=True)
class ControlTransactionManifest:
    RECORD_TYPE = "control_transaction"

    transaction_id: str
    run_id: str
    iteration: int
    created_at: str
    records: tuple[StoredRecordRef, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "transaction_id",
            validate_id(self.transaction_id, "transaction_id"),
        )
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.iteration, "iteration")
        object.__setattr__(self, "created_at", normalize_timestamp(self.created_at))
        if not self.records:
            raise ControlContractError("control transaction must contain records")
        ordered = tuple(
            sorted(self.records, key=lambda item: (item.record_type, item.record_id))
        )
        if len({(item.record_type, item.record_id) for item in ordered}) != len(ordered):
            raise ControlContractError("control transaction contains duplicate record IDs")
        object.__setattr__(self, "records", ordered)

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": LINEAGE_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "transaction_id": self.transaction_id,
            "run_id": self.run_id,
            "iteration": self.iteration,
            "created_at": self.created_at,
            "records": [item.to_dict() for item in self.records],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ControlTransactionManifest:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            {
                "schema_version",
                "record_type",
                "transaction_id",
                "run_id",
                "iteration",
                "created_at",
                "records",
            },
            LINEAGE_SCHEMA_VERSION,
        )
        raw_records = data["records"]
        if not isinstance(raw_records, Sequence) or isinstance(
            raw_records, (str, bytes, bytearray)
        ):
            raise ControlContractError("records must be an array")
        refs: list[StoredRecordRef] = []
        for item in raw_records:
            if not isinstance(item, Mapping):
                raise ControlContractError("records must contain JSON objects")
            refs.append(StoredRecordRef.from_dict(item))
        return cls(
            transaction_id=required_str(data, "transaction_id"),
            run_id=required_str(data, "run_id"),
            iteration=required_int(data, "iteration"),
            created_at=required_str(data, "created_at"),
            records=tuple(refs),
        )


class ControlRecordStore:
    """Immutable schema-v1 control records with a commit marker written last."""

    def __init__(
        self,
        root: str | Path,
        *,
        lock_timeout_seconds: float = 2.0,
        poll_interval_seconds: float = 0.01,
    ) -> None:
        self.root = Path(root)
        self.control_root = self.root / "control"
        self.transactions_root = self.control_root / "transactions"
        self.lock_path = self.control_root / ".commit.lock"
        self.lock_timeout_seconds = lock_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        for directory in (*_RECORD_DIRECTORIES.values(), "transactions"):
            (self.control_root / directory).mkdir(parents=True, exist_ok=True)

    def commit(
        self,
        *,
        transaction_id: str,
        run_id: str,
        iteration: int,
        created_at: str,
        records: Iterable[ControlRecord],
    ) -> ControlTransactionManifest:
        normalized_records = tuple(records)
        if not normalized_records:
            raise ControlContractError("control transaction must contain records")
        transaction_id = validate_id(transaction_id, "transaction_id")
        run_id = validate_id(run_id, "run_id")
        validate_nonnegative_int(iteration, "iteration")
        created_at = normalize_timestamp(created_at)

        record_payloads: dict[tuple[str, str], tuple[ControlRecord, bytes]] = {}
        for record in normalized_records:
            record_type, record_id = _record_identity(record)
            if record.run_id != run_id:
                raise ControlContractError(
                    f"record {record_id} belongs to run {record.run_id}, not {run_id}"
                )
            key = (record_type, record_id)
            if key in record_payloads:
                raise ControlContractError(
                    f"duplicate control record in transaction: {record_type}/{record_id}"
                )
            record_payloads[key] = (
                record,
                (record.to_json() + "\n").encode("utf-8"),
            )

        refs = tuple(
            StoredRecordRef(
                record_type=record_type,
                record_id=record_id,
                relative_path=(
                    Path(_RECORD_DIRECTORIES[record_type]) / f"{record_id}.json"
                ).as_posix(),
                sha256=sha256_bytes(payload),
            )
            for (record_type, record_id), (_, payload) in record_payloads.items()
        )
        manifest = ControlTransactionManifest(
            transaction_id=transaction_id,
            run_id=run_id,
            iteration=iteration,
            created_at=created_at,
            records=refs,
        )
        manifest_bytes = canonical_json_bytes(manifest.to_dict())
        transaction_path = self._transaction_path(transaction_id)

        with exclusive_lock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
        ):
            if transaction_path.exists():
                existing = transaction_path.read_bytes()
                if existing != manifest_bytes:
                    raise LineageConflictError(
                        f"transaction ID {transaction_id} already has different content"
                    )
                return self.load_transaction(transaction_id)

            committed = self._committed_index()
            incoming = set(record_payloads)
            self._validate_dependencies(
                (item[0] for item in record_payloads.values()),
                committed | incoming,
            )

            for ref in manifest.records:
                _, payload = record_payloads[(ref.record_type, ref.record_id)]
                write_immutable(self.control_root / ref.relative_path, payload)

            # The marker is written last. Files left by an interrupted attempt are
            # immutable orphans and are not considered committed until retry succeeds.
            write_immutable(transaction_path, manifest_bytes)
            return self.load_transaction(transaction_id)

    def load_transaction(self, transaction_id: str) -> ControlTransactionManifest:
        transaction_id = validate_id(transaction_id, "transaction_id")
        path = self._transaction_path(transaction_id)
        if not path.exists():
            raise LineageIntegrityError(f"unknown control transaction {transaction_id}")
        manifest = ControlTransactionManifest.from_dict(read_json_object(path))
        if manifest.transaction_id != transaction_id:
            raise LineageIntegrityError("transaction filename and payload ID differ")
        for ref in manifest.records:
            verify_file_hash(self.control_root / ref.relative_path, ref.sha256)
            self._load_record_file(ref.record_type, ref.record_id)
        return manifest

    def is_committed(self, record_type: str, record_id: str) -> bool:
        _validate_record_type(record_type)
        record_id = validate_id(record_id, "record_id")
        return (record_type, record_id) in self._committed_index()

    def load_record(
        self,
        record_type: str,
        record_id: str,
        *,
        require_committed: bool = True,
    ) -> ControlRecord:
        _validate_record_type(record_type)
        record_id = validate_id(record_id, "record_id")
        if require_committed and not self.is_committed(record_type, record_id):
            raise LineageIntegrityError(
                f"control record is not part of a committed transaction: "
                f"{record_type}/{record_id}"
            )
        return self._load_record_file(record_type, record_id)

    def load_evidence(self, evidence_id: str) -> EvidenceRecord:
        record = self.load_record(EvidenceRecord.RECORD_TYPE, evidence_id)
        if not isinstance(record, EvidenceRecord):
            raise LineageIntegrityError("record type mismatch while loading evidence")
        return record

    def load_decision(self, decision_id: str) -> DecisionRecord:
        record = self.load_record(DecisionRecord.RECORD_TYPE, decision_id)
        if not isinstance(record, DecisionRecord):
            raise LineageIntegrityError("record type mismatch while loading decision")
        return record

    def load_transition(self, transition_id: str) -> TransitionRecord:
        record = self.load_record(TransitionRecord.RECORD_TYPE, transition_id)
        if not isinstance(record, TransitionRecord):
            raise LineageIntegrityError("record type mismatch while loading transition")
        return record

    def load_snapshot(self, snapshot_id: str) -> StateSnapshot:
        record = self.load_record(StateSnapshot.RECORD_TYPE, snapshot_id)
        if not isinstance(record, StateSnapshot):
            raise LineageIntegrityError("record type mismatch while loading snapshot")
        return record

    def _transaction_path(self, transaction_id: str) -> Path:
        return self.transactions_root / f"{transaction_id}.json"

    def _record_path(self, record_type: str, record_id: str) -> Path:
        return (
            self.control_root
            / _RECORD_DIRECTORIES[record_type]
            / f"{record_id}.json"
        )

    def _load_record_file(self, record_type: str, record_id: str) -> ControlRecord:
        path = self._record_path(record_type, record_id)
        if not path.exists():
            raise LineageIntegrityError(
                f"missing control record {record_type}/{record_id}"
            )
        record_class = _RECORD_CLASSES[record_type]
        try:
            record = record_class.from_dict(read_json_object(path))
        except ControlContractError as exc:
            raise LineageIntegrityError(
                f"invalid committed control record {record_type}/{record_id}"
            ) from exc
        actual_type, actual_id = _record_identity(record)
        if actual_type != record_type or actual_id != record_id:
            raise LineageIntegrityError(
                f"control record filename and payload differ for {path}"
            )
        return record

    def _committed_index(self) -> set[tuple[str, str]]:
        committed: set[tuple[str, str]] = set()
        for path in sorted(self.transactions_root.glob("*.json")):
            manifest = ControlTransactionManifest.from_dict(read_json_object(path))
            for ref in manifest.records:
                verify_file_hash(self.control_root / ref.relative_path, ref.sha256)
                committed.add((ref.record_type, ref.record_id))
        return committed

    @staticmethod
    def _validate_dependencies(
        records: Iterable[ControlRecord],
        available: set[tuple[str, str]],
    ) -> None:
        for record in records:
            if isinstance(record, EvidenceRecord):
                continue
            for evidence_id in record.evidence_ids:
                if (EvidenceRecord.RECORD_TYPE, evidence_id) not in available:
                    raise LineageIntegrityError(
                        f"record references uncommitted evidence {evidence_id}"
                    )
            if isinstance(record, TransitionRecord) and record.decision_id is not None:
                if (DecisionRecord.RECORD_TYPE, record.decision_id) not in available:
                    raise LineageIntegrityError(
                        f"transition references uncommitted decision "
                        f"{record.decision_id}"
                    )
            if isinstance(record, StateSnapshot):
                decision_id = record.metadata.get("decision_id")
                if decision_id is not None:
                    if not isinstance(decision_id, str):
                        raise LineageIntegrityError(
                            "snapshot metadata.decision_id must be a string"
                        )
                    if (DecisionRecord.RECORD_TYPE, decision_id) not in available:
                        raise LineageIntegrityError(
                            f"snapshot references uncommitted decision {decision_id}"
                        )


def _record_identity(record: ControlRecord) -> tuple[str, str]:
    record_type = record.RECORD_TYPE
    _validate_record_type(record_type)
    field_name = _RECORD_ID_FIELDS[record_type]
    record_id = getattr(record, field_name)
    if not isinstance(record_id, str):
        raise ControlContractError(f"{field_name} must be a string")
    return record_type, validate_id(record_id, field_name)


def _validate_record_type(record_type: str) -> None:
    if record_type not in _RECORD_DIRECTORIES:
        raise ControlContractError(f"unsupported control record_type: {record_type!r}")


def _required_mapping_str(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ControlContractError(f"{key} must be a string")
    return item
