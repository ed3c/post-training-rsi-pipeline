from __future__ import annotations

from dataclasses import asdict, dataclass
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> LineageManifest:
        return cls(**value)
