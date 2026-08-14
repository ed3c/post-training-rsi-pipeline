from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..control_plane import JSONValue
from ..control_plane.validation import canonical_json
from .model_inner_loop import (
    ModelBenchmarkObservation,
    ModelCandidateArtifact,
    ModelServingLease,
    ModelTeardownObservation,
    ModelTrainingRequest,
)
from .outer_loop import (
    HarnessSpec,
    HarnessTask,
    HarnessTaskResult,
)
from .trace_harvesting import (
    ObservableTraceStep,
    ObservableTrajectory,
    TraceEventType,
)


@dataclass(frozen=True, slots=True)
class ReferenceClock:
    """Deterministic logical timestamps stable across resume and replay."""

    origin: str = "2026-08-14T09:00:00Z"

    def at(self, *, cycle: int, ordinal: int) -> str:
        base = datetime.fromisoformat(self.origin.replace("Z", "+00:00"))
        value = base + timedelta(seconds=cycle * 10_000 + ordinal)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ReferenceTaskSuite:
    benchmark_id: str
    tasks: tuple[HarnessTask, ...]

    @classmethod
    def default(cls) -> ReferenceTaskSuite:
        families = (
            "tool-use",
            "state-verification",
            "boundary-handling",
            "safety",
        )
        tasks = tuple(
            HarnessTask(
                task_id=f"reference-task-{index:03d}",
                task_family=families[index % len(families)],
                weight=1.0 + (index % 3) * 0.25,
                metadata={"fixture_version": "v1", "index": index},
            )
            for index in range(16)
        )
        return cls(
            benchmark_id="reference-coevolution-benchmark-v1",
            tasks=tasks,
        )


class ReferenceHarnessTaskRunner:
    """Deterministic observable task results under a frozen model/Harness pair."""

    def __init__(self, *, model_score: float, harness_score: float) -> None:
        self.model_score = float(model_score)
        self.harness_score = float(harness_score)

    def __call__(
        self,
        harness: HarnessSpec,
        task: HarnessTask,
    ) -> HarnessTaskResult:
        family_adjustment = {
            "tool-use": 0.010,
            "state-verification": 0.015,
            "boundary-handling": 0.005,
            "safety": 0.000,
        }.get(task.task_family, 0.0)
        tool_adjustment = 0.005 if "browser" in harness.tools else 0.0
        score = min(
            1.0,
            max(
                0.0,
                0.45 * self.model_score
                + 0.55 * self.harness_score
                + family_adjustment
                + tool_adjustment,
            ),
        )
        success = score >= 0.45
        return HarnessTaskResult(
            task_id=task.task_id,
            task_family=task.task_family,
            score=score,
            success=success,
            failure_code=None if success else "REFERENCE_TASK_FAILED",
            observable_trace_uri=(
                f"artifact://reference-traces/{task.task_id}.json"
            ),
            metadata={
                "fixture_version": "v1",
                "model_score": self.model_score,
                "harness_score": self.harness_score,
                "tool_count": len(harness.tools),
            },
        )


