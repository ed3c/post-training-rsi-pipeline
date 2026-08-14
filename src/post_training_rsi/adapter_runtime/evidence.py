from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from ..control_plane import EvidenceKind, EvidenceRecord
from ..models import EvaluationResult, TrainingResult
from ..serving.adapter import ServingDeployment, ServingTeardown
from ..synthesis.runtime import SynthesisBatch
from .integrity import canonical_sha256, sha256_path


class AdapterEvidenceTranslator:
    """Translate provider-neutral stage results into schema-v1 evidence."""

    def __init__(
        self,
        *,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.clock = clock or _utc_now

    def synthesis(
        self,
        batch: SynthesisBatch,
        *,
        run_id: str,
        iteration: int,
        manifest_uri: str | None = None,
    ) -> EvidenceRecord:
        manifest = batch.manifest()
        return EvidenceRecord(
            evidence_id=_evidence_id(
                "synthesis",
                run_id,
                iteration,
                batch.teacher_prompt_hash,
            ),
            run_id=run_id,
            iteration=iteration,
            kind=EvidenceKind.SYNTHESIS_MANIFEST,
            producer="adapter.teacher",
            uri=manifest_uri
            or f"adapter://synthesis/{batch.teacher_model}/{iteration}",
            created_at=self.clock(),
            sha256=canonical_sha256(manifest),
            metadata={
                "teacher_model": batch.teacher_model,
                "teacher_api_version": batch.api_version,
                "teacher_prompt_hash": batch.teacher_prompt_hash,
                "input_tokens": batch.input_tokens,
                "output_tokens": batch.output_tokens,
                "estimated_cost_usd": batch.estimated_cost_usd,
                "request_ids": list(batch.request_ids),
                "example_count": len(batch.examples),
            },
        )

    def training(
        self,
        result: TrainingResult,
        *,
        run_id: str,
        iteration: int,
    ) -> tuple[EvidenceRecord, EvidenceRecord]:
        artifact_sha256 = str(
            result.metadata.get("artifact_sha256")
            or sha256_path(result.checkpoint_path)
        )
        training_payload = {
            "checkpoint_id": result.checkpoint_id,
            "model_id": result.model_id,
            "parent_checkpoint_id": result.parent_checkpoint_id,
            "dataset_hash": result.dataset_hash,
            "final_loss": result.final_loss,
            "artifact_sha256": artifact_sha256,
        }
        training = EvidenceRecord(
            evidence_id=_evidence_id(
                "training",
                run_id,
                iteration,
                result.checkpoint_id,
            ),
            run_id=run_id,
            iteration=iteration,
            kind=EvidenceKind.TRAINING_RESULT,
            producer="adapter.training",
            uri=f"adapter://training/{result.checkpoint_id}",
            created_at=self.clock(),
            sha256=canonical_sha256(training_payload),
            metadata=training_payload,
        )
        checkpoint = EvidenceRecord(
            evidence_id=_evidence_id(
                "checkpoint",
                run_id,
                iteration,
                result.checkpoint_id,
            ),
            run_id=run_id,
            iteration=iteration,
            kind=EvidenceKind.CHECKPOINT,
            producer="adapter.training",
            uri=result.checkpoint_path.resolve().as_uri(),
            created_at=self.clock(),
            sha256=artifact_sha256,
            metadata={
                "checkpoint_id": result.checkpoint_id,
                "model_id": result.model_id,
                "parent_checkpoint_id": result.parent_checkpoint_id,
                "dataset_hash": result.dataset_hash,
            },
        )
        return training, checkpoint

    def serving_deployment(
        self,
        deployment: ServingDeployment,
        *,
        run_id: str,
        iteration: int,
    ) -> EvidenceRecord:
        payload = {
            "checkpoint_id": deployment.checkpoint_id,
            "deployment_id": deployment.deployment_id,
            "endpoint": deployment.endpoint,
            "idempotency_key": deployment.idempotency_key,
        }
        return EvidenceRecord(
            evidence_id=_evidence_id(
                "serving",
                run_id,
                iteration,
                deployment.deployment_id,
            ),
            run_id=run_id,
            iteration=iteration,
            kind=EvidenceKind.SERVING_ENDPOINT,
            producer="adapter.serving",
            uri=deployment.endpoint,
            created_at=self.clock(),
            sha256=canonical_sha256(payload),
            metadata=payload,
        )

    def evaluation(
        self,
        result: EvaluationResult,
        *,
        run_id: str,
        iteration: int,
        checkpoint_id: str,
        endpoint: str | None,
    ) -> EvidenceRecord:
        failure_codes: list[str] = []
        for trace in result.failure_traces:
            code = trace.get("code")
            if isinstance(code, str) and code not in failure_codes:
                failure_codes.append(code)
        payload: dict[str, Any] = {
            "checkpoint_id": checkpoint_id,
            "benchmark_id": result.benchmark_id,
            "score": result.score,
            "metrics": dict(result.metrics),
            "failure_count": len(result.failure_traces),
            "failure_codes": failure_codes,
            "estimated_cost_usd": result.estimated_cost_usd,
            "endpoint": endpoint,
        }
        return EvidenceRecord(
            evidence_id=_evidence_id(
                "evaluation",
                run_id,
                iteration,
                f"{checkpoint_id}:{result.benchmark_id}",
            ),
            run_id=run_id,
            iteration=iteration,
            kind=EvidenceKind.EVALUATION_RESULT,
            producer="adapter.evaluation",
            uri=(
                f"adapter://evaluation/{checkpoint_id}/"
                f"{result.benchmark_id}"
            ),
            created_at=self.clock(),
            sha256=canonical_sha256(payload),
            metadata=payload,
        )

    def serving_teardown(
        self,
        teardown: ServingTeardown,
        *,
        run_id: str,
        iteration: int,
    ) -> EvidenceRecord:
        payload = {
            "checkpoint_id": teardown.checkpoint_id,
            "deployment_id": teardown.deployment_id,
            "endpoint": teardown.endpoint,
            "stopped": teardown.stopped,
        }
        return EvidenceRecord(
            evidence_id=_evidence_id(
                "teardown",
                run_id,
                iteration,
                teardown.deployment_id,
            ),
            run_id=run_id,
            iteration=iteration,
            kind=EvidenceKind.SERVING_TEARDOWN,
            producer="adapter.serving",
            uri=(
                f"adapter://serving-teardown/"
                f"{teardown.deployment_id}"
            ),
            created_at=self.clock(),
            sha256=canonical_sha256(payload),
            metadata=payload,
        )


def _evidence_id(
    stage: str,
    run_id: str,
    iteration: int,
    subject: str,
) -> str:
    digest = canonical_sha256(
        {
            "stage": stage,
            "run_id": run_id,
            "iteration": iteration,
            "subject": subject,
        }
    )
    return f"ev.adapter.{stage}.{digest[:24]}"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
