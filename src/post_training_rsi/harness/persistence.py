from __future__ import annotations

import math
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..control_plane import DecisionAction, DecisionRecord, DecisionSubject, JSONValue
from ..control_plane.validation import (
    normalize_timestamp,
    required_float,
    required_int,
    required_str,
    validate_finite_number,
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
    replace_directory_atomic,
    sha256_bytes,
    verify_file_hash,
    write_immutable,
)
from ..lineage.control_store import ControlRecordStore
from .outer_loop import HarnessSpec

HARNESS_SCHEMA_VERSION = "post-training-rsi.harness/v1"
_ALLOWED_STATUSES = {"ACTIVE", "CANDIDATE", "REJECTED", "AWAITING_APPROVAL"}


@dataclass(frozen=True, slots=True)
class HarnessSnapshotManifest:
    RECORD_TYPE = "harness_snapshot"

    harness_id: str
    run_id: str
    cycle: int
    parent_harness_id: str | None
    harness_sha256: str
    score: float
    status: str
    control_transaction_id: str
    created_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "harness_id",
            validate_id(self.harness_id, "harness_id"),
        )
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.cycle, "cycle")
        if self.parent_harness_id is not None:
            object.__setattr__(
                self,
                "parent_harness_id",
                validate_id(self.parent_harness_id, "parent_harness_id"),
            )
        if self.parent_harness_id == self.harness_id:
            raise ValueError("Harness snapshot cannot be its own parent")
        validate_sha256(self.harness_sha256)
        validate_finite_number(self.score, "score")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("Harness score must be in [0, 1]")
        if self.status not in _ALLOWED_STATUSES:
            raise ValueError(f"unsupported Harness status: {self.status!r}")
        object.__setattr__(
            self,
            "control_transaction_id",
            validate_id(self.control_transaction_id, "control_transaction_id"),
        )
        object.__setattr__(self, "created_at", normalize_timestamp(self.created_at))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": HARNESS_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "harness_id": self.harness_id,
            "run_id": self.run_id,
            "cycle": self.cycle,
            "parent_harness_id": self.parent_harness_id,
            "harness_sha256": self.harness_sha256,
            "score": self.score,
            "status": self.status,
            "control_transaction_id": self.control_transaction_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> HarnessSnapshotManifest:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            {
                "schema_version",
                "record_type",
                "harness_id",
                "run_id",
                "cycle",
                "parent_harness_id",
                "harness_sha256",
                "score",
                "status",
                "control_transaction_id",
                "created_at",
            },
            HARNESS_SCHEMA_VERSION,
        )
        parent = data["parent_harness_id"]
        if parent is not None and not isinstance(parent, str):
            raise ValueError("parent_harness_id must be a string or null")
        return cls(
            harness_id=required_str(data, "harness_id"),
            run_id=required_str(data, "run_id"),
            cycle=required_int(data, "cycle"),
            parent_harness_id=parent,
            harness_sha256=required_str(data, "harness_sha256"),
            score=required_float(data, "score"),
            status=required_str(data, "status"),
            control_transaction_id=required_str(data, "control_transaction_id"),
            created_at=required_str(data, "created_at"),
        )

    @property
    def manifest_sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.to_dict()))


@dataclass(frozen=True, slots=True)
class HarnessSnapshotBundle:
    spec: HarnessSpec
    manifest: HarnessSnapshotManifest


