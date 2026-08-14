from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..control_plane import ControlState, JSONValue
from ..control_plane.validation import (
    normalize_timestamp,
    optional_str,
    required_enum,
    required_int,
    required_str,
    validate_id,
    validate_nonnegative_int,
    validate_sha256,
    validated_record_mapping,
)
from ..lineage._io import (
    LineageConflictError,
    LineageIntegrityError,
    canonical_json_bytes,
    exclusive_lock,
    read_json_object,
    replace_atomic,
    write_immutable,
)

COEVOLUTION_RUN_SCHEMA_VERSION = "post-training-rsi.coevolution-run/v1"


@dataclass(frozen=True, slots=True)
class CoEvolutionRunMetadata:
    RECORD_TYPE = "coevolution_run"

    run_id: str
    config_sha256: str
    revision: int
    state: ControlState
    current_cycle: int
    completed_cycles: int
    active_checkpoint_id: str
    active_model_score: float
    active_harness_id: str
    active_harness_score: float
    latest_snapshot_id: str
    latest_transaction_id: str
    pending_approval_request_id: str | None
    pending_approval_request_sha256: str | None
    pending_approval_subject: str | None
    status: str
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_sha256(self.config_sha256)
        validate_nonnegative_int(self.revision, "revision")
        validate_nonnegative_int(self.current_cycle, "current_cycle")
        validate_nonnegative_int(self.completed_cycles, "completed_cycles")
        if self.current_cycle < 1:
            raise ValueError("current_cycle must be at least 1")
        if self.completed_cycles > self.current_cycle:
            raise ValueError("completed_cycles cannot exceed current_cycle")
        for name in (
            "active_checkpoint_id",
            "active_harness_id",
            "latest_snapshot_id",
            "latest_transaction_id",
        ):
            object.__setattr__(self, name, validate_id(getattr(self, name), name))
        for name in ("active_model_score", "active_harness_score"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number")
            numeric = float(value)
            if not 0.0 <= numeric <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
            object.__setattr__(self, name, numeric)
        for name in (
            "pending_approval_request_id",
            "pending_approval_request_sha256",
            "pending_approval_subject",
        ):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, str):
                    raise TypeError(f"{name} must be a string or null")
                if name == "pending_approval_request_sha256":
                    validate_sha256(value)
                else:
                    object.__setattr__(self, name, validate_id(value, name))
        pending_values = (
            self.pending_approval_request_id,
            self.pending_approval_request_sha256,
            self.pending_approval_subject,
        )
        if any(value is None for value in pending_values) and any(
            value is not None for value in pending_values
        ):
            raise ValueError("pending approval fields must be all set or all null")
        if not self.status or not self.status.strip():
            raise ValueError("status must be non-empty")
        object.__setattr__(self, "created_at", normalize_timestamp(self.created_at))
        object.__setattr__(self, "updated_at", normalize_timestamp(self.updated_at))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": COEVOLUTION_RUN_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "run_id": self.run_id,
            "config_sha256": self.config_sha256,
            "revision": self.revision,
            "state": self.state.value,
            "current_cycle": self.current_cycle,
            "completed_cycles": self.completed_cycles,
            "active_checkpoint_id": self.active_checkpoint_id,
            "active_model_score": self.active_model_score,
            "active_harness_id": self.active_harness_id,
            "active_harness_score": self.active_harness_score,
            "latest_snapshot_id": self.latest_snapshot_id,
            "latest_transaction_id": self.latest_transaction_id,
            "pending_approval_request_id": self.pending_approval_request_id,
            "pending_approval_request_sha256": self.pending_approval_request_sha256,
            "pending_approval_subject": self.pending_approval_subject,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> CoEvolutionRunMetadata:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            {
                "schema_version",
                "record_type",
                "run_id",
                "config_sha256",
                "revision",
                "state",
                "current_cycle",
                "completed_cycles",
                "active_checkpoint_id",
                "active_model_score",
                "active_harness_id",
                "active_harness_score",
                "latest_snapshot_id",
                "latest_transaction_id",
                "pending_approval_request_id",
                "pending_approval_request_sha256",
                "pending_approval_subject",
                "status",
                "created_at",
                "updated_at",
            },
            COEVOLUTION_RUN_SCHEMA_VERSION,
        )
        return cls(
            run_id=required_str(data, "run_id"),
            config_sha256=required_str(data, "config_sha256"),
            revision=required_int(data, "revision"),
            state=required_enum(data, "state", ControlState),
            current_cycle=required_int(data, "current_cycle"),
            completed_cycles=required_int(data, "completed_cycles"),
            active_checkpoint_id=required_str(data, "active_checkpoint_id"),
            active_model_score=_required_float(data, "active_model_score"),
            active_harness_id=required_str(data, "active_harness_id"),
            active_harness_score=_required_float(data, "active_harness_score"),
            latest_snapshot_id=required_str(data, "latest_snapshot_id"),
            latest_transaction_id=required_str(data, "latest_transaction_id"),
            pending_approval_request_id=optional_str(
                data,
                "pending_approval_request_id",
            ),
            pending_approval_request_sha256=optional_str(
                data,
                "pending_approval_request_sha256",
            ),
            pending_approval_subject=optional_str(
                data,
                "pending_approval_subject",
            ),
            status=required_str(data, "status"),
            created_at=required_str(data, "created_at"),
            updated_at=required_str(data, "updated_at"),
        )


