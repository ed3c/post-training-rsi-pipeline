from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..adapter_runtime.command import CommandSpec, run_json_command
from ..adapter_runtime.errors import AdapterResultError
from ..adapter_runtime.integrity import make_idempotency_key
from ..control_plane.validation import validate_id
from ..models import TrainingResult

SERVING_DEPLOY_RESULT_TYPE = "serving_deploy_result"
SERVING_UNDEPLOY_RESULT_TYPE = "serving_undeploy_result"
_SERVING_DEPLOY_FIELDS = {
    "checkpoint_id",
    "deployment_id",
    "endpoint",
    "ready",
    "metadata",
}
_SERVING_UNDEPLOY_FIELDS = {
    "checkpoint_id",
    "deployment_id",
    "endpoint",
    "stopped",
    "metadata",
}


@dataclass(frozen=True, slots=True)
class ServingDeployment:
    checkpoint_id: str
    deployment_id: str
    endpoint: str
    idempotency_key: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.checkpoint_id, "checkpoint_id")
        validate_id(self.deployment_id, "deployment_id")
        _nonempty_string(self.endpoint, "endpoint")
        _nonempty_string(self.idempotency_key, "idempotency_key")
        object.__setattr__(
            self,
            "metadata",
            _json_object(self.metadata, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class ServingTeardown:
    checkpoint_id: str
    deployment_id: str
    endpoint: str
    stopped: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.checkpoint_id, "checkpoint_id")
        validate_id(self.deployment_id, "deployment_id")
        _nonempty_string(self.endpoint, "endpoint")
        if not isinstance(self.stopped, bool):
            raise TypeError("stopped must be a boolean")
        object.__setattr__(
            self,
            "metadata",
            _json_object(self.metadata, "metadata"),
        )


class ServingAdapter(Protocol):
    def deploy(self, checkpoint: TrainingResult) -> str: ...

    def deploy_handle(
        self,
        checkpoint: TrainingResult,
    ) -> ServingDeployment: ...

    def undeploy(
        self,
        checkpoint: TrainingResult,
        endpoint: str,
    ) -> None: ...

    def undeploy_handle(
        self,
        checkpoint: TrainingResult,
        deployment: ServingDeployment,
    ) -> ServingTeardown: ...


class LocalArtifactServingAdapter:
    """Expose an immutable local URI and a deterministic no-op teardown."""

    def __init__(self) -> None:
        self._deployments: dict[str, ServingDeployment] = {}

    def deploy(self, checkpoint: TrainingResult) -> str:
        return self.deploy_handle(checkpoint).endpoint

    def deploy_handle(
        self,
        checkpoint: TrainingResult,
    ) -> ServingDeployment:
        validate_id(checkpoint.checkpoint_id, "checkpoint_id")
        endpoint = checkpoint.checkpoint_path.resolve().as_uri()
        idempotency_key = make_idempotency_key(
            "serving-deploy",
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "endpoint": endpoint,
            },
        )
        deployment = ServingDeployment(
            checkpoint_id=checkpoint.checkpoint_id,
            deployment_id=f"local-{checkpoint.checkpoint_id}",
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            metadata={"adapter": "local"},
        )
        self._deployments[endpoint] = deployment
        return deployment

    def undeploy(
        self,
        checkpoint: TrainingResult,
        endpoint: str,
    ) -> None:
        deployment = self._deployments.get(endpoint)
        if deployment is None:
            raise AdapterResultError(
                "no local deployment exists for the endpoint"
            )
        self.undeploy_handle(checkpoint, deployment)

    def undeploy_handle(
        self,
        checkpoint: TrainingResult,
        deployment: ServingDeployment,
    ) -> ServingTeardown:
        _validate_deployment(checkpoint, deployment)
        self._deployments.pop(deployment.endpoint, None)
        return ServingTeardown(
            checkpoint_id=checkpoint.checkpoint_id,
            deployment_id=deployment.deployment_id,
            endpoint=deployment.endpoint,
            stopped=True,
            metadata={"adapter": "local", "action": "released"},
        )


