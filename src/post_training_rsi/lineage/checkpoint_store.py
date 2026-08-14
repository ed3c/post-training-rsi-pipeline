from __future__ import annotations

import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

from ..control_plane import JSONValue
from ..control_plane.validation import (
    ControlContractError,
    normalize_json_object,
    normalize_timestamp,
    required_int,
    required_json_object,
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
    replace_directory_atomic,
    sha256_bytes,
    sha256_path,
    verify_file_hash,
    write_immutable,
)
from .control_store import (
    LINEAGE_SCHEMA_VERSION,
    ControlRecordStore,
    ControlTransactionManifest,
)
from .manifest import LineageManifest


@dataclass(frozen=True, slots=True)
class CheckpointBundleManifest:
    RECORD_TYPE = "checkpoint_bundle"

    checkpoint_id: str
    run_id: str
    iteration: int
    artifact_uri: str
    artifact_sha256: str
    checkpoint_payload_sha256: str
    lineage_manifest_sha256: str
    control_transaction_id: str
    created_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "checkpoint_id",
            validate_id(self.checkpoint_id, "checkpoint_id"),
        )
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        validate_nonnegative_int(self.iteration, "iteration")
        object.__setattr__(
            self,
            "artifact_uri",
            validate_text(self.artifact_uri, "artifact_uri"),
        )
        validate_sha256(self.artifact_sha256)
        validate_sha256(self.checkpoint_payload_sha256)
        validate_sha256(self.lineage_manifest_sha256)
        object.__setattr__(
            self,
            "control_transaction_id",
            validate_id(self.control_transaction_id, "control_transaction_id"),
        )
        object.__setattr__(self, "created_at", normalize_timestamp(self.created_at))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": LINEAGE_SCHEMA_VERSION,
            "record_type": self.RECORD_TYPE,
            "checkpoint_id": self.checkpoint_id,
            "run_id": self.run_id,
            "iteration": self.iteration,
            "artifact_uri": self.artifact_uri,
            "artifact_sha256": self.artifact_sha256,
            "checkpoint_payload_sha256": self.checkpoint_payload_sha256,
            "lineage_manifest_sha256": self.lineage_manifest_sha256,
            "control_transaction_id": self.control_transaction_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> CheckpointBundleManifest:
        data = validated_record_mapping(
            value,
            cls.RECORD_TYPE,
            {
                "schema_version",
                "record_type",
                "checkpoint_id",
                "run_id",
                "iteration",
                "artifact_uri",
                "artifact_sha256",
                "checkpoint_payload_sha256",
                "lineage_manifest_sha256",
                "control_transaction_id",
                "created_at",
            },
            LINEAGE_SCHEMA_VERSION,
        )
        return cls(
            checkpoint_id=required_str(data, "checkpoint_id"),
            run_id=required_str(data, "run_id"),
            iteration=required_int(data, "iteration"),
            artifact_uri=required_str(data, "artifact_uri"),
            artifact_sha256=required_str(data, "artifact_sha256"),
            checkpoint_payload_sha256=required_str(
                data, "checkpoint_payload_sha256"
            ),
            lineage_manifest_sha256=required_str(
                data, "lineage_manifest_sha256"
            ),
            control_transaction_id=required_str(data, "control_transaction_id"),
            created_at=required_str(data, "created_at"),
        )

    @property
    def manifest_sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.to_dict()))


@dataclass(frozen=True, slots=True)
class CheckpointBundle:
    manifest: CheckpointBundleManifest
    checkpoint_payload: dict[str, JSONValue]
    lineage_manifest: LineageManifest
    control_transaction: ControlTransactionManifest

    @property
    def checkpoint(self) -> dict[str, JSONValue]:
        return self.checkpoint_payload

    @property
    def lineage(self) -> LineageManifest:
        return self.lineage_manifest

    @property
    def manifest_sha256(self) -> str:
        return self.manifest.manifest_sha256


