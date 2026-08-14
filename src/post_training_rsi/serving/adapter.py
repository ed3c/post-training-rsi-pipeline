from __future__ import annotations

import json
import os
import subprocess
from typing import Protocol

from ..models import TrainingResult


class ServingAdapter(Protocol):
    def deploy(self, checkpoint: TrainingResult) -> str: ...


class LocalArtifactServingAdapter:
    """Returns an immutable local URI used by the deterministic evaluator."""

    def deploy(self, checkpoint: TrainingResult) -> str:
        return checkpoint.checkpoint_path.resolve().as_uri()


class CommandServingAdapter:
    """Invokes a serving system through an environment/JSON readiness contract."""

    def __init__(self, command: list[str], *, timeout_seconds: float = 1_800.0) -> None:
        if not command:
            raise ValueError("command cannot be empty")
        self.command = command
        self.timeout_seconds = timeout_seconds

    def deploy(self, checkpoint: TrainingResult) -> str:
        result_path = checkpoint.checkpoint_path / "serving-result.json"
        env = os.environ.copy()
        env.update(
            {
                "RSI_CHECKPOINT_ID": checkpoint.checkpoint_id,
                "RSI_CHECKPOINT_PATH": str(checkpoint.checkpoint_path),
                "RSI_SERVE_RESULT_PATH": str(result_path),
            }
        )
        subprocess.run(
            self.command,
            env=env,
            check=True,
            timeout=self.timeout_seconds,
        )
        if not result_path.exists():
            raise RuntimeError(f"serving command did not create {result_path}")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if payload.get("ready") is not True:
            raise RuntimeError("serving deployment did not report ready=true")
        endpoint = str(payload.get("endpoint", "")).strip()
        if not endpoint:
            raise RuntimeError("serving result must contain a non-empty endpoint")
        return endpoint
