from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields
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
class TeacherAdapterConfig:
    backend: str = "mock"
    model_id: str = "mock-teacher-70b"
    api_version: str = "mock-v1"
    base_url: str | None = None
    api_key_env: str = "TEACHER_API_KEY"
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    timeout_seconds: float = 60.0
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class TrainingAdapterConfig:
    backend: str = "mock"
    command: tuple[str, ...] = ()
    timeout_seconds: float = 14_400.0
    max_attempts: int = 1
    initial_backoff_seconds: float = 0.0
    allow_external_artifact_path: bool = False


@dataclass(frozen=True, slots=True)
class EvaluationAdapterConfig:
    backend: str = "deterministic"
    command: tuple[str, ...] = ()
    timeout_seconds: float = 7_200.0
    max_attempts: int = 1
    initial_backoff_seconds: float = 0.0
    score_min: float = 0.0
    score_max: float = 1.0


@dataclass(frozen=True, slots=True)
class ServingAdapterConfig:
    backend: str = "local"
    deploy_command: tuple[str, ...] = ()
    undeploy_command: tuple[str, ...] = ()
    timeout_seconds: float = 1_800.0
    max_attempts: int = 1
    initial_backoff_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class AdapterConfig:
    teacher: TeacherAdapterConfig = field(default_factory=TeacherAdapterConfig)
    training: TrainingAdapterConfig = field(default_factory=TrainingAdapterConfig)
    evaluation: EvaluationAdapterConfig = field(default_factory=EvaluationAdapterConfig)
    serving: ServingAdapterConfig = field(default_factory=ServingAdapterConfig)


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    model_id: str = "mock-student-8b"
    teacher_model: str = "mock-teacher-70b"
    seed: int = 7
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    rsi: RSIConfig = field(default_factory=RSIConfig)
    co_evolution: CoEvolutionConfig = field(default_factory=CoEvolutionConfig)
    adapters: AdapterConfig = field(default_factory=AdapterConfig)
    benchmark_texts: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> PipelineConfig:
        data = _mapping(value, "config")
        _reject_unknown(
            data,
            {
                "model_id",
                "teacher_model",
                "seed",
                "budget",
                "verification",
                "rsi",
                "co_evolution",
                "adapters",
                "benchmark_texts",
            },
            "config",
        )
        defaults = cls()
        teacher_model = _string(data, "teacher_model", defaults.teacher_model)
        adapters = _adapters_from_mapping(
            data.get("adapters", {}),
            default_teacher_model=teacher_model,
        )
        config = cls(
            model_id=_string(data, "model_id", defaults.model_id),
            teacher_model=teacher_model,
            seed=_integer(data, "seed", defaults.seed),
            budget=_dataclass_from_mapping(
                BudgetConfig,
                data.get("budget", {}),
                "budget",
            ),
            verification=_verification_from_mapping(
                data.get("verification", {}),
            ),
            rsi=_dataclass_from_mapping(
                RSIConfig,
                data.get("rsi", {}),
                "rsi",
            ),
            co_evolution=_dataclass_from_mapping(
                CoEvolutionConfig,
                data.get("co_evolution", {}),
                "co_evolution",
            ),
            adapters=adapters,
            benchmark_texts=_string_tuple(
                data.get("benchmark_texts", defaults.benchmark_texts),
                "benchmark_texts",
            ),
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
            value = json.load(handle)
        if not isinstance(value, Mapping):
            raise TypeError("config root must be a JSON object")
        return cls.from_mapping(value)

    def validate(self) -> None:
        _nonempty_string(self.model_id, "model_id")
        _nonempty_string(self.teacher_model, "teacher_model")
        _integer_value(self.seed, "seed")
        if self.adapters.teacher.model_id != self.teacher_model:
            raise ValueError(
                "teacher_model and adapters.teacher.model_id must match"
            )
        self._validate_budget()
        self._validate_verification()
        self._validate_rsi()
        self._validate_co_evolution()
        self._validate_adapters()

    def _validate_budget(self) -> None:
        _positive_number(self.budget.total_limit_usd, "budget.total_limit_usd")
        _positive_number(
            self.budget.per_iteration_limit_usd,
            "budget.per_iteration_limit_usd",
        )
        _positive_integer(
            self.budget.max_consecutive_api_failures,
            "budget.max_consecutive_api_failures",
        )
        if self.budget.per_iteration_limit_usd > self.budget.total_limit_usd:
            raise ValueError("per-iteration budget cannot exceed total budget")

    def _validate_verification(self) -> None:
        _nonnegative_number(
            self.verification.min_entropy,
            "verification.min_entropy",
        )
        for name in (
            "min_distinct_2",
            "min_type_token_ratio",
            "max_semantic_similarity",
            "max_benchmark_overlap",
            "max_lcs_ratio",
            "min_acceptance_rate",
        ):
            value = getattr(self.verification, name)
            _bounded_ratio(value, f"verification.{name}")
        _positive_integer(
            self.verification.benchmark_ngram_size,
            "verification.benchmark_ngram_size",
        )
        _string_tuple(
            self.verification.allowed_python_imports,
            "verification.allowed_python_imports",
        )

    def _validate_rsi(self) -> None:
        _positive_integer(self.rsi.max_iterations, "rsi.max_iterations")
        _positive_integer(self.rsi.plateau_patience, "rsi.plateau_patience")
        _nonnegative_number(self.rsi.min_improvement, "rsi.min_improvement")
        _positive_integer(
            self.rsi.examples_per_iteration,
            "rsi.examples_per_iteration",
        )
        _finite_number(self.rsi.initial_score, "rsi.initial_score")
        _nonempty_string(self.rsi.benchmark_id, "rsi.benchmark_id")

    def _validate_co_evolution(self) -> None:
        _positive_integer(
            self.co_evolution.max_cycles,
            "co_evolution.max_cycles",
        )
        _positive_integer(
            self.co_evolution.max_outer_iterations,
            "co_evolution.max_outer_iterations",
        )
        _positive_integer(
            self.co_evolution.plateau_patience,
            "co_evolution.plateau_patience",
        )
        _positive_integer(
            self.co_evolution.target_traces,
            "co_evolution.target_traces",
        )
        _nonnegative_number(
            self.co_evolution.harness_min_improvement,
            "co_evolution.harness_min_improvement",
        )
        _nonnegative_number(
            self.co_evolution.model_min_improvement,
            "co_evolution.model_min_improvement",
        )

    def _validate_adapters(self) -> None:
        teacher = self.adapters.teacher
        if teacher.backend not in {"mock", "openai_compatible"}:
            raise ValueError("adapters.teacher.backend is unsupported")
        _nonempty_string(teacher.model_id, "adapters.teacher.model_id")
        _nonempty_string(teacher.api_version, "adapters.teacher.api_version")
        _nonempty_string(teacher.api_key_env, "adapters.teacher.api_key_env")
        _retry_values(
            teacher.timeout_seconds,
            teacher.max_attempts,
            teacher.initial_backoff_seconds,
            "adapters.teacher",
        )
        _nonnegative_number(
            teacher.input_cost_per_million,
            "adapters.teacher.input_cost_per_million",
        )
        _nonnegative_number(
            teacher.output_cost_per_million,
            "adapters.teacher.output_cost_per_million",
        )
        if teacher.backend == "openai_compatible":
            if teacher.base_url is None:
                raise ValueError(
                    "adapters.teacher.base_url is required for openai_compatible"
                )
            _nonempty_string(teacher.base_url, "adapters.teacher.base_url")
        elif teacher.base_url is not None:
            raise ValueError(
                "adapters.teacher.base_url is only valid for openai_compatible"
            )

        training = self.adapters.training
        _backend_command(
            training.backend,
            {"mock", "command"},
            training.command,
            "adapters.training",
        )
        _retry_values(
            training.timeout_seconds,
            training.max_attempts,
            training.initial_backoff_seconds,
            "adapters.training",
        )
        _boolean_value(
            training.allow_external_artifact_path,
            "adapters.training.allow_external_artifact_path",
        )

        evaluation = self.adapters.evaluation
        _backend_command(
            evaluation.backend,
            {"deterministic", "command"},
            evaluation.command,
            "adapters.evaluation",
        )
        _retry_values(
            evaluation.timeout_seconds,
            evaluation.max_attempts,
            evaluation.initial_backoff_seconds,
            "adapters.evaluation",
        )
        _finite_number(evaluation.score_min, "adapters.evaluation.score_min")
        _finite_number(evaluation.score_max, "adapters.evaluation.score_max")
        if evaluation.score_min >= evaluation.score_max:
            raise ValueError(
                "adapters.evaluation.score_min must be less than score_max"
            )

        serving = self.adapters.serving
        if serving.backend not in {"local", "command"}:
            raise ValueError("adapters.serving.backend is unsupported")
        _command_tuple(
            serving.deploy_command,
            "adapters.serving.deploy_command",
        )
        _command_tuple(
            serving.undeploy_command,
            "adapters.serving.undeploy_command",
        )
        _retry_values(
            serving.timeout_seconds,
            serving.max_attempts,
            serving.initial_backoff_seconds,
            "adapters.serving",
        )
        if serving.backend == "command":
            if not serving.deploy_command or not serving.undeploy_command:
                raise ValueError(
                    "command serving requires deploy_command and undeploy_command"
                )
        elif serving.deploy_command or serving.undeploy_command:
            raise ValueError("local serving cannot define commands")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["verification"]["allowed_python_imports"] = list(
            self.verification.allowed_python_imports
        )
        value["benchmark_texts"] = list(self.benchmark_texts)
        value["adapters"]["training"]["command"] = list(
            self.adapters.training.command
        )
        value["adapters"]["evaluation"]["command"] = list(
            self.adapters.evaluation.command
        )
        value["adapters"]["serving"]["deploy_command"] = list(
            self.adapters.serving.deploy_command
        )
        value["adapters"]["serving"]["undeploy_command"] = list(
            self.adapters.serving.undeploy_command
        )
        return value


T = TypeVar("T")


def _dataclass_from_mapping(
    cls: type[T],
    value: object,
    field_name: str,
    *,
    overrides: Mapping[str, object] | None = None,
) -> T:
    data = _mapping(value, field_name)
    allowed = {item.name for item in fields(cls)}
    _reject_unknown(data, allowed, field_name)
    kwargs = dict(data)
    if overrides:
        kwargs.update(overrides)
    return cls(**kwargs)


def _verification_from_mapping(value: object) -> VerificationConfig:
    data = _mapping(value, "verification")
    if "allowed_python_imports" in data:
        data["allowed_python_imports"] = _string_tuple(
            data["allowed_python_imports"],
            "verification.allowed_python_imports",
        )
    return _dataclass_from_mapping(
        VerificationConfig,
        data,
        "verification",
    )


def _adapters_from_mapping(
    value: object,
    *,
    default_teacher_model: str,
) -> AdapterConfig:
    data = _mapping(value, "adapters")
    _reject_unknown(
        data,
        {"teacher", "training", "evaluation", "serving"},
        "adapters",
    )
    teacher_data = _mapping(data.get("teacher", {}), "adapters.teacher")
    if "command" in teacher_data:
        raise ValueError("adapters.teacher contains unknown fields: ['command']")
    teacher = _dataclass_from_mapping(
        TeacherAdapterConfig,
        teacher_data,
        "adapters.teacher",
        overrides={
            "model_id": teacher_data.get("model_id", default_teacher_model),
        },
    )
    training_data = _mapping(
        data.get("training", {}),
        "adapters.training",
    )
    if "command" in training_data:
        training_data["command"] = _command(
            training_data["command"],
            "adapters.training.command",
        )
    training = _dataclass_from_mapping(
        TrainingAdapterConfig,
        training_data,
        "adapters.training",
    )
    evaluation_data = _mapping(
        data.get("evaluation", {}),
        "adapters.evaluation",
    )
    if "command" in evaluation_data:
        evaluation_data["command"] = _command(
            evaluation_data["command"],
            "adapters.evaluation.command",
        )
    evaluation = _dataclass_from_mapping(
        EvaluationAdapterConfig,
        evaluation_data,
        "adapters.evaluation",
    )
    serving_data = _mapping(
        data.get("serving", {}),
        "adapters.serving",
    )
    for key in ("deploy_command", "undeploy_command"):
        if key in serving_data:
            serving_data[key] = _command(
                serving_data[key],
                f"adapters.serving.{key}",
            )
    serving = _dataclass_from_mapping(
        ServingAdapterConfig,
        serving_data,
        "adapters.serving",
    )
    return AdapterConfig(
        teacher=teacher,
        training=training,
        evaluation=evaluation,
        serving=serving,
    )


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a JSON object")
    data: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{field_name} keys must be strings")
        data[key] = item
    return data


def _reject_unknown(
    value: Mapping[str, object],
    allowed: set[str],
    field_name: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{field_name} contains unknown fields: {unknown}")


def _string(
    value: Mapping[str, object],
    key: str,
    default: str,
) -> str:
    item = value.get(key, default)
    if not isinstance(item, str):
        raise TypeError(f"{key} must be a string")
    _nonempty_string(item, key)
    return item


def _integer(
    value: Mapping[str, object],
    key: str,
    default: int,
) -> int:
    item = value.get(key, default)
    _integer_value(item, key)
    return item


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} must be an array of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{field_name} must contain only strings")
        _nonempty_string(item, field_name)
        result.append(item)
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return tuple(result)


def _command(value: object, field_name: str) -> tuple[str, ...]:
    command = _string_tuple(value, field_name)
    _command_tuple(command, field_name)
    return command


def _command_tuple(value: tuple[str, ...], field_name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple")
    for item in value:
        _nonempty_string(item, field_name)
        if "\x00" in item:
            raise ValueError(f"{field_name} must not contain NUL bytes")


def _backend_command(
    backend: str,
    allowed: set[str],
    command: tuple[str, ...],
    field_name: str,
) -> None:
    if backend not in allowed:
        raise ValueError(f"{field_name}.backend is unsupported")
    _command_tuple(command, f"{field_name}.command")
    if backend == "command" and not command:
        raise ValueError(f"{field_name}.command is required")
    if backend != "command" and command:
        raise ValueError(f"{field_name}.command is only valid for command backend")


def _retry_values(
    timeout_seconds: object,
    max_attempts: object,
    initial_backoff_seconds: object,
    field_name: str,
) -> None:
    _positive_number(timeout_seconds, f"{field_name}.timeout_seconds")
    _positive_integer(max_attempts, f"{field_name}.max_attempts")
    _nonnegative_number(
        initial_backoff_seconds,
        f"{field_name}.initial_backoff_seconds",
    )


def _nonempty_string(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field_name} must not contain control characters")


def _integer_value(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")


def _positive_integer(value: object, field_name: str) -> None:
    _integer_value(value, field_name)
    if value < 1:
        raise ValueError(f"{field_name} must be positive")


def _finite_number(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    if not math.isfinite(float(value)):
        raise ValueError(f"{field_name} must be finite")


def _positive_number(value: object, field_name: str) -> None:
    _finite_number(value, field_name)
    if float(value) <= 0:
        raise ValueError(f"{field_name} must be positive")


def _nonnegative_number(value: object, field_name: str) -> None:
    _finite_number(value, field_name)
    if float(value) < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _bounded_ratio(value: object, field_name: str) -> None:
    _nonnegative_number(value, field_name)
    if float(value) > 1.0:
        raise ValueError(f"{field_name} must be in [0, 1]")


def _boolean_value(value: object, field_name: str) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a boolean")
