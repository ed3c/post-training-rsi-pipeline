from __future__ import annotations

import json
import os
import subprocess
from typing import Protocol

from ..models import EvaluationResult, TrainingResult


class Evaluator(Protocol):
    def evaluate(
        self,
        *,
        checkpoint: TrainingResult,
        iteration: int,
        benchmark_id: str,
    ) -> EvaluationResult: ...


class DeterministicEvaluator:
    """Reproducible benchmark adapter with an explicit score schedule for control-loop tests."""

    def __init__(self, score_schedule: dict[int, float] | None = None) -> None:
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
    ) -> EvaluationResult:
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
                    "task_id": f"{benchmark_id}-tool-schema-{iteration}",
                    "message": "candidate emitted an invalid tool argument in a boundary case",
                },
                {
                    "code": "UNVERIFIED_INTERMEDIATE_STATE",
                    "task_id": f"{benchmark_id}-state-{iteration}",
                    "message": "candidate skipped one state assertion before the final action",
                },
            ]
        return EvaluationResult(
            score=score,
            benchmark_id=benchmark_id,
            metrics={
                "task_success_rate": score,
                "tool_argument_validity": round(min(1.0, score + 0.08), 6),
                "state_assertion_rate": round(max(0.0, score - 0.04), 6),
            },
            failure_traces=failures,
            estimated_cost_usd=0.0,
        )


class CommandEvaluator:
    """Runs Inspect AI, lm-eval, or an internal benchmark through a JSON contract."""

    def __init__(self, command: list[str], *, timeout_seconds: float = 7_200.0) -> None:
        if not command:
            raise ValueError("command cannot be empty")
        self.command = command
        self.timeout_seconds = timeout_seconds

    def evaluate(
        self,
        *,
        checkpoint: TrainingResult,
        iteration: int,
        benchmark_id: str,
    ) -> EvaluationResult:
        result_path = checkpoint.checkpoint_path / "evaluation-result.json"
        env = os.environ.copy()
        env.update(
            {
                "RSI_ITERATION": str(iteration),
                "RSI_CHECKPOINT_ID": checkpoint.checkpoint_id,
                "RSI_CHECKPOINT_PATH": str(checkpoint.checkpoint_path),
                "RSI_BENCHMARK_ID": benchmark_id,
                "RSI_EVAL_RESULT_PATH": str(result_path),
            }
        )
        subprocess.run(
            self.command,
            env=env,
            check=True,
            timeout=self.timeout_seconds,
        )
        if not result_path.exists():
            raise RuntimeError(f"external evaluator did not create {result_path}")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        return EvaluationResult(
            score=float(payload["score"]),
            benchmark_id=str(payload.get("benchmark_id", benchmark_id)),
            metrics={key: float(value) for key, value in payload.get("metrics", {}).items()},
            failure_traces=list(payload.get("failure_traces", [])),
            estimated_cost_usd=float(payload.get("estimated_cost_usd", 0.0)),
        )
