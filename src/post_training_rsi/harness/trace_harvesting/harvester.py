from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ...control_plane import JSONValue
from ...control_plane.validation import canonical_json
from .contracts import (
    HarvestedTraceBatch,
    ObservableTrajectory,
    TraceContractError,
    TraceEventType,
    TraceRejection,
    TraceTrainingExample,
)


@dataclass(frozen=True, slots=True)
class TraceHarvestConfig:
    target_count: int = 12
    min_score: float = 0.5
    max_per_task_family: int = 6

    def __post_init__(self) -> None:
        if isinstance(self.target_count, bool) or not isinstance(self.target_count, int):
            raise TraceContractError("target_count must be an integer")
        if self.target_count < 1:
            raise TraceContractError("target_count must be positive")
        if isinstance(self.min_score, bool) or not isinstance(
            self.min_score,
            (int, float),
        ):
            raise TraceContractError("min_score must be a number")
        if not 0.0 <= float(self.min_score) <= 1.0:
            raise TraceContractError("min_score must be in [0, 1]")
        if isinstance(self.max_per_task_family, bool) or not isinstance(
            self.max_per_task_family,
            int,
        ):
            raise TraceContractError("max_per_task_family must be an integer")
        if self.max_per_task_family < 1:
            raise TraceContractError("max_per_task_family must be positive")


class TraceHarvester:
    """Select successful, lineage-matching observable traces without input-order bias."""

    def __init__(self, config: TraceHarvestConfig) -> None:
        self.config = config

    def harvest(
        self,
        traces: Iterable[ObservableTrajectory],
        *,
        expected_run_id: str,
        expected_cycle: int,
        expected_model_checkpoint_id: str,
        expected_harness_id: str,
        selection_seed: str,
        created_at: str,
        evidence_ids: tuple[str, ...],
        cost_usd: float = 0.0,
        metadata: Mapping[str, object] | None = None,
    ) -> HarvestedTraceBatch:
        ordered = sorted(
            tuple(traces),
            key=lambda trace: (trace.trace_id, trace.trace_sha256),
        )
        eligible: list[ObservableTrajectory] = []
        rejected: list[TraceRejection] = []
        seen: set[str] = set()

        for trace in ordered:
            reasons: list[str] = []
            if trace.trace_id in seen:
                reasons.append("DUPLICATE_TRACE")
            else:
                seen.add(trace.trace_id)
            if trace.run_id != expected_run_id:
                reasons.append("RUN_MISMATCH")
            if trace.cycle != expected_cycle:
                reasons.append("CYCLE_MISMATCH")
            if trace.model_checkpoint_id != expected_model_checkpoint_id:
                reasons.append("MODEL_MISMATCH")
            if trace.harness_id != expected_harness_id:
                reasons.append("HARNESS_MISMATCH")
            if not trace.success:
                reasons.append("UNSUCCESSFUL_TRACE")
            if trace.score < self.config.min_score:
                reasons.append("SCORE_BELOW_MINIMUM")
            if not any(
                step.event_type is TraceEventType.TASK_INPUT
                for step in trace.steps
            ):
                reasons.append("MISSING_TASK_INPUT")

            if reasons:
                rejected.append(
                    TraceRejection(
                        trace_id=trace.trace_id,
                        reasons=tuple(reasons),
                    )
                )
            else:
                eligible.append(trace)

        ranked = sorted(
            eligible,
            key=lambda trace: (
                _selection_rank(selection_seed, trace.trace_id),
                trace.trace_id,
            ),
        )
        selected: list[ObservableTrajectory] = []
        family_counts: Counter[str] = Counter()
        for trace in ranked:
            if len(selected) >= self.config.target_count:
                rejected.append(
                    TraceRejection(
                        trace_id=trace.trace_id,
                        reasons=("TARGET_COUNT_REACHED",),
                    )
                )
                continue
            if family_counts[trace.task_family] >= self.config.max_per_task_family:
                rejected.append(
                    TraceRejection(
                        trace_id=trace.trace_id,
                        reasons=("TASK_FAMILY_CAP_REACHED",),
                    )
                )
                continue
            selected.append(trace)
            family_counts[trace.task_family] += 1

        selected_tuple = tuple(sorted(selected, key=lambda trace: trace.trace_id))
        rejected_tuple = tuple(
            sorted(rejected, key=lambda item: (item.trace_id, item.reasons))
        )
        batch_metadata: dict[str, object] = dict(metadata or {})
        batch_metadata.update(
            {
                "eligible_count": len(eligible),
                "selected_family_counts": dict(sorted(family_counts.items())),
                "min_score": float(self.config.min_score),
                "max_per_task_family": self.config.max_per_task_family,
            }
        )
        return HarvestedTraceBatch.create(
            run_id=expected_run_id,
            cycle=expected_cycle,
            model_checkpoint_id=expected_model_checkpoint_id,
            harness_id=expected_harness_id,
            target_count=self.config.target_count,
            selected=selected_tuple,
            rejected=rejected_tuple,
            selection_seed=selection_seed,
            cost_usd=cost_usd,
            created_at=created_at,
            evidence_ids=evidence_ids,
            metadata=batch_metadata,
        )

    def to_training_examples(
        self,
        batch: HarvestedTraceBatch,
    ) -> tuple[TraceTrainingExample, ...]:
        examples = tuple(_trajectory_to_example(trace) for trace in batch.selected)
        return tuple(sorted(examples, key=lambda example: example.example_id))


def _trajectory_to_example(trace: ObservableTrajectory) -> TraceTrainingExample:
    task_input = [
        step.to_dict()
        for step in trace.steps
        if step.event_type is TraceEventType.TASK_INPUT
    ]
    observable_actions = [
        step.to_dict()
        for step in trace.steps
        if step.event_type is not TraceEventType.TASK_INPUT
    ]
    if not task_input:
        raise TraceContractError(
            f"successful trace {trace.trace_id} has no TASK_INPUT event"
        )

    prompt_payload: dict[str, JSONValue] = {
        "task_id": trace.task_id,
        "task_family": trace.task_family,
        "observable_input": task_input,
    }
    response_payload: dict[str, JSONValue] = {
        "observable_actions": observable_actions,
        "outcome": {
            "success": trace.success,
            "score": trace.score,
        },
    }
    prompt = canonical_json(prompt_payload)
    response = canonical_json(response_payload)
    example_payload: dict[str, JSONValue] = {
        "trace_id": trace.trace_id,
        "task_id": trace.task_id,
        "task_family": trace.task_family,
        "prompt": prompt,
        "response": response,
        "model_checkpoint_id": trace.model_checkpoint_id,
        "harness_id": trace.harness_id,
        "trace_sha256": trace.trace_sha256,
    }
    example_id = _content_id("trace-example", example_payload)
    return TraceTrainingExample(
        example_id=example_id,
        trace_id=trace.trace_id,
        task_id=trace.task_id,
        task_family=trace.task_family,
        prompt=prompt,
        response=response,
        code="",
        metadata={
            "run_id": trace.run_id,
            "cycle": trace.cycle,
            "model_checkpoint_id": trace.model_checkpoint_id,
            "harness_id": trace.harness_id,
            "trace_sha256": trace.trace_sha256,
            "source_evidence_ids": list(trace.evidence_ids),
            "observable_only": True,
        },
    )


def _selection_rank(seed: str, trace_id: str) -> str:
    return hashlib.sha256(f"{seed}|{trace_id}".encode("utf-8")).hexdigest()


def _content_id(prefix: str, payload: Mapping[str, JSONValue]) -> str:
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:24]}"