class HarnessSnapshotStore:
    """Commit immutable Harness content bound to a control transaction."""

    def __init__(
        self,
        root: str | Path,
        control_store: ControlRecordStore,
        *,
        lock_timeout_seconds: float = 2.0,
        poll_interval_seconds: float = 0.01,
    ) -> None:
        self.root = Path(root)
        self.snapshots_root = self.root / "harness" / "snapshots"
        self.snapshots_root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.snapshots_root / ".snapshot.lock"
        self.control_store = control_store
        self.lock_timeout_seconds = lock_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def commit(
        self,
        spec: HarnessSpec,
        *,
        run_id: str,
        cycle: int,
        score: float,
        status: str,
        control_transaction_id: str,
        created_at: str,
    ) -> HarnessSnapshotBundle:
        transaction = self.control_store.load_transaction(control_transaction_id)
        if transaction.run_id != run_id:
            raise LineageIntegrityError(
                "Harness snapshot and control transaction Run IDs differ"
            )
        if spec.content_sha256 != sha256_bytes(spec.to_json().encode("utf-8")):
            raise LineageIntegrityError("Harness content hash is internally inconsistent")
        manifest = HarnessSnapshotManifest(
            harness_id=spec.harness_id,
            run_id=run_id,
            cycle=cycle,
            parent_harness_id=spec.parent_harness_id,
            harness_sha256=spec.content_sha256,
            score=score,
            status=status,
            control_transaction_id=control_transaction_id,
            created_at=created_at,
        )
        # Store the exact canonical bytes used by HarnessSpec.content_sha256.
        # canonical_json_bytes adds a trailing newline and therefore belongs
        # to manifest files, not the content-addressed Harness payload.
        spec_bytes = spec.to_json().encode("utf-8")
        manifest_bytes = canonical_json_bytes(manifest.to_dict())
        final_directory = self._directory(spec.harness_id)

        with exclusive_lock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
        ):
            if final_directory.exists():
                existing = self.load(spec.harness_id)
                if (
                    existing.spec != spec
                    or canonical_json_bytes(existing.manifest.to_dict())
                    != manifest_bytes
                ):
                    raise LineageConflictError(
                        f"Harness ID {spec.harness_id} already has different content"
                    )
                return existing

            staging = self.snapshots_root / (
                f".staging-{spec.harness_id}-{time.monotonic_ns()}"
            )
            staging.mkdir(mode=0o700)
            try:
                write_immutable(staging / "harness.json", spec_bytes)
                write_immutable(staging / "snapshot_manifest.json", manifest_bytes)
                replace_directory_atomic(staging, final_directory)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        return self.load(spec.harness_id)

    def load(self, harness_id: str) -> HarnessSnapshotBundle:
        harness_id = validate_id(harness_id, "harness_id")
        directory = self._directory(harness_id)
        if not directory.is_dir() or directory.is_symlink():
            raise LineageIntegrityError(f"unknown Harness snapshot {harness_id}")
        spec_payload = read_json_object(directory / "harness.json")
        manifest = HarnessSnapshotManifest.from_dict(
            read_json_object(directory / "snapshot_manifest.json")
        )
        spec = HarnessSpec.from_dict(spec_payload)
        if spec.harness_id != harness_id or manifest.harness_id != harness_id:
            raise LineageIntegrityError(
                "Harness directory, content, and manifest IDs differ"
            )
        verify_file_hash(directory / "harness.json", manifest.harness_sha256)
        if spec.content_sha256 != manifest.harness_sha256:
            raise LineageIntegrityError("Harness content SHA-256 mismatch")
        transaction = self.control_store.load_transaction(
            manifest.control_transaction_id
        )
        if transaction.run_id != manifest.run_id:
            raise LineageIntegrityError(
                "Harness manifest and control transaction Run IDs differ"
            )
        return HarnessSnapshotBundle(spec=spec, manifest=manifest)

    def _directory(self, harness_id: str) -> Path:
        return self.snapshots_root / harness_id


@dataclass(frozen=True, slots=True)
class HarnessPointer:
    RECORD_TYPE = "active_harness_pointer"

    run_id: str
    harness_id: str
    previous_harness_id: str | None
    cycle: int
    score: float
    decision_id: str
    control_transaction_id: str
    snapshot_manifest_sha256: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        object.__setattr__(
            self,
            "harness_id",
            validate_id(self.harness_id, "harness_id"),
        )
        if self.previous_harness_id is not None:
            object.__setattr__(
                self,
                "previous_harness_id",
                validate_id(self.previous_harness_id, "previous_harness_id"),
            )
        if self.previous_harness_id == self.harness_id:
            raise ValueError("Harness pointer cannot reference itself as previous")
        validate_nonnegative_int(self.cycle, "cycle")
        validate_finite_number(self.score, "score")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("Harness pointer score must be in [0, 1]")
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
        validate_sha256(self.snapshot_manifest_sha256)
        object.__setattr__(self, "updated_at", normalize_timestamp(self.updated_at))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": HARNESS_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "run_id": self.run_id,
            "harness_id": self.harness_id,
            "previous_harness_id": self.previous_harness_id,
            "cycle": self.cycle,
            "score": self.score,
            "decision_id": self.decision_id,
            "control_transaction_id": self.control_transaction_id,
            "snapshot_manifest_sha256": self.snapshot_manifest_sha256,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> HarnessPointer:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            {
                "schema_version",
                "record_type",
                "run_id",
                "harness_id",
                "previous_harness_id",
                "cycle",
                "score",
                "decision_id",
                "control_transaction_id",
                "snapshot_manifest_sha256",
                "updated_at",
            },
            HARNESS_SCHEMA_VERSION,
        )
        previous = data["previous_harness_id"]
        if previous is not None and not isinstance(previous, str):
            raise ValueError("previous_harness_id must be a string or null")
        return cls(
            run_id=required_str(data, "run_id"),
            harness_id=required_str(data, "harness_id"),
            previous_harness_id=previous,
            cycle=required_int(data, "cycle"),
            score=required_float(data, "score"),
            decision_id=required_str(data, "decision_id"),
            control_transaction_id=required_str(data, "control_transaction_id"),
            snapshot_manifest_sha256=required_str(
                data,
                "snapshot_manifest_sha256",
            ),
            updated_at=required_str(data, "updated_at"),
        )


