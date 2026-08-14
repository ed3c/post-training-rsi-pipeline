from __future__ import annotations

from pathlib import Path

import pytest

from post_training_rsi.config import VerificationConfig
from post_training_rsi.harness.trace_harvesting import (
    ObservableTraceStep,
    ObservableTrajectory,
    TraceContractError,
    TraceDatasetConflictError,
    TraceDatasetResult,
    TraceEventType,
    TraceHarvestConfig,
    TraceHarvester,
    TraceVerificationService,
)
from post_training_rsi.verification.pipeline import VerificationPipeline

NOW = "2026-08-14T09:00:00Z"
RUN_ID = "run-trace-hardening"
CHECKPOINT_ID = "checkpoint-trace-hardening"
HARNESS_ID = "harness-trace-hardening"


def _trace_without_task_input() -> ObservableTrajectory:
    return ObservableTrajectory.create(
        run_id=RUN_ID,
        cycle=1,
        task_id="task-missing-input",
        task_family="tool-use",
        model_checkpoint_id=CHECKPOINT_ID,
        harness_id=HARNESS_ID,
        success=True,
        score=0.9,
        started_at=NOW,
        completed_at="2026-08-14T09:00:01Z",
        steps=(
            ObservableTraceStep(
                step_index=0,
                event_type=TraceEventType.FINAL_OUTPUT,
                content="A public final output exists, but the task input is absent.",
                status="SUCCEEDED",
            ),
        ),
        evidence_ids=("ev-missing-input",),
        metadata={"observable_only": True},
    )


def _valid_trace() -> ObservableTrajectory:
    return ObservableTrajectory.create(
        run_id=RUN_ID,
        cycle=1,
        task_id="task-replay",
        task_family="tool-use",
        model_checkpoint_id=CHECKPOINT_ID,
        harness_id=HARNESS_ID,
        success=True,
        score=0.95,
        started_at=NOW,
        completed_at="2026-08-14T09:00:02Z",
        steps=(
            ObservableTraceStep(
                step_index=0,
                event_type=TraceEventType.TASK_INPUT,
                content="Read the public fixture and return the verified total.",
            ),
            ObservableTraceStep(
                step_index=1,
                event_type=TraceEventType.FINAL_OUTPUT,
                content="The verified public total is 16.",
                status="SUCCEEDED",
            ),
        ),
        evidence_ids=("ev-valid-trace",),
        metadata={"observable_only": True},
    )


def _harvester() -> TraceHarvester:
    return TraceHarvester(
        TraceHarvestConfig(
            target_count=1,
            min_score=0.5,
            max_per_task_family=1,
        )
    )


def _batch():  # type: ignore[no-untyped-def]
    return _harvester().harvest(
        (_valid_trace(),),
        expected_run_id=RUN_ID,
        expected_cycle=1,
        expected_model_checkpoint_id=CHECKPOINT_ID,
        expected_harness_id=HARNESS_ID,
        selection_seed="hardening-seed",
        created_at=NOW,
        evidence_ids=("ev-hardening-harvest",),
    )


def _verifier() -> VerificationPipeline:
    return VerificationPipeline(
        VerificationConfig(
            min_entropy=0.0,
            min_distinct_2=0.0,
            min_type_token_ratio=0.0,
            max_semantic_similarity=1.0,
            benchmark_ngram_size=3,
            max_benchmark_overlap=1.0,
            max_lcs_ratio=1.0,
            min_acceptance_rate=0.0,
        )
    )


def test_harvester_rejects_success_without_observable_task_input() -> None:
    trace = _trace_without_task_input()

    batch = _harvester().harvest(
        (trace,),
        expected_run_id=RUN_ID,
        expected_cycle=1,
        expected_model_checkpoint_id=CHECKPOINT_ID,
        expected_harness_id=HARNESS_ID,
        selection_seed="missing-input-seed",
        created_at=NOW,
        evidence_ids=("ev-missing-input-batch",),
    )

    assert batch.selected == ()
    assert len(batch.rejected) == 1
    assert batch.rejected[0].trace_id == trace.trace_id
    assert batch.rejected[0].reasons == ("MISSING_TASK_INPUT",)


def test_same_service_exact_replay_is_idempotent(tmp_path: Path) -> None:
    batch = _batch()
    service = TraceVerificationService(
        verifier=_verifier(),
        output_root=tmp_path,
        harvester=_harvester(),
    )

    first = service.verify(batch, created_at=NOW)
    replay = service.verify(batch, created_at=NOW)

    assert replay == first


def test_same_service_replay_detects_committed_dataset_tampering(
    tmp_path: Path,
) -> None:
    batch = _batch()
    service = TraceVerificationService(
        verifier=_verifier(),
        output_root=tmp_path,
        harvester=_harvester(),
    )
    committed = service.verify(batch, created_at=NOW)
    Path(committed.result.dataset_path).write_text("tampered\n", encoding="utf-8")

    with pytest.raises(TraceDatasetConflictError):
        service.verify(batch, created_at=NOW)


def test_trace_dataset_result_rejects_inconsistent_acceptance_rate() -> None:
    with pytest.raises(TraceContractError, match="acceptance_rate"):
        TraceDatasetResult(
            batch_id="trace-batch-rate",
            dataset_id="trace-dataset-rate",
            run_id=RUN_ID,
            cycle=1,
            model_checkpoint_id=CHECKPOINT_ID,
            harness_id=HARNESS_ID,
            dataset_path="trace-datasets/rate/accepted.jsonl",
            dataset_sha256="a" * 64,
            audit_path="trace-datasets/rate/filter_audit.jsonl",
            raw_count=2,
            accepted_count=1,
            rejected_count=1,
            acceptance_rate=0.9,
            rejection_counts={"LOW_ENTROPY": 1},
            accepted_example_ids=("trace-example-rate",),
            created_at=NOW,
            evidence_ids=("ev-trace-rate",),
        )
