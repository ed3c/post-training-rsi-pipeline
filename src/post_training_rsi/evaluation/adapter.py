from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Protocol

from ..adapter_runtime.command import CommandSpec, run_json_command
from ..adapter_runtime.errors import AdapterResultError
from ..adapter_runtime.integrity import make_idempotency_key
from ..control_plane.validation import validate_id
from ..models import EvaluationResult, TrainingResult

EVALUATION_RESULT_TYPE = "evaluation_result"
_EVALUATION_RESULT_FIELDS = {
    "checkpoint_id",
    "benchmark_id",
    "iteration",
    "endpoint",
    "score",
    "metrics",
    "failure_traces",
    "estimated_cost_usd",
    "metadata",
}


class Evaluator(Protocol):
    def evaluate(
        self,
        *,
        checkpoint: TrainingResult,
        iteration: int,
        benchmark_id: str,
        endpoint: str | None = None,
    ) -> EvaluationResult: ...


class DeterministicEvaluator:
    """Reproducible benchmark with an explicit score schedule."""

    def __init__(
        self,
        score_schedule: dict[int, float] | None = None,
    ) -> None:
        self.score_schedule = score_schedule or {
            1: 0.58,
            2: 0.64,
            3: 0.62,
            4: 0.63,
            5: 0.625,
        }

    def evaluate(
        self,
        *,
        checkpoint: TrainingResult,
        iteration: int,
        benchmark_id: str,
        endpoint: str | None = None,
    ) -> EvaluationResult:
        del checkpoint, endpoint
        score = self.score_schedule.get(
            iteration,
            max(0.0, min(1.0, 0.61 - 0.003 * max(0, iteration - 5))),
        )
        score = round(float(score), 6)
        failures: list[dict[str, object]] = []
        if score < 0.70:
            failures = [
                {
                    "code": "INVALID_JSON",
                    "task_id": (
                        f"{benchmark_id}-tool-schema-{iteration}"
                    ),
                    "message": (
                        "candidate emitted an invalid tool argument "
                        "in a boundary case"
                    ),
                },
                {
                    "code": "UNVERIFIED_INTERMEDIATE_STATE",
                    "task_id": f"{benchmark_id}-state-{iteration}",
                    "message": (
                        "candidate skipped one state assertion "
                        "before the final action"
                    ),
                },
            ]
        return EvaluationResult(
            score=score,
            benchmark_id=benchmark_id,
            metrics={
                "task_success_rate": score,
                "tool_argument_validity": round(
                    min(1.0, score + 0.08),
                    6,
                ),
                "state_assertion_rate": round(
                    max(0.0, score - 0.04),
                    6,
                ),
            },
            failure_traces=failures,
            estimated_cost_usd=0.0,
        )


class CommandEvaluator:
    """Invoke Inspect AI, lm-eval, or a sandbox through exact JSON."""

    def __init__(
        self,
        command: list[str] | tuple[str, ...],
        *,
        timeout_seconds: float = 7_200.0,
        max_attempts: int = 1,
        initial_backoff_seconds: float = 0.0,
        result_root: Path | None = None,
        score_min: float = 0.0,
        score_max: float = 1.0,
    ) -> None:
        self.spec = CommandSpec(
            command=tuple(command),
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            initial_backoff_seconds=initial_backoff_seconds,
        )
        if not math.isfinite(score_min) or not math.isfinite(score_max):
            raise ValueError("score bounds must be finite")
        if score_min >= score_max:
            raise ValueError("score_min must be less than score_max")
        self.result_root = result_root
        self.score_min = score_min
        self.score_max = score_max

    def evaluate(
        self,
        *,
        checkpoint: TrainingResult,
        iteration: int,
        benchmark_id: str,
        endpoint: str | None = None,
    ) -> EvaluationResult:
        validate_id(checkpoint.checkpoint_id, "checkpoint_id")
        validate_id(benchmark_id, "benchmark_id")
        if isinstance(iteration, bool) or not isinstance(iteration, int):
            raise TypeError("iteration must be an integer")
        if iteration < 1:
            raise ValueError("iteration must be at least 1")
        if endpoint is not None:
            _nonempty_string(endpoint, "endpoint")

        idempotency_key = make_idempotency_key(
            "evaluation",
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "benchmark_id": benchmark_id,
                "iteration": iteration,
                "endpoint": endpoint,
            },
        )
        root = (
            self.result_root.resolve()
            if self.result_root is not None
            else checkpoint.checkpoint_path.parent.resolve()
            / ".adapter-results"
            / "evaluation"
        )
        result_path = (
            root
            / f"evaluation-{idempotency_key.split(':', 1)[1][:24]}.json"
        )
        payload = run_json_command(
            self.spec,
            result_type=EVALUATION_RESULT_TYPE,
            result_path=result_path,
            idempotency_key=idempotency_key,
            expected_fields=_EVALUATION_RESULT_FIELDS,
            environment={
                "RSI_ITERATION": str(iteration),
                "RSI_CHECKPOINT_ID": checkpoint.checkpoint_id,
                "RSI_CHECKPOINT_PATH": str(
                    checkpoint.checkpoint_path.resolve()
                ),
                "RSI_BENCHMARK_ID": benchmark_id,
                "RSI_SERVING_ENDPOINT": endpoint or "",
                "RSI_EVAL_RESULT_PATH": str(result_path),
            },
        )
        if _required_string(payload, "checkpoint_id") != checkpoint.checkpoint_id:
            raise AdapterResultError("evaluation checkpoint_id mismatch")
        if _required_string(payload, "benchmark_id") != benchmark_id:
            raise AdapterResultError("evaluation benchmark_id mismatch")
        if _required_int(payload, "iteration") != iteration:
            raise AdapterResultError("evaluation iteration mismatch")
        returned_endpoint = _nullable_string(payload, "endpoint")
        if returned_endpoint != endpoint:
            raise AdapterResultError("evaluation endpoint mismatch")

        score = _finite_number(payload["score"], "score")
        if not self.score_min <= score <= self.score_max:
            raise AdapterResultError(
                f"score must be in [{self.score_min}, {self.score_max}]"
            )
        metrics = _metrics(payload["metrics"])
        failure_traces = _json_object_list(
            payload["failure_traces"],
            "failure_traces",
        )
        estimated_cost_usd = _finite_number(
            payload["estimated_cost_usd"],
            "estimated_cost_usd",
        )
        if estimated_cost_usd < 0:
            raise AdapterResultError(
                "estimated_cost_usd must be non-negative"
            )
        _json_object(payload["metadata"], "metadata")
        return EvaluationResult(
            score=score,
            benchmark_id=benchmark_id,
            metrics=metrics,
            failure_traces=failure_traces,
            estimated_cost_usd=estimated_cost_usd,
        )


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item.strip():
        raise AdapterResultError(f"{key} must be a non-empty string")
    return item


def _nullable_string(value: dict[str, Any], key: str) -> str | None:
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


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterResultError(f"{field_name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise AdapterResultError(f"{field_name} must be finite")
    return number


def _metrics(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        raise AdapterResultError("metrics must be a JSON object")
    result: dict[str, float] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise AdapterResultError(
                "metrics keys must be non-empty strings"
            )
        result[key] = _finite_number(item, f"metrics.{key}")
    return result


def _json_object_list(
    value: object,
    field_name: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise AdapterResultError(f"{field_name} must be a JSON array")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        result.append(
            _json_object(item, f"{field_name}[{index}]")
        )
    return result


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
