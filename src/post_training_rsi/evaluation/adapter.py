from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..domain import Checkpoint, EvaluationResult, FailureTrace, HarnessSnapshot
from ..hashing import canonical_sha256


class Evaluator(Protocol):
    def evaluate(
        self,
        checkpoint: Checkpoint,
        *,
        harness: HarnessSnapshot | None = None,
    ) -> EvaluationResult: ...


@dataclass(slots=True)
class DeterministicEvaluator:
    max_training_examples_for_saturation: int = 30

    def evaluate(
        self,
        checkpoint: Checkpoint,
        *,
        harness: HarnessSnapshot | None = None,
    ) -> EvaluationResult:
        count = int(checkpoint.metadata.get("accepted_count", 0))
        iteration = int(checkpoint.metadata.get("iteration", 1))
        harness_text = harness.system_prompt.lower() if harness else ""
        prompt_bonus = 0.0
        failures: list[FailureTrace] = []
        task_scores: dict[str, float] = {}
        families = {
            "reasoning": "invariant",
            "tool_use": "validate arguments",
            "state_check": "verify intermediate",
            "timeout_recovery": "bounded retry",
        }
        data_bonus = 0.20 * min(1.0, count / self.max_training_examples_for_saturation)
        iteration_bonus = min(0.15, 0.025 * iteration)
        for task_id, cue in families.items():
            cue_bonus = 0.04 if cue in harness_text else 0.0
            score = min(1.0, 0.48 + data_bonus + iteration_bonus + cue_bonus)
            task_scores[task_id] = round(score, 6)
            prompt_bonus += cue_bonus / len(families)
            if score < 0.70:
                failures.append(
                    FailureTrace(
                        task_id=task_id,
                        category=self._category(task_id),
                        message=f"{task_id} did not meet the 0.70 acceptance threshold",
                    )
                )
        generalization_penalty = max(
            0.0,
            0.015 * max(0, iteration - 4) + 0.02 * max(0, len(harness_text) / 1200 - 1),
        )
        score = sum(task_scores.values()) / len(task_scores) - generalization_penalty
        score = max(0.0, min(1.0, score))
        return EvaluationResult(
            score=round(score, 6),
            task_scores=task_scores,
            failures=tuple(failures),
            metrics={
                "accepted_training_examples": count,
                "prompt_bonus": round(prompt_bonus, 6),
                "generalization_penalty": round(generalization_penalty, 6),
                "checkpoint_fingerprint": canonical_sha256(checkpoint.to_dict())[:16],
                "finite": math.isfinite(score),
            },
        )

    @staticmethod
    def _category(task_id: str) -> str:
        return {
            "tool_use": "INVALID_JSON",
            "state_check": "UNVERIFIED_INTERMEDIATE_STATE",
            "timeout_recovery": "TIMEOUT",
        }.get(task_id, "GENERAL")


@dataclass(slots=True)
class CommandEvaluator:
    command: str
    timeout_seconds: int = 60 * 60

    def evaluate(
        self,
        checkpoint: Checkpoint,
        *,
        harness: HarnessSnapshot | None = None,
    ) -> EvaluationResult:
        result_path = checkpoint.artifact_path.parent / "evaluation_result.json"
        harness_path: Path | None = None
        if harness is not None:
            harness_path = checkpoint.artifact_path.parent / "candidate_harness.json"
            harness_path.write_text(
                json.dumps(harness.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        env = os.environ.copy()
        env.update(
            {
                "RSI_CHECKPOINT_ID": checkpoint.checkpoint_id,
                "RSI_CHECKPOINT_PATH": str(checkpoint.artifact_path.resolve()),
                "RSI_EVAL_RESULT_PATH": str(result_path.resolve()),
                "RSI_HARNESS_PATH": str(harness_path.resolve()) if harness_path else "",
            }
        )
        process = subprocess.run(
            shlex.split(self.command),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        (checkpoint.artifact_path.parent / "evaluation.stdout.log").write_text(
            process.stdout,
            encoding="utf-8",
        )
        (checkpoint.artifact_path.parent / "evaluation.stderr.log").write_text(
            process.stderr,
            encoding="utf-8",
        )
        if process.returncode != 0:
            raise RuntimeError(f"evaluation command failed with exit code {process.returncode}")
        data = json.loads(result_path.read_text(encoding="utf-8"))
        failures = tuple(FailureTrace(**item) for item in data.get("failures", []))
        return EvaluationResult(
            score=float(data["score"]),
            task_scores={str(key): float(value) for key, value in data["task_scores"].items()},
            failures=failures,
            metrics=data.get("metrics", {}),
        )
