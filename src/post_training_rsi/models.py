from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SyntheticExample:
    """One synthesized training example and its generation metadata."""

    example_id: str
    prompt: str
    response: str
    code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        parts = [self.prompt.strip(), self.response.strip()]
        if self.code:
            parts.append(self.code.strip())
        return "\n".join(part for part in parts if part)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SyntheticExample:
        return cls(
            example_id=str(value["example_id"]),
            prompt=str(value.get("prompt", "")),
            response=str(value.get("response", "")),
            code=value.get("code"),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(slots=True)
class VerificationRecord:
    example_id: str
    accepted: bool
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, float | str | bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class VerificationBatch:
    accepted: list[SyntheticExample]
    quarantined: list[SyntheticExample]
    records: list[VerificationRecord]

    @property
    def acceptance_rate(self) -> float:
        total = len(self.accepted) + len(self.quarantined)
        return len(self.accepted) / total if total else 0.0

    @property
    def rejection_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            for reason in record.reasons:
                counts[reason] = counts.get(reason, 0) + 1
        return counts


@dataclass(slots=True)
class TrainingResult:
    checkpoint_id: str
    checkpoint_path: Path
    model_id: str
    parent_checkpoint_id: str | None
    dataset_hash: str
    final_loss: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["checkpoint_path"] = str(self.checkpoint_path)
        return value


@dataclass(slots=True)
class EvaluationResult:
    score: float
    benchmark_id: str
    metrics: dict[str, float] = field(default_factory=dict)
    failure_traces: list[dict[str, Any]] = field(default_factory=list)
    estimated_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class IterationOutcome:
    iteration: int
    status: str
    hypothesis: str
    raw_count: int
    accepted_count: int
    rejected_count: int
    acceptance_rate: float
    checkpoint_id: str | None = None
    candidate_score: float | None = None
    peak_score: float | None = None
    promoted: bool = False
    rollback_to: str | None = None
    cost_usd: float = 0.0
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RSIRunResult:
    status: str
    peak_checkpoint_id: str | None
    peak_score: float
    total_cost_usd: float
    outcomes: list[IterationOutcome]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "peak_checkpoint_id": self.peak_checkpoint_id,
            "peak_score": self.peak_score,
            "total_cost_usd": self.total_cost_usd,
            "outcomes": [item.to_dict() for item in self.outcomes],
        }


@dataclass(slots=True)
class ExecutionTrace:
    trace_id: str
    task: str
    response: str
    tool_steps: list[dict[str, Any]]
    success: bool
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