class CommandServingAdapter:
    """Deploy and tear down a candidate through exact JSON contracts."""

    def __init__(
        self,
        deploy_command: list[str] | tuple[str, ...],
        *,
        undeploy_command: list[str] | tuple[str, ...],
        timeout_seconds: float = 1_800.0,
        max_attempts: int = 1,
        initial_backoff_seconds: float = 0.0,
        result_root: Path | None = None,
    ) -> None:
        self.deploy_spec = CommandSpec(
            command=tuple(deploy_command),
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            initial_backoff_seconds=initial_backoff_seconds,
        )
        self.undeploy_spec = CommandSpec(
            command=tuple(undeploy_command),
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            initial_backoff_seconds=initial_backoff_seconds,
        )
        self.result_root = result_root
        self._deployments: dict[str, ServingDeployment] = {}

    def deploy(self, checkpoint: TrainingResult) -> str:
        return self.deploy_handle(checkpoint).endpoint

    def deploy_handle(
        self,
        checkpoint: TrainingResult,
    ) -> ServingDeployment:
        validate_id(checkpoint.checkpoint_id, "checkpoint_id")
        idempotency_key = make_idempotency_key(
            "serving-deploy",
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "artifact_sha256": checkpoint.metadata.get(
                    "artifact_sha256",
                ),
            },
        )
        result_path = (
            self._result_root(checkpoint)
            / f"deploy-{idempotency_key.split(':', 1)[1][:24]}.json"
        )
        payload = run_json_command(
            self.deploy_spec,
            result_type=SERVING_DEPLOY_RESULT_TYPE,
            result_path=result_path,
            idempotency_key=idempotency_key,
            expected_fields=_SERVING_DEPLOY_FIELDS,
            environment={
                "RSI_CHECKPOINT_ID": checkpoint.checkpoint_id,
                "RSI_CHECKPOINT_PATH": str(
                    checkpoint.checkpoint_path.resolve()
                ),
                "RSI_SERVE_RESULT_PATH": str(result_path),
            },
        )
        if _required_string(payload, "checkpoint_id") != checkpoint.checkpoint_id:
            raise AdapterResultError("serving checkpoint_id mismatch")
        if payload["ready"] is not True:
            raise AdapterResultError(
                "serving deployment must report ready=true"
            )
        deployment = ServingDeployment(
            checkpoint_id=checkpoint.checkpoint_id,
            deployment_id=_required_id(payload, "deployment_id"),
            endpoint=_required_string(payload, "endpoint"),
            idempotency_key=idempotency_key,
            metadata=_json_object(payload["metadata"], "metadata"),
        )
        existing = self._deployments.get(deployment.endpoint)
        if existing is not None and existing != deployment:
            raise AdapterResultError(
                "serving endpoint is already bound to another deployment"
            )
        self._deployments[deployment.endpoint] = deployment
        return deployment

    def undeploy(
        self,
        checkpoint: TrainingResult,
        endpoint: str,
    ) -> None:
        deployment = self._deployments.get(endpoint)
        if deployment is None:
            raise AdapterResultError(
                "no command deployment exists for the endpoint"
            )
        self.undeploy_handle(checkpoint, deployment)

    def undeploy_handle(
        self,
        checkpoint: TrainingResult,
        deployment: ServingDeployment,
    ) -> ServingTeardown:
        _validate_deployment(checkpoint, deployment)
        idempotency_key = make_idempotency_key(
            "serving-undeploy",
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "deployment_id": deployment.deployment_id,
                "endpoint": deployment.endpoint,
            },
        )
        result_path = (
            self._result_root(checkpoint)
            / f"undeploy-{idempotency_key.split(':', 1)[1][:24]}.json"
        )
        payload = run_json_command(
            self.undeploy_spec,
            result_type=SERVING_UNDEPLOY_RESULT_TYPE,
            result_path=result_path,
            idempotency_key=idempotency_key,
            expected_fields=_SERVING_UNDEPLOY_FIELDS,
            environment={
                "RSI_CHECKPOINT_ID": checkpoint.checkpoint_id,
                "RSI_CHECKPOINT_PATH": str(
                    checkpoint.checkpoint_path.resolve()
                ),
                "RSI_DEPLOYMENT_ID": deployment.deployment_id,
                "RSI_SERVING_ENDPOINT": deployment.endpoint,
                "RSI_UNSERVE_RESULT_PATH": str(result_path),
            },
        )
        if _required_string(payload, "checkpoint_id") != checkpoint.checkpoint_id:
            raise AdapterResultError(
                "serving teardown checkpoint_id mismatch"
            )
        if (
            _required_string(payload, "deployment_id")
            != deployment.deployment_id
        ):
            raise AdapterResultError(
                "serving teardown deployment_id mismatch"
            )
        if _required_string(payload, "endpoint") != deployment.endpoint:
            raise AdapterResultError(
                "serving teardown endpoint mismatch"
            )
        if payload["stopped"] is not True:
            raise AdapterResultError(
                "serving teardown must report stopped=true"
            )
        teardown = ServingTeardown(
            checkpoint_id=checkpoint.checkpoint_id,
            deployment_id=deployment.deployment_id,
            endpoint=deployment.endpoint,
            stopped=True,
            metadata=_json_object(payload["metadata"], "metadata"),
        )
        self._deployments.pop(deployment.endpoint, None)
        return teardown

    def _result_root(self, checkpoint: TrainingResult) -> Path:
        if self.result_root is not None:
            return self.result_root.resolve()
        return (
            checkpoint.checkpoint_path.parent.resolve()
            / ".adapter-results"
            / "serving"
        )


def _validate_deployment(
    checkpoint: TrainingResult,
    deployment: ServingDeployment,
) -> None:
    if deployment.checkpoint_id != checkpoint.checkpoint_id:
        raise AdapterResultError(
            "deployment does not belong to the checkpoint"
        )


def _required_id(value: dict[str, Any], key: str) -> str:
    return validate_id(_required_string(value, key), key)


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item.strip():
        raise AdapterResultError(f"{key} must be a non-empty string")
    _nonempty_string(item, key)
    return item


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


def _nonempty_string(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
