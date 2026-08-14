from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..config import PipelineConfig
from ..evaluation.adapter import (
    CommandEvaluator,
    DeterministicEvaluator,
    Evaluator,
)
from ..serving.adapter import (
    CommandServingAdapter,
    LocalArtifactServingAdapter,
    ServingAdapter,
)
from ..synthesis.runtime import TeacherClient
from ..synthesis.teacher import (
    MockTeacherClient,
    OpenAICompatibleTeacherClient,
    TeacherTransport,
    UrllibTeacherTransport,
)
from ..training.adapter import CommandTrainer, MockTrainer, Trainer
from .errors import AdapterConfigurationError
from .evidence import AdapterEvidenceTranslator


@dataclass(frozen=True, slots=True)
class AdapterRuntime:
    teacher: TeacherClient
    trainer: Trainer
    evaluator: Evaluator
    serving: ServingAdapter
    evidence: AdapterEvidenceTranslator


def build_adapter_runtime(
    config: PipelineConfig,
    *,
    workspace: str | Path,
    environment: Mapping[str, str] | None = None,
    teacher_transport: TeacherTransport | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], str] | None = None,
) -> AdapterRuntime:
    """Select adapters from strict config without modifying controllers."""

    config.validate()
    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ if environment is None else environment)

    teacher_config = config.adapters.teacher
    if teacher_config.backend == "mock":
        teacher: TeacherClient = MockTeacherClient(
            model_id=teacher_config.model_id,
            api_version=teacher_config.api_version,
        )
    else:
        api_key = env.get(teacher_config.api_key_env)
        if not api_key:
            raise AdapterConfigurationError(
                f"missing API key environment variable "
                f"{teacher_config.api_key_env}"
            )
        selected_transport = (
            teacher_transport
            if teacher_transport is not None
            else UrllibTeacherTransport()
        )
        teacher = OpenAICompatibleTeacherClient(
            model_id=teacher_config.model_id,
            api_version=teacher_config.api_version,
            base_url=teacher_config.base_url or "",
            api_key=api_key,
            input_cost_per_million=(
                teacher_config.input_cost_per_million
            ),
            output_cost_per_million=(
                teacher_config.output_cost_per_million
            ),
            timeout_seconds=teacher_config.timeout_seconds,
            max_attempts=teacher_config.max_attempts,
            initial_backoff_seconds=(
                teacher_config.initial_backoff_seconds
            ),
            transport=selected_transport,
            sleeper=sleeper,
        )

    training_config = config.adapters.training
    if training_config.backend == "mock":
        trainer: Trainer = MockTrainer()
    else:
        trainer = CommandTrainer(
            training_config.command,
            timeout_seconds=training_config.timeout_seconds,
            max_attempts=training_config.max_attempts,
            initial_backoff_seconds=(
                training_config.initial_backoff_seconds
            ),
            allow_external_artifact_path=(
                training_config.allow_external_artifact_path
            ),
        )

    evaluation_config = config.adapters.evaluation
    if evaluation_config.backend == "deterministic":
        evaluator: Evaluator = DeterministicEvaluator()
    else:
        evaluator = CommandEvaluator(
            evaluation_config.command,
            timeout_seconds=evaluation_config.timeout_seconds,
            max_attempts=evaluation_config.max_attempts,
            initial_backoff_seconds=(
                evaluation_config.initial_backoff_seconds
            ),
            result_root=root / ".adapter-results" / "evaluation",
            score_min=evaluation_config.score_min,
            score_max=evaluation_config.score_max,
        )

    serving_config = config.adapters.serving
    if serving_config.backend == "local":
        serving: ServingAdapter = LocalArtifactServingAdapter()
    else:
        serving = CommandServingAdapter(
            serving_config.deploy_command,
            undeploy_command=serving_config.undeploy_command,
            timeout_seconds=serving_config.timeout_seconds,
            max_attempts=serving_config.max_attempts,
            initial_backoff_seconds=(
                serving_config.initial_backoff_seconds
            ),
            result_root=root / ".adapter-results" / "serving",
        )

    return AdapterRuntime(
        teacher=teacher,
        trainer=trainer,
        evaluator=evaluator,
        serving=serving,
        evidence=AdapterEvidenceTranslator(clock=clock),
    )
