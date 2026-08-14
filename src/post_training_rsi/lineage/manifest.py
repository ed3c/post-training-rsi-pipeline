from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class LineageManifest:
    checkpoint_id: str
    parent_checkpoint_id: str | None
    dataset_commit_hash: str
    dataset_path: str
    teacher_api_version: str
    teacher_model: str
    teacher_prompt_hash: str
    filter_config_version: str
    rejected_data_count: int
    training_loss_final: float
    benchmark_score: float
    model_id: str
    code_git_commit: str
    iteration: int
    status: str
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        checkpoint_id: str,
        parent_checkpoint_id: str | None,
        dataset_commit_hash: str,
        dataset_path: str,
        teacher_api_version: str,
        teacher_model: str,
        teacher_prompt_hash: str,
        filter_config_version: str,
        rejected_data_count: int,
        training_loss_final: float,
        benchmark_score: float,
        model_id: str,
        iteration: int,
        status: str,
        code_git_commit: str | None = None,
    ) -> LineageManifest:
        return cls(
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=parent_checkpoint_id,
            dataset_commit_hash=dataset_commit_hash,
            dataset_path=dataset_path,
            teacher_api_version=teacher_api_version,
            teacher_model=teacher_model,
            teacher_prompt_hash=teacher_prompt_hash,
            filter_config_version=filter_config_version,
            rejected_data_count=rejected_data_count,
            training_loss_final=training_loss_final,
            benchmark_score=benchmark_score,
            model_id=model_id,
            code_git_commit=code_git_commit or detect_git_commit(),
            iteration=iteration,
            status=status,
            created_at=datetime.now(UTC).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> LineageManifest:
        return cls(**value)


def detect_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"