def build_reference_trajectories(
    *,
    run_id: str,
    cycle: int,
    model_checkpoint_id: str,
    harness_id: str,
    count: int,
    score: float,
    clock: ReferenceClock,
) -> tuple[ObservableTrajectory, ...]:
    trajectories: list[ObservableTrajectory] = []
    families = (
        "tool-use",
        "state-verification",
        "boundary-handling",
        "safety",
    )
    for index in range(count):
        task_id = f"trace-task-c{cycle:02d}-{index:03d}"
        family = families[index % len(families)]
        started_at = clock.at(cycle=cycle, ordinal=300 + index * 10)
        completed_at = clock.at(cycle=cycle, ordinal=305 + index * 10)
        unique_values = [index + 3, index + 7, index + 11]
        total = sum(unique_values)
        steps = (
            ObservableTraceStep(
                step_index=0,
                event_type=TraceEventType.TASK_INPUT,
                content=(
                    f"Task {task_id}: inspect public record {index}, calculate the "
                    "verified total, check the observable terminal state, and return "
                    "a concise result without exposing private data."
                ),
                metadata={
                    "fixture_version": "v1",
                    "record_index": index,
                    "task_family": family,
                },
            ),
            ObservableTraceStep(
                step_index=1,
                event_type=TraceEventType.TOOL_CALL,
                content=canonical_json(
                    {
                        "operation": "lookup_public_record",
                        "record_index": index,
                        "expected_fields": ["values", "status"],
                    }
                ),
                tool_name="search",
                tool_call_id=f"call-c{cycle:02d}-{index:03d}",
                status="REQUESTED",
            ),
            ObservableTraceStep(
                step_index=2,
                event_type=TraceEventType.TOOL_RESULT,
                content=canonical_json(
                    {
                        "record_index": index,
                        "status": "ok",
                        "values": unique_values,
                    }
                ),
                tool_name="search",
                tool_call_id=f"call-c{cycle:02d}-{index:03d}",
                status="SUCCEEDED",
            ),
            ObservableTraceStep(
                step_index=3,
                event_type=TraceEventType.STATE_OBSERVATION,
                content=(
                    f"Observable record {index} returned three values and status ok; "
                    f"the verified sum is {total}."
                ),
                status="VERIFIED",
                metadata={
                    "item_count": 3,
                    "verified_total": total,
                    "terminal_state_observed": True,
                },
            ),
            ObservableTraceStep(
                step_index=4,
                event_type=TraceEventType.FINAL_OUTPUT,
                content=(
                    f"Verified public record {index}: total {total} across three items; "
                    "observable state confirmed."
                ),
                status="SUCCEEDED",
                metadata={"final_state_verified": True},
            ),
        )
        trajectories.append(
            ObservableTrajectory.create(
                run_id=run_id,
                cycle=cycle,
                task_id=task_id,
                task_family=family,
                model_checkpoint_id=model_checkpoint_id,
                harness_id=harness_id,
                success=True,
                score=min(1.0, score + (index % 5) * 0.001),
                started_at=started_at,
                completed_at=completed_at,
                steps=steps,
                evidence_ids=(f"ev-reference-task-c{cycle:02d}-{index:03d}",),
                metadata={
                    "fixture_version": "v1",
                    "observable_only": True,
                    "task_index": index,
                },
            )
        )
    return tuple(trajectories)


class ReferenceModelTrainer:
    """Dependency-free trainer that writes a deterministic Candidate artifact."""

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        expected_score: float,
        clock: ReferenceClock,
    ) -> None:
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.expected_score = float(expected_score)
        self.clock = clock

    def train(self, request: ModelTrainingRequest) -> ModelCandidateArtifact:
        payload: dict[str, JSONValue] = {
            "schema_version": "reference-model-artifact/v1",
            "request_id": request.request_id,
            "run_id": request.run_id,
            "cycle": request.cycle,
            "model_id": request.model_id,
            "parent_checkpoint_id": request.parent_checkpoint_id,
            "dataset_id": request.dataset_id,
            "dataset_sha256": request.dataset_sha256,
            "algorithm": request.algorithm.value,
            "accepted_example_count": request.accepted_example_count,
            "expected_reference_score": self.expected_score,
        }
        serialized = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        artifact = self.artifact_root / request.request_id / "weights.reference.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        if artifact.exists():
            if artifact.read_bytes() != serialized:
                raise ValueError(
                    "reference model artifact path already has different bytes"
                )
        else:
            artifact.write_bytes(serialized)
        artifact_sha256 = hashlib.sha256(serialized).hexdigest()
        return ModelCandidateArtifact.create(
            request=request,
            artifact_path=artifact.as_posix(),
            artifact_sha256=artifact_sha256,
            training_loss=max(0.01, 0.25 - request.cycle * 0.03),
            training_cost_usd=0.20 + request.accepted_example_count * 0.001,
            trained_at=self.clock.at(cycle=request.cycle, ordinal=700),
            evidence_ids=(
                f"ev-reference-training-c{request.cycle:02d}",
                f"ev-reference-artifact-c{request.cycle:02d}",
            ),
            metadata={
                "provider": "reference-local-trainer",
                "expected_reference_score": self.expected_score,
            },
        )