class CheckpointBundleStore:
    """Atomically commit Checkpoint metadata, lineage, and artifact integrity."""

    def __init__(
        self,
        root: str | Path,
        control_store: ControlRecordStore,
        *,
        lock_timeout_seconds: float = 2.0,
        poll_interval_seconds: float = 0.01,
    ) -> None:
        self.root = Path(root)
        self.checkpoints_root = self.root / "checkpoints"
        self.checkpoints_root.mkdir(parents=True, exist_ok=True)
        self.control_store = control_store
        self.lock_path = self.checkpoints_root / ".bundle.lock"
        self.lock_timeout_seconds = lock_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def commit(
        self,
        *,
        checkpoint_id: str,
        run_id: str,
        iteration: int,
        checkpoint_payload: Mapping[str, object],
        lineage_manifest: LineageManifest,
        artifact_path: str | Path,
        artifact_uri: str,
        control_transaction_id: str,
        created_at: str,
    ) -> CheckpointBundle:
        checkpoint_id = validate_id(checkpoint_id, "checkpoint_id")
        run_id = validate_id(run_id, "run_id")
        validate_nonnegative_int(iteration, "iteration")
        created_at = normalize_timestamp(created_at)
        artifact_uri = validate_text(artifact_uri, "artifact_uri")
        control_transaction_id = validate_id(
            control_transaction_id,
            "control_transaction_id",
        )

        control_transaction = self.control_store.load_transaction(
            control_transaction_id
        )
        if control_transaction.run_id != run_id:
            raise LineageIntegrityError(
                "Checkpoint bundle and control transaction belong to different Runs"
            )
        if control_transaction.iteration != iteration:
            raise LineageIntegrityError(
                "Checkpoint bundle and control transaction iterations differ"
            )

        normalized_checkpoint = normalize_json_object(
            checkpoint_payload,
            "checkpoint_payload",
        )
        payload_checkpoint_id = normalized_checkpoint.get("checkpoint_id")
        if payload_checkpoint_id != checkpoint_id:
            raise ControlContractError(
                "checkpoint_payload.checkpoint_id must match the bundle Checkpoint ID"
            )

        _validate_lineage_manifest(
            lineage_manifest,
            checkpoint_id=checkpoint_id,
            iteration=iteration,
        )
        artifact_sha256 = sha256_path(artifact_path)
        payload_artifact_hash = normalized_checkpoint.get("artifact_sha256")
        if (
            payload_artifact_hash is not None
            and payload_artifact_hash != artifact_sha256
        ):
            raise LineageIntegrityError(
                "checkpoint_payload artifact hash does not match the artifact bytes"
            )

        checkpoint_bytes = canonical_json_bytes(normalized_checkpoint)
        lineage_bytes = canonical_json_bytes(lineage_manifest.to_dict())
        manifest = CheckpointBundleManifest(
            checkpoint_id=checkpoint_id,
            run_id=run_id,
            iteration=iteration,
            artifact_uri=artifact_uri,
            artifact_sha256=artifact_sha256,
            checkpoint_payload_sha256=sha256_bytes(checkpoint_bytes),
            lineage_manifest_sha256=sha256_bytes(lineage_bytes),
            control_transaction_id=control_transaction_id,
            created_at=created_at,
        )
        manifest_bytes = canonical_json_bytes(manifest.to_dict())
        final_directory = self._bundle_directory(checkpoint_id)

        with exclusive_lock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
        ):
            if final_directory.exists():
                existing = self.load(checkpoint_id, artifact_path=artifact_path)
                if (
                    canonical_json_bytes(existing.manifest.to_dict())
                    != manifest_bytes
                    or canonical_json_bytes(existing.checkpoint_payload)
                    != checkpoint_bytes
                    or canonical_json_bytes(existing.lineage_manifest.to_dict())
                    != lineage_bytes
                ):
                    raise LineageConflictError(
                        f"Checkpoint ID {checkpoint_id} already has different content"
                    )
                return existing

            staging = self.checkpoints_root / (
                f".staging-{checkpoint_id}-{time.monotonic_ns()}"
            )
            staging.mkdir(mode=0o700)
            try:
                write_immutable(staging / "checkpoint.json", checkpoint_bytes)
                write_immutable(
                    staging / "lineage_manifest.json",
                    lineage_bytes,
                )
                # Written last inside the directory; the directory rename is the
                # externally visible commit point.
                write_immutable(staging / "bundle_manifest.json", manifest_bytes)
                replace_directory_atomic(staging, final_directory)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise

        return self.load(checkpoint_id, artifact_path=artifact_path)

    def load(
        self,
        checkpoint_id: str,
        *,
        artifact_path: str | Path | None = None,
    ) -> CheckpointBundle:
        checkpoint_id = validate_id(checkpoint_id, "checkpoint_id")
        directory = self._bundle_directory(checkpoint_id)
        if not directory.is_dir() or directory.is_symlink():
            raise LineageIntegrityError(
                f"unknown or invalid Checkpoint bundle {checkpoint_id}"
            )

        manifest = CheckpointBundleManifest.from_dict(
            read_json_object(directory / "bundle_manifest.json")
        )
        if manifest.checkpoint_id != checkpoint_id:
            raise LineageIntegrityError(
                "Checkpoint directory and bundle manifest IDs differ"
            )
        verify_file_hash(
            directory / "checkpoint.json",
            manifest.checkpoint_payload_sha256,
        )
        verify_file_hash(
            directory / "lineage_manifest.json",
            manifest.lineage_manifest_sha256,
        )
        checkpoint_payload = required_json_object(
            {"checkpoint_payload": read_json_object(directory / "checkpoint.json")},
            "checkpoint_payload",
        )
        if checkpoint_payload.get("checkpoint_id") != checkpoint_id:
            raise LineageIntegrityError(
                "Checkpoint payload and bundle manifest IDs differ"
            )
        lineage_manifest = _parse_lineage_manifest(
            read_json_object(directory / "lineage_manifest.json")
        )
        _validate_lineage_manifest(
            lineage_manifest,
            checkpoint_id=checkpoint_id,
            iteration=manifest.iteration,
        )
        control_transaction = self.control_store.load_transaction(
            manifest.control_transaction_id
        )
        if control_transaction.run_id != manifest.run_id:
            raise LineageIntegrityError(
                "bundle manifest and control transaction Run IDs differ"
            )
        if control_transaction.iteration != manifest.iteration:
            raise LineageIntegrityError(
                "bundle manifest and control transaction iterations differ"
            )
        if artifact_path is not None:
            actual_artifact_sha256 = sha256_path(artifact_path)
            if actual_artifact_sha256 != manifest.artifact_sha256:
                raise LineageIntegrityError(
                    "Checkpoint artifact bytes no longer match the committed bundle"
                )
        return CheckpointBundle(
            manifest=manifest,
            checkpoint_payload=checkpoint_payload,
            lineage_manifest=lineage_manifest,
            control_transaction=control_transaction,
        )

    def _bundle_directory(self, checkpoint_id: str) -> Path:
        return self.checkpoints_root / checkpoint_id


