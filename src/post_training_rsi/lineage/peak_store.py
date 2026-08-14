from __future__ import annotations

import math
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
    normalize_timestamp,
    optional_str,
    required_float,
    required_int,
    required_str,
    validate_finite_number,
    validate_id,
    validate_nonnegative_int,
    validate_sha256,
    validate_text,
    validated_record_mapping,
)
from ._io import (
    LineageConflictError,
    LineageIntegrityError,
    canonical_json_bytes,
    exclusive_lock,
    read_json_object,
    replace_atomic,
    write_immutable,
)
from .checkpoint_store import CheckpointBundleStore
from .control_store import (
    LINEAGE_SCHEMA_VERSION,
    ControlRecordStore,
)


@dataclass(frozen=True, slots=True)
class PeakPointer:
    RECORD_TYPE = "peak_pointer"

    run_id: str
    checkpoint_id: str
    previous_checkpoint_id: str | None
    model_id: str
    iteration: int
    score: float
    decision_id: str
    control_transaction_id: str
    checkpoint_bundle_sha256: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        object.__setattr__(
            self,
            "checkpoint_id",
            validate_id(self.checkpoint_id, "checkpoint_id"),
        )
        if self.previous_checkpoint_id is not None:
            object.__setattr__(
                self,
                "previous_checkpoint_id",
                validate_id(
                    self.previous_checkpoint_id,
                    "previous_checkpoint_id",
                ),
            )
        if self.previous_checkpoint_id == self.checkpoint_id:
            raise ValueError("Peak Checkpoint cannot reference itself as previous")
        object.__setattr__(
            self,
            "model_id",
            validate_text(self.model_id, "model_id"),
        )
        validate_nonnegative_int(self.iteration, "iteration")
        validate_finite_number(self.score, "score")
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
        validate_sha256(self.checkpoint_bundle_sha256)
        object.__setattr__(self, "updated_at", normalize_timestamp(self.updated_at))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": LINEAGE_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "run_id": self.run_id,
            "checkpoint_id": self.checkpoint_id,
            "previous_checkpoint_id": self.previous_checkpoint_id,
            "model_id": self.model_id,
            "iteration": self.iteration,
            "score": self.score,
            "decision_id": self.decision_id,
            "control_transaction_id": self.control_transaction_id,
            "checkpoint_bundle_sha256": self.checkpoint_bundle_sha256,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> PeakPointer:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            {
                "schema_version",
                "record_type",
                "run_id",
                "checkpoint_id",
                "previous_checkpoint_id",
                "model_id",
                "iteration",
                "score",
                "decision_id",
                "control_transaction_id",
                "checkpoint_bundle_sha256",
                "updated_at",
            },
            LINEAGE_SCHEMA_VERSION,
        )
        return cls(
            run_id=required_str(data, "run_id"),
            checkpoint_id=required_str(data, "checkpoint_id"),
            previous_checkpoint_id=optional_str(data, "previous_checkpoint_id"),
            model_id=required_str(data, "model_id"),
            iteration=required_int(data, "iteration"),
            score=required_float(data, "score"),
            decision_id=required_str(data, "decision_id"),
            control_transaction_id=required_str(data, "control_transaction_id"),
            checkpoint_bundle_sha256=required_str(
                data,
                "checkpoint_bundle_sha256",
            ),
            updated_at=required_str(data, "updated_at"),
        )


