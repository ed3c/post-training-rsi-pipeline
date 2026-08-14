from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from ...control_plane import JSONValue
from .contracts import (
    HarnessBenchmarkResult,
    HarnessContractError,
    HarnessSpec,
    HarnessTask,
    HarnessTaskResult,
)

HarnessTaskRunner = Callable[[HarnessSpec, HarnessTask], HarnessTaskResult]


class DeterministicHarnessEvaluator:
    """Aggregate observable task results under a fixed Harness and task suite."""

    def __init__(
        self,
        *,
        benchmark_id: str,
        runner: HarnessTaskRunner,
    ) -> None:
        if not benchmark_id or not benchmark_id.strip():
            raise HarnessContractError("benchmark_id must be non-empty")
        self.benchmark_id = benchmark_id
        self.runner = runner

    def evaluate(
        self,
        harness: HarnessSpec,
        tasks: Sequence[HarnessTask],
        *,
        evaluated_at: str,
        evidence_ids: tuple[str, ...],
        cost_usd: float = 0.0,
        metadata: dict[str, JSONValue] | None = None,
    ) -> HarnessBenchmarkResult:
        if not tasks:
            raise HarnessContractError("Harness evaluation requires at least one task")
        task_ids = tuple(task.task_id for task in tasks)
        if len(task_ids) != len(set(task_ids)):
            raise HarnessContractError("Harness evaluation task IDs must be unique")

        results: list[HarnessTaskResult] = []
        weighted_scores: list[float] = []
        weights: list[float] = []
        family_scores: dict[str, list[tuple[float, float]]] = {}

        for task in tasks:
            result = self.runner(harness, task)
            if result.task_id != task.task_id:
                raise HarnessContractError(
                    f"task runner returned {result.task_id!r} for task {task.task_id!r}"
                )
            if result.task_family != task.task_family:
                raise HarnessContractError(
                    "task runner changed task_family for "
                    f"{task.task_id!r}: {result.task_family!r}"
                )
            results.append(result)
            weighted_scores.append(result.score * task.weight)
            weights.append(task.weight)
            family_scores.setdefault(task.task_family, []).append(
                (result.score, task.weight)
            )

        total_weight = math.fsum(weights)
        if total_weight <= 0:
            raise HarnessContractError("Harness evaluation total weight must be positive")
        score = math.fsum(weighted_scores) / total_weight
        aggregate_family_scores = {
            family: math.fsum(item_score * item_weight for item_score, item_weight in items)
            / math.fsum(item_weight for _, item_weight in items)
            for family, items in sorted(family_scores.items())
        }

        result_metadata: dict[str, JSONValue] = dict(metadata or {})
        result_metadata.update(
            {
                "task_count": len(results),
                "success_count": sum(1 for result in results if result.success),
                "failure_count": sum(1 for result in results if not result.success),
            }
        )
        return HarnessBenchmarkResult(
            harness_id=harness.harness_id,
            benchmark_id=self.benchmark_id,
            score=score,
            task_family_scores=aggregate_family_scores,
            task_results=tuple(results),
            cost_usd=cost_usd,
            evaluated_at=evaluated_at,
            evidence_ids=evidence_ids,
            metadata=result_metadata,
        )
