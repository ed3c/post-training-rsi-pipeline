from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Protocol

from ..adapter_runtime.command import CommandSpec, run_json_command
from ..adapter_runtime.errors import (
    AdapterIntegrityError,
    AdapterResultError,
)
from ..adapter_runtime.integrity import (
    make_idempotency_key,
    resolve_artifact_path,
    sha256_path,
    validate_sha256,
)
from ..control_plane.validation import validate_id
from ..models import SyntheticExample, TrainingResult

TRAINING_RESULT_TYPE = "training_result"
_TRAINING_RESULT_FIELDS = {
    "checkpoint_id",
    "checkpoint_path",
    "model_id",
    "parent_checkpoint_id",
    "dataset_hash",
    "iteration",
    "final_loss",
    "artifact_sha256",
    "metadata",
}


class Trainer(Protocol):
    def train(
        self,
        *,
        examples: list[SyntheticExample],
        dataset_path: Path,
        dataset_hash: str,
        model_id: str,
        parent_checkpoint_id: str | None,
        iteration: int,
        output_root: Path,
    ) -> TrainingResult: ...


class MockTrainer:
    """Materialize deterministic checkpoint bytes without gradient updates."""

    def train(
        self,
        *,
        examples: list[SyntheticExample],
        dataset_path: Path,
        dataset_hash: str,
        model_id: str,
        parent_checkpoint_id: str | None,
        iteration: int,
        output_root: Path,
    ) -> TrainingResult:
        _validate_training_request(
            examples=examples,
            dataset_path=dataset_path,
            dataset_hash=dataset_hash,
            model_id=model_id,
            parent_checkpoint_id=parent_checkpoint_id,
            iteration=iteration,
        )
        checkpoint_id = f"ckpt-rsi-iter-{iteration:03d}-{dataset_hash[:8]}"
        checkpoint_path = output_root.resolve() / checkpoint_id
        checkpoint_path.mkdir(parents=True, exist_ok=True)
        final_loss = round(
            max(
                0.04,
                0.42 / (1.0 + len(examples) * 0.08)
                + iteration * 0.004,
            ),
            6,
        )
        payload = {
            "format": "mock-weights-v1",
            "model_id": model_id,
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": parent_checkpoint_id,
            "dataset_path": str(dataset_path.resolve()),
            "dataset_hash": dataset_hash,
            "example_count": len(examples),
            "iteration": iteration,
            "final_loss": final_loss,
        }
        (checkpoint_path / "weights.mock.json").write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        artifact_sha256 = sha256_path(checkpoint_path)
        return TrainingResult(
            checkpoint_id=checkpoint_id,
            checkpoint_path=checkpoint_path,
            model_id=model_id,
            parent_checkpoint_id=parent_checkpoint_id,
            dataset_hash=dataset_hash,
            final_loss=final_loss,
            metadata={
                "iteration": iteration,
                "example_count": len(examples),
                "trainer": "mock",
                "artifact_sha256": artifact_sha256,
                "idempotency_key": _training_idempotency_key(
                    dataset_hash=dataset_hash,
                    model_id=model_id,
                    parent_checkpoint_id=parent_checkpoint_id,
                    iteration=iteration,
                ),
            },
        )