class PeakPointerStore:
    """Compare-and-swap the accepted Peak after verifying Decision and bundle links."""

    def __init__(
        self,
        root: str | Path,
        control_store: ControlRecordStore,
        checkpoint_store: CheckpointBundleStore,
        *,
        lock_timeout_seconds: float = 2.0,
        poll_interval_seconds: float = 0.01,
    ) -> None:
        self.root = Path(root)
        self.pointer_path = self.root / "peak_checkpoint.json"
        self.history_root = self.root / "peak_history"
        self.history_root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.root / ".peak.lock"
        self.control_store = control_store
        self.checkpoint_store = checkpoint_store
        self.lock_timeout_seconds = lock_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def compare_and_swap(
        self,
        pointer: PeakPointer,
        *,
        expected_previous_checkpoint_id: str | None,
    ) -> PeakPointer:
        if expected_previous_checkpoint_id is not None:
            expected_previous_checkpoint_id = validate_id(
                expected_previous_checkpoint_id,
                "expected_previous_checkpoint_id",
            )
        pointer_bytes = canonical_json_bytes(pointer.to_dict())

        with exclusive_lock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
        ):
            current = self._load_unlocked(verify_links=True)
            if current is not None and canonical_json_bytes(current.to_dict()) == pointer_bytes:
                return current

            current_checkpoint_id = current.checkpoint_id if current else None
            if current_checkpoint_id != expected_previous_checkpoint_id:
                raise LineageConflictError(
                    "Peak compare-and-swap failed: expected "
                    f"{expected_previous_checkpoint_id!r}, found "
                    f"{current_checkpoint_id!r}"
                )
            if pointer.previous_checkpoint_id != expected_previous_checkpoint_id:
                raise LineageConflictError(
                    "PeakPointer.previous_checkpoint_id does not match the expected Peak"
                )
            self._verify_links(pointer)

            history_path = self.history_root / (
                f"iter-{pointer.iteration:06d}-{pointer.checkpoint_id}.json"
            )
            write_immutable(history_path, pointer_bytes)
            replace_atomic(self.pointer_path, pointer_bytes)
            return self._load_unlocked(verify_links=True) or pointer

    def load(self) -> PeakPointer | None:
        return self._load_unlocked(verify_links=True)

    def _load_unlocked(self, *, verify_links: bool) -> PeakPointer | None:
        if not self.pointer_path.exists():
            return None
        pointer = PeakPointer.from_dict(read_json_object(self.pointer_path))
        if verify_links:
            self._verify_links(pointer)
        return pointer

    def _verify_links(self, pointer: PeakPointer) -> None:
        transaction = self.control_store.load_transaction(
            pointer.control_transaction_id
        )
        if transaction.run_id != pointer.run_id:
            raise LineageIntegrityError(
                "Peak pointer and control transaction Run IDs differ"
            )
        if transaction.iteration != pointer.iteration:
            raise LineageIntegrityError(
                "Peak pointer and control transaction iterations differ"
            )
        if not any(
            ref.record_type == DecisionRecord.RECORD_TYPE
            and ref.record_id == pointer.decision_id
            for ref in transaction.records
        ):
            raise LineageIntegrityError(
                "Peak promotion Decision is not committed by the referenced transaction"
            )

        decision = self.control_store.load_decision(pointer.decision_id)
        if decision.action is not DecisionAction.PROMOTE:
            raise LineageIntegrityError("Peak pointer requires a PROMOTE Decision")
        if decision.subject_type is not DecisionSubject.CHECKPOINT:
            raise LineageIntegrityError(
                "Peak promotion Decision must target a Checkpoint"
            )
        if decision.subject_id != pointer.checkpoint_id:
            raise LineageIntegrityError(
                "Peak promotion Decision targets a different Checkpoint"
            )
        if decision.run_id != pointer.run_id or decision.iteration != pointer.iteration:
            raise LineageIntegrityError(
                "Peak pointer and promotion Decision lineage differ"
            )

        bundle = self.checkpoint_store.load(pointer.checkpoint_id)
        if bundle.manifest.run_id != pointer.run_id:
            raise LineageIntegrityError("Peak pointer and Checkpoint Run IDs differ")
        if bundle.manifest.iteration != pointer.iteration:
            raise LineageIntegrityError(
                "Peak pointer and Checkpoint iterations differ"
            )
        if bundle.manifest.control_transaction_id != pointer.control_transaction_id:
            raise LineageIntegrityError(
                "Peak pointer and Checkpoint reference different control transactions"
            )
        if bundle.manifest.manifest_sha256 != pointer.checkpoint_bundle_sha256:
            raise LineageIntegrityError(
                "Peak pointer Checkpoint bundle hash is invalid"
            )
        if bundle.lineage_manifest.model_id != pointer.model_id:
            raise LineageIntegrityError("Peak pointer and lineage model IDs differ")
        if not math.isclose(
            bundle.lineage_manifest.benchmark_score,
            pointer.score,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise LineageIntegrityError(
                "Peak pointer score differs from the committed lineage score"
            )