class ReferenceModelDeployer:
    def __init__(self, *, clock: ReferenceClock) -> None:
        self.clock = clock

    def deploy(self, candidate: ModelCandidateArtifact) -> ModelServingLease:
        digest = hashlib.sha256(candidate.checkpoint_id.encode("utf-8")).hexdigest()
        deployment_id = f"reference-deployment-{digest[:20]}"
        return ModelServingLease(
            deployment_id=deployment_id,
            checkpoint_id=candidate.checkpoint_id,
            endpoint=f"memory://reference-model/{candidate.checkpoint_id}",
            deployed_at=self.clock.at(cycle=candidate.cycle, ordinal=710),
            evidence_ids=(f"ev-reference-deploy-c{candidate.cycle:02d}",),
        )


class ReferenceModelEvaluator:
    def __init__(
        self,
        *,
        benchmark_id: str,
        clock: ReferenceClock,
    ) -> None:
        self.benchmark_id = benchmark_id
        self.clock = clock

    def evaluate(
        self,
        candidate: ModelCandidateArtifact,
        *,
        endpoint: str,
    ) -> ModelBenchmarkObservation:
        expected = candidate.metadata.get("expected_reference_score")
        if isinstance(expected, bool) or not isinstance(expected, (int, float)):
            raise ValueError(
                "reference Candidate metadata is missing expected_reference_score"
            )
        score = min(1.0, max(0.0, float(expected)))
        return ModelBenchmarkObservation(
            run_id=candidate.run_id,
            cycle=candidate.cycle,
            checkpoint_id=candidate.checkpoint_id,
            parent_checkpoint_id=candidate.parent_checkpoint_id,
            endpoint=endpoint,
            benchmark_id=self.benchmark_id,
            score=score,
            task_family_scores={
                "tool-use": score,
                "state-verification": max(0.0, score - 0.01),
                "boundary-handling": max(0.0, score - 0.02),
                "safety": min(1.0, score + 0.01),
            },
            failure_trace_uris=(
                f"artifact://reference-model-failures/{candidate.checkpoint_id}.json",
            ),
            evaluation_cost_usd=0.15,
            evaluated_at=self.clock.at(cycle=candidate.cycle, ordinal=720),
            evidence_ids=(f"ev-reference-model-eval-c{candidate.cycle:02d}",),
            metadata={"provider": "reference-local-evaluator"},
        )


class ReferenceModelTeardown:
    def __init__(self, *, clock: ReferenceClock) -> None:
        self.clock = clock
        self.calls = 0

    def teardown(self, lease: ModelServingLease) -> ModelTeardownObservation:
        self.calls += 1
        return ModelTeardownObservation(
            deployment_id=lease.deployment_id,
            checkpoint_id=lease.checkpoint_id,
            torn_down=True,
            completed_at=self.clock.at(cycle=1, ordinal=730 + self.calls),
            evidence_ids=(f"ev-reference-teardown-{lease.deployment_id}",),
        )


def reference_model_score(
    *,
    cycle: int,
    active_score: float,
    min_improvement: float,
) -> float:
    if cycle == 1:
        return min(1.0, active_score + min_improvement + 0.03)
    return max(0.0, active_score - 0.02)


def reference_harness_candidate_score(
    *,
    cycle: int,
    attempt: int,
    active_score: float,
    min_improvement: float,
) -> float:
    if attempt == 1:
        return min(1.0, active_score + min_improvement + 0.02)
    return active_score


def content_addressed_harness_id(
    *,
    prefix: str,
    payload: dict[str, JSONValue],
) -> str:
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:24]}"
