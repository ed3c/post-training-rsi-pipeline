from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TypeVar


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    total_limit_usd: float = 100.0
    per_iteration_limit_usd: float = 30.0
    max_consecutive_api_failures: int = 3


@dataclass(frozen=True, slots=True)
class VerificationConfig:
    min_entropy: float = 2.5
    min_distinct_2: float = 0.35
    min_type_token_ratio: float = 0.25
    max_semantic_similarity: float = 0.95
    benchmark_ngram_size: int = 13
    max_benchmark_overlap: float = 0.70
    max_lcs_ratio: float = 0.80
    min_acceptance_rate: float = 0.25
    allowed_python_imports: tuple[str, ...] = (
        "collections",
        "dataclasses",
        "functools",
        "itertools",
        "json",
        "math",
        "re",
        "statistics",
        "typing",
    )


@dataclass(frozen=True, slots=True)
class RSIConfig:
    max_iterations: int = 5
    plateau_patience: int = 2
    min_improvement: float = 0.005
    examples_per_iteration: int = 12
    initial_score: float = 0.50
    benchmark_id: str = "mock-agent-benchmark"


@dataclass(frozen=True, slots=True)
class CoEvolutionConfig:
    max_cycles: int = 2
    max_outer_iterations: int = 8
    plateau_patience: int = 3
    target_traces: int = 12
    harness_min_improvement: float = 0.005
    model_min_improvement: float = 0.005


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    model_id: str = "mock-student-8b"
    teacher_model: str = "mock-teacher-70b"
    seed: int = 7
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    rsi: RSIConfig = field(default_factory=RSIConfig)
    co_evolution: CoEvolutionConfig = field(default_factory=CoEvolutionConfig)
    benchmark_texts: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> PipelineConfig:
        defaults = cls()
        config = cls(
            model_id=str(value.get("model_id", defaults.model_id)),
            teacher_model=str(value.get("teacher_model", defaults.teacher_model)),
            seed=int(value.get("seed", defaults.seed)),
            budget=_dataclass_from_mapping(BudgetConfig, value.get("budget", {})),
            verification=_verification_from_mapping(value.get("verification", {})),
            rsi=_dataclass_from_mapping(RSIConfig, value.get("rsi", {})),
            co_evolution=_dataclass_from_mapping(
                CoEvolutionConfig, value.get("co_evolution", {})
            ),
            benchmark_texts=tuple(str(item) for item in value.get("benchmark_texts", [])),
        )
        config.validate()
        return config

    @classmethod
    def load(cls, path: str | Path | None) -> PipelineConfig:
        if path is None:
            config = cls()
            config.validate()
            return config
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_mapping(json.load(handle))

    def validate(self) -> None:
        if self.budget.total_limit_usd <= 0:
            raise ValueError("budget.total_limit_usd must be positive")
        if self.budget.per_iteration_limit_usd <= 0:
            raise ValueError("budget.per_iteration_limit_usd must be positive")
        if self.budget.per_iteration_limit_usd > self.budget.total_limit_usd:
            raise ValueError("per-iteration budget cannot exceed total budget")
        if self.rsi.max_iterations < 1 or self.rsi.plateau_patience < 1:
            raise ValueError("RSI iteration and plateau limits must be positive")
        if self.rsi.examples_per_iteration < 1:
            raise ValueError("rsi.examples_per_iteration must be positive")
        if not 0.0 <= self.verification.min_acceptance_rate <= 1.0:
            raise ValueError("verification.min_acceptance_rate must be in [0, 1]")
        if not 0.0 <= self.verification.max_semantic_similarity <= 1.0:
            raise ValueError("verification.max_semantic_similarity must be in [0, 1]")
        if self.verification.benchmark_ngram_size < 1:
            raise ValueError("verification.benchmark_ngram_size must be positive")
        if self.co_evolution.max_cycles < 1 or self.co_evolution.plateau_patience < 1:
            raise ValueError("co-evolution cycle and plateau limits must be positive")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["verification"]["allowed_python_imports"] = list(
            self.verification.allowed_python_imports
        )
        value["benchmark_texts"] = list(self.benchmark_texts)
        return value


T = TypeVar("T")


def _dataclass_from_mapping(cls: type[T], value: dict[str, Any]) -> T:
    if not isinstance(value, dict):
        raise TypeError(f"expected mapping for {cls.__name__}")
    fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
    kwargs = {name: value[name] for name in fields if name in value}
    return cls(**kwargs)


def _verification_from_mapping(value: dict[str, Any]) -> VerificationConfig:
    if not isinstance(value, dict):
        raise TypeError("expected mapping for VerificationConfig")
    normalized = dict(value)
    if "allowed_python_imports" in normalized:
        normalized["allowed_python_imports"] = tuple(normalized["allowed_python_imports"])
    return _dataclass_from_mapping(VerificationConfig, normalized)