class HarnessPointerStore:
    """Compare-and-swap the accepted Harness after verifying ACCEPT evidence."""

    def __init__(
        self,
        root: str | Path,
        control_store: ControlRecordStore,
        snapshot_store: HarnessSnapshotStore,
        *,
        lock_timeout_seconds: float = 2.0,
        poll_interval_seconds: float = 0.01,
    ) -> None:
        self.root = Path(root)
        self.pointer_path = self.root / "active_harness.json"
        self.history_root = self.root / "harness" / "history"
        self.history_root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.root / "harness" / ".active.lock"
        self.control_store = control_store
        self.snapshot_store = snapshot_store
        self.lock_timeout_seconds = lock_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def compare_and_swap(
        self,
        pointer: HarnessPointer,
        *,
        expected_previous_harness_id: str | None,
    ) -> HarnessPointer:
        if expected_previous_harness_id is not None:
            expected_previous_harness_id = validate_id(
                expected_previous_harness_id,
                "expected_previous_harness_id",
            )
        payload = canonical_json_bytes(pointer.to_dict())
        with exclusive_lock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
        ):
            current = self._load_unlocked(verify_links=True)
            if current is not None and canonical_json_bytes(current.to_dict()) == payload:
                return current
            current_id = current.harness_id if current is not None else None
            if current_id != expected_previous_harness_id:
                raise LineageConflictError(
                    "Harness compare-and-swap failed: expected "
                    f"{expected_previous_harness_id!r}, found {current_id!r}"
                )
            if pointer.previous_harness_id != expected_previous_harness_id:
                raise LineageConflictError(
                    "HarnessPointer.previous_harness_id does not match expected active"
                )
            if current is not None:
                if pointer.cycle < current.cycle:
                    raise LineageConflictError("Harness pointer cycle cannot move backward")
                if pointer.score <= current.score:
                    raise LineageConflictError(
                        "accepted Harness pointer score must strictly increase"
                    )
            self._verify_links(pointer)
            history = self.history_root / (
                f"cycle-{pointer.cycle:06d}-{pointer.harness_id}.json"
            )
            write_immutable(history, payload)
            replace_atomic(self.pointer_path, payload)
            return self._load_unlocked(verify_links=True) or pointer

    def load(self) -> HarnessPointer | None:
        return self._load_unlocked(verify_links=True)

    def _load_unlocked(self, *, verify_links: bool) -> HarnessPointer | None:
        if not self.pointer_path.exists():
            return None
        pointer = HarnessPointer.from_dict(read_json_object(self.pointer_path))
        if verify_links:
            self._verify_links(pointer)
        return pointer

    def _verify_links(self, pointer: HarnessPointer) -> None:
        transaction = self.control_store.load_transaction(
            pointer.control_transaction_id
        )
        if transaction.run_id != pointer.run_id:
            raise LineageIntegrityError(
                "Harness pointer and control transaction Run IDs differ"
            )
        if not any(
            ref.record_type == DecisionRecord.RECORD_TYPE
            and ref.record_id == pointer.decision_id
            for ref in transaction.records
        ):
            raise LineageIntegrityError(
                "Harness ACCEPT Decision is not in the referenced transaction"
            )
        decision = self.control_store.load_decision(pointer.decision_id)
        if decision.action is not DecisionAction.ACCEPT:
            raise LineageIntegrityError("Harness pointer requires an ACCEPT Decision")
        if decision.subject_type is not DecisionSubject.HARNESS:
            raise LineageIntegrityError("Harness pointer Decision must target a Harness")
        if decision.subject_id != pointer.harness_id:
            raise LineageIntegrityError(
                "Harness pointer Decision targets a different Harness"
            )
        if decision.run_id != pointer.run_id:
            raise LineageIntegrityError(
                "Harness pointer and Decision Run IDs differ"
            )
        bundle = self.snapshot_store.load(pointer.harness_id)
        if bundle.manifest.run_id != pointer.run_id:
            raise LineageIntegrityError(
                "Harness pointer and snapshot Run IDs differ"
            )
        if bundle.manifest.cycle != pointer.cycle:
            raise LineageIntegrityError(
                "Harness pointer and snapshot cycles differ"
            )
        if bundle.manifest.control_transaction_id != pointer.control_transaction_id:
            raise LineageIntegrityError(
                "Harness pointer and snapshot reference different transactions"
            )
        if bundle.manifest.manifest_sha256 != pointer.snapshot_manifest_sha256:
            raise LineageIntegrityError("Harness snapshot manifest hash mismatch")
        if not math.isclose(
            bundle.manifest.score,
            pointer.score,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise LineageIntegrityError(
                "Harness pointer score differs from snapshot score"
            )