class CoEvolutionRunStore:
    """Atomic compare-and-swap Run metadata with immutable revision history."""

    def __init__(
        self,
        root: str | Path,
        *,
        lock_timeout_seconds: float = 2.0,
        poll_interval_seconds: float = 0.01,
    ) -> None:
        self.root = Path(root)
        self.run_root = self.root / "coevolution"
        self.history_root = self.run_root / "history"
        self.pointer_path = self.run_root / "run.json"
        self.lock_path = self.run_root / ".run.lock"
        self.history_root.mkdir(parents=True, exist_ok=True)
        self.lock_timeout_seconds = lock_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def create(self, metadata: CoEvolutionRunMetadata) -> CoEvolutionRunMetadata:
        if metadata.revision != 0:
            raise ValueError("new Co-Evolution Run must begin at revision 0")
        payload = canonical_json_bytes(metadata.to_dict())
        with exclusive_lock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
        ):
            if self.pointer_path.exists():
                existing = self._load_unlocked()
                if canonical_json_bytes(existing.to_dict()) == payload:
                    return existing
                raise LineageConflictError(
                    "Co-Evolution Run metadata already exists with different content"
                )
            write_immutable(self._history_path(metadata.revision), payload)
            replace_atomic(self.pointer_path, payload)
            return self._load_unlocked()

    def compare_and_swap(
        self,
        metadata: CoEvolutionRunMetadata,
        *,
        expected_revision: int,
    ) -> CoEvolutionRunMetadata:
        validate_nonnegative_int(expected_revision, "expected_revision")
        if metadata.revision != expected_revision + 1:
            raise ValueError(
                "next Co-Evolution Run revision must equal expected_revision + 1"
            )
        payload = canonical_json_bytes(metadata.to_dict())
        with exclusive_lock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
        ):
            current = self._load_unlocked()
            if canonical_json_bytes(current.to_dict()) == payload:
                return current
            if current.revision != expected_revision:
                raise LineageConflictError(
                    "Co-Evolution Run compare-and-swap failed: expected revision "
                    f"{expected_revision}, found {current.revision}"
                )
            if metadata.run_id != current.run_id:
                raise LineageConflictError("Co-Evolution Run ID cannot change")
            if metadata.config_sha256 != current.config_sha256:
                raise LineageConflictError(
                    "Co-Evolution immutable configuration hash cannot change"
                )
            if metadata.created_at != current.created_at:
                raise LineageConflictError(
                    "Co-Evolution Run creation timestamp cannot change"
                )
            if metadata.completed_cycles < current.completed_cycles:
                raise LineageConflictError("completed_cycles cannot move backward")
            if metadata.current_cycle < current.current_cycle:
                raise LineageConflictError("current_cycle cannot move backward")
            history = self._history_path(metadata.revision)
            write_immutable(history, payload)
            replace_atomic(self.pointer_path, payload)
            return self._load_unlocked()

    def load(
        self,
        *,
        expected_run_id: str | None = None,
        expected_config_sha256: str | None = None,
    ) -> CoEvolutionRunMetadata:
        metadata = self._load_unlocked()
        if expected_run_id is not None:
            expected_run_id = validate_id(expected_run_id, "expected_run_id")
            if metadata.run_id != expected_run_id:
                raise LineageIntegrityError(
                    "Co-Evolution workspace belongs to a different Run ID"
                )
        if expected_config_sha256 is not None:
            validate_sha256(expected_config_sha256)
            if metadata.config_sha256 != expected_config_sha256:
                raise LineageIntegrityError(
                    "Co-Evolution workspace configuration hash mismatch"
                )
        return metadata

    def exists(self) -> bool:
        return self.pointer_path.exists()

    def _load_unlocked(self) -> CoEvolutionRunMetadata:
        if not self.pointer_path.exists():
            raise LineageIntegrityError("Co-Evolution Run metadata does not exist")
        metadata = CoEvolutionRunMetadata.from_dict(
            read_json_object(self.pointer_path)
        )
        history = self._history_path(metadata.revision)
        if not history.exists():
            raise LineageIntegrityError(
                "Co-Evolution Run revision history is missing"
            )
        if history.read_bytes() != self.pointer_path.read_bytes():
            raise LineageIntegrityError(
                "Co-Evolution Run pointer and immutable revision differ"
            )
        return metadata

    def _history_path(self, revision: int) -> Path:
        return self.history_root / f"revision-{revision:06d}.json"


def _required_float(value: Mapping[str, object], key: str) -> float:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise TypeError(f"{key} must be a number")
    return float(item)
