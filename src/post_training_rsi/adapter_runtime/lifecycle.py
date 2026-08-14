from __future__ import annotations

from dataclasses import dataclass

from ..control_plane import EvidenceRecord
from ..models import EvaluationResult, TrainingResult
from ..serving.adapter import ServingDeployment, ServingTeardown
from .errors import (
    AdapterLifecycleError,
    AdapterLifecycleFailure,
)
from .factory import AdapterRuntime


@dataclass(frozen=True, slots=True)
class ServedEvaluation:
    deployment: ServingDeployment
    evaluation: EvaluationResult
    teardown: ServingTeardown
    evidence: tuple[EvidenceRecord, ...]


def evaluate_checkpoint_with_serving(
    runtime: AdapterRuntime,
    *,
    checkpoint: TrainingResult,
    run_id: str,
    iteration: int,
    benchmark_id: str,
) -> ServedEvaluation:
    """Deploy, hand the endpoint to evaluation, and always tear down."""

    deployment = runtime.serving.deploy_handle(checkpoint)
    deployment_evidence = runtime.evidence.serving_deployment(
        deployment,
        run_id=run_id,
        iteration=iteration,
    )
    try:
        evaluation = runtime.evaluator.evaluate(
            checkpoint=checkpoint,
            iteration=iteration,
            benchmark_id=benchmark_id,
            endpoint=deployment.endpoint,
        )
    except Exception as evaluation_error:
        try:
            runtime.serving.undeploy_handle(checkpoint, deployment)
        except Exception as teardown_error:
            raise AdapterLifecycleError(
                "evaluation failed and serving teardown also failed",
                failures=(
                    AdapterLifecycleFailure(
                        "evaluation",
                        str(evaluation_error),
                    ),
                    AdapterLifecycleFailure(
                        "serving_teardown",
                        str(teardown_error),
                    ),
                ),
            ) from evaluation_error
        raise

    evaluation_evidence = runtime.evidence.evaluation(
        evaluation,
        run_id=run_id,
        iteration=iteration,
        checkpoint_id=checkpoint.checkpoint_id,
        endpoint=deployment.endpoint,
    )
    try:
        teardown = runtime.serving.undeploy_handle(
            checkpoint,
            deployment,
        )
    except Exception as teardown_error:
        raise AdapterLifecycleError(
            "evaluation completed but serving teardown failed",
            failures=(
                AdapterLifecycleFailure(
                    "serving_teardown",
                    str(teardown_error),
                ),
            ),
        ) from teardown_error
    teardown_evidence = runtime.evidence.serving_teardown(
        teardown,
        run_id=run_id,
        iteration=iteration,
    )
    return ServedEvaluation(
        deployment=deployment,
        evaluation=evaluation,
        teardown=teardown,
        evidence=(
            deployment_evidence,
            evaluation_evidence,
            teardown_evidence,
        ),
    )
