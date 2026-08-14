from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Protocol

from ..models import SyntheticExample, TrainingResult


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
    """Materializes a deterministic checkpoint artifact without performing gradient updates."""

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
        if not examples:
            raise ValueError("cannot train with an empty dataset")
        checkpoint_id = f"ckpt-rsi-iter-{iteration:03d}-{dataset_hash[:8]}"
        checkpoint_path = output_root / checkpoint_id
        checkpoint_path.mkdir(parents=True, exist_ok=True)
        final_loss = round(
            max(0.04, 0.42 / (1.0 + len(examples) * 0.08) + iteration * 0.004),
            6,
        )
        payload = {
            "format": "mock-weights-v1",
            "model_id": model_id,
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": parent_checkpoint_id,
            "dataset_path": str(dataset_path),
            "dataset_hash": dataset_hash,
            "example_count": len(examples),
            "iteration": iteration,
            "final_loss": final_loss,
        }
        (checkpoint_path / "weights.mock.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
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
            },
        )


class CommandTrainer:
    """Runs an external trainer through a stable environment/JSON result contract."""

    def __init__(self, command: list[str], *, timeout_seconds: float = 14_400.0) -> None:
        if not command:
            raise ValueError("command cannot be empty")
        self.command = command
        self.timeout_seconds = timeout_seconds

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
        if not examples:
            raise ValueError("cannot train with an empty dataset")
        output_root.mkdir(parents=True, exist_ok=True)
        result_path = output_root / f"train-result-{iteration:03d}.json"
        env = os.environ.copy()
        env.update(
            {
                "RSI_ITERATION": str(iteration),
                "RSI_MODEL_ID": model_id,
                "RSI_DATASET_PATH": str(dataset_path),
                "RSI_DATASET_HASH": dataset_hash,
                "RSI_PARENT_CHECKPOINT_ID": parent_checkpoint_id or "",
                "RSI_OUTPUT_DIR": str(output_root),
                "RSI_TRAIN_RESULT_PATH": str(result_path),
            }
        )
        subprocess.run(
            self.command,
            env=env,
            check=True,
            timeout=self.timeout_seconds,
        )
        if not result_path.exists():
            raise RuntimeError(f"external trainer did not create {result_path}")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        checkpoint_path = Path(payload["checkpoint_path"])
        if not checkpoint_path.exists():
            raise RuntimeError(f"external checkpoint does not exist: {checkpoint_path}")
        return TrainingResult(
            checkpoint_id=str(payload["checkpoint_id"]),
            checkpoint_path=checkpoint_path,
            model_id=str(payload.get("model_id", model_id)),
            parent_checkpoint_id=payload.get("parent_checkpoint_id") or parent_checkpoint_id,
            dataset_hash=str(payload.get("dataset_hash", dataset_hash)),
            final_loss=float(payload["final_loss"]),
            metadata=dict(payload.get("metadata", {})),
        )