class CommandTrainer:
    """Invoke an external GPU trainer through a strict JSON contract."""

    def __init__(
        self,
        command: list[str] | tuple[str, ...],
        *,
        timeout_seconds: float = 14_400.0,
        max_attempts: int = 1,
        initial_backoff_seconds: float = 0.0,
        allow_external_artifact_path: bool = False,
    ) -> None:
        self.spec = CommandSpec(
            command=tuple(command),
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            initial_backoff_seconds=initial_backoff_seconds,
        )
        if not isinstance(allow_external_artifact_path, bool):
            raise TypeError("allow_external_artifact_path must be a boolean")
        self.allow_external_artifact_path = allow_external_artifact_path

    def train(
        self,
        *,
        examples: list[SyntheticExample],
        dataset_path: Path,
        dataset_hash: str,
        model_id: str,
        parent_checkpoint_id: str | None,
        iteration: int,
        output_root: Path,
    ) -> TrainingResult:
        _validate_training_request(
            examples=examples,
            dataset_path=dataset_path,
            dataset_hash=dataset_hash,
            model_id=model_id,
            parent_checkpoint_id=parent_checkpoint_id,
            iteration=iteration,
        )
        output_root = output_root.resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        idempotency_key = _training_idempotency_key(
            dataset_hash=dataset_hash,
            model_id=model_id,
            parent_checkpoint_id=parent_checkpoint_id,
            iteration=iteration,
        )
        result_path = (
            output_root
            / ".adapter-results"
            / f"training-{idempotency_key.split(':', 1)[1][:24]}.json"
        )
        payload = run_json_command(
            self.spec,
            result_type=TRAINING_RESULT_TYPE,
            result_path=result_path,
            idempotency_key=idempotency_key,
            expected_fields=_TRAINING_RESULT_FIELDS,
            environment={
                "RSI_ITERATION": str(iteration),
                "RSI_MODEL_ID": model_id,
                "RSI_DATASET_PATH": str(dataset_path.resolve()),
                "RSI_DATASET_HASH": dataset_hash,
                "RSI_PARENT_CHECKPOINT_ID": parent_checkpoint_id or "",
                "RSI_OUTPUT_DIR": str(output_root),
                "RSI_TRAIN_RESULT_PATH": str(result_path),
            },
        )
        checkpoint_id = _required_id(payload, "checkpoint_id")
        returned_model_id = _required_string(payload, "model_id")
        returned_parent = _nullable_string(
            payload,
            "parent_checkpoint_id",
        )
        returned_dataset_hash = validate_sha256(
            payload["dataset_hash"],
            "dataset_hash",
        )
        returned_iteration = _required_int(payload, "iteration")
        final_loss = _required_finite_nonnegative(
            payload,
            "final_loss",
        )
        if returned_model_id != model_id:
            raise AdapterResultError("training model_id mismatch")
        if returned_parent != parent_checkpoint_id:
            raise AdapterResultError(
                "training parent_checkpoint_id mismatch"
            )
        if returned_dataset_hash != dataset_hash:
            raise AdapterResultError("training dataset_hash mismatch")
        if returned_iteration != iteration:
            raise AdapterResultError("training iteration mismatch")

        checkpoint_path = resolve_artifact_path(
            payload["checkpoint_path"],
            output_root=output_root,
            allow_external=self.allow_external_artifact_path,
        )
        actual_artifact_sha256 = sha256_path(checkpoint_path)
        reported_artifact_sha256 = payload["artifact_sha256"]
        if reported_artifact_sha256 is not None:
            reported = validate_sha256(
                reported_artifact_sha256,
                "artifact_sha256",
            )
            if reported != actual_artifact_sha256:
                raise AdapterIntegrityError(
                    "training artifact_sha256 mismatch"
                )
        metadata = _json_object(payload["metadata"], "metadata")
        metadata.update(
            {
                "iteration": iteration,
                "trainer": "command",
                "artifact_sha256": actual_artifact_sha256,
                "idempotency_key": idempotency_key,
            }
        )
        return TrainingResult(
            checkpoint_id=checkpoint_id,
            checkpoint_path=checkpoint_path,
            model_id=model_id,
            parent_checkpoint_id=parent_checkpoint_id,
            dataset_hash=dataset_hash,
            final_loss=final_loss,
            metadata=metadata,
        )


def _validate_training_request(
    *,
    examples: list[SyntheticExample],
    dataset_path: Path,
    dataset_hash: str,
    model_id: str,
    parent_checkpoint_id: str | None,
    iteration: int,
) -> None:
    if not examples:
        raise ValueError("cannot train with an empty dataset")
    validate_id(model_id, "model_id")
    if parent_checkpoint_id is not None:
        validate_id(parent_checkpoint_id, "parent_checkpoint_id")
    if isinstance(iteration, bool) or not isinstance(iteration, int):
        raise TypeError("iteration must be an integer")
    if iteration < 1:
        raise ValueError("iteration must be at least 1")
    expected_hash = validate_sha256(dataset_hash, "dataset_hash")
    if dataset_path.is_symlink() or not dataset_path.is_file():
        raise AdapterIntegrityError(
            "dataset_path must be a regular non-symlink file"
        )
    actual_hash = sha256_path(dataset_path)
    if actual_hash != expected_hash:
        raise AdapterIntegrityError(
            "dataset_hash does not match the exact dataset bytes"
        )


def _training_idempotency_key(
    *,
    dataset_hash: str,
    model_id: str,
    parent_checkpoint_id: str | None,
    iteration: int,
) -> str:
    return make_idempotency_key(
        "training",
        {
            "dataset_hash": dataset_hash,
            "model_id": model_id,
            "parent_checkpoint_id": parent_checkpoint_id,
            "iteration": iteration,
        },
    )


def _required_id(value: dict[str, Any], key: str) -> str:
    item = _required_string(value, key)
    return validate_id(item, key)


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item.strip():
        raise AdapterResultError(f"{key} must be a non-empty string")
    return item


def _nullable_string(
    value: dict[str, Any],
    key: str,
) -> str | None:
    item = value[key]
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise AdapterResultError(
            f"{key} must be a non-empty string or null"
        )
    return item


def _required_int(value: dict[str, Any], key: str) -> int:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int):
        raise AdapterResultError(f"{key} must be an integer")
    return item


def _required_finite_nonnegative(
    value: dict[str, Any],
    key: str,
) -> float:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise AdapterResultError(f"{key} must be a number")
    number = float(item)
    if not math.isfinite(number) or number < 0:
        raise AdapterResultError(
            f"{key} must be finite and non-negative"
        )
    return number


def _json_object(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AdapterResultError(f"{field_name} must be a JSON object")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise AdapterResultError(
            f"{field_name} contains a non-JSON value"
        ) from exc
    if not isinstance(decoded, dict):
        raise AdapterResultError(f"{field_name} must be a JSON object")
    return decoded