def _parse_lineage_manifest(value: Mapping[str, object]) -> LineageManifest:
    expected_fields = {item.name for item in fields(LineageManifest)}
    actual_fields = set(value)
    if actual_fields != expected_fields:
        raise LineageIntegrityError(
            "lineage manifest fields mismatch: "
            f"missing={sorted(expected_fields - actual_fields)}, "
            f"unknown={sorted(actual_fields - expected_fields)}"
        )
    try:
        return LineageManifest.from_dict(dict(value))
    except (TypeError, ValueError) as exc:
        raise LineageIntegrityError("invalid lineage manifest") from exc


def _validate_lineage_manifest(
    manifest: LineageManifest,
    *,
    checkpoint_id: str,
    iteration: int,
) -> None:
    if manifest.checkpoint_id != checkpoint_id:
        raise LineageIntegrityError(
            "lineage manifest Checkpoint ID does not match the bundle"
        )
    if manifest.parent_checkpoint_id == checkpoint_id:
        raise LineageIntegrityError("Checkpoint cannot be its own lineage parent")
    if manifest.iteration != iteration:
        raise LineageIntegrityError(
            "lineage manifest iteration does not match the bundle"
        )
    validate_nonnegative_int(manifest.iteration, "lineage.iteration")
    validate_nonnegative_int(
        manifest.rejected_data_count,
        "lineage.rejected_data_count",
    )
    validate_finite_number(
        manifest.training_loss_final,
        "lineage.training_loss_final",
    )
    validate_finite_number(manifest.benchmark_score, "lineage.benchmark_score")
    normalize_timestamp(manifest.created_at)
    for field_name in (
        "dataset_commit_hash",
        "dataset_path",
        "teacher_api_version",
        "teacher_model",
        "teacher_prompt_hash",
        "filter_config_version",
        "model_id",
        "code_git_commit",
        "status",
    ):
        validate_text(getattr(manifest, field_name), f"lineage.{field_name}")
