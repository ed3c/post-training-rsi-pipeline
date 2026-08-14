from __future__ import annotations

from dataclasses import replace

import pytest

from post_training_rsi.control_plane import (
    ControlEvent,
    ControlState,
    DecisionAction,
    StateSnapshot,
    StopReason,
)
from post_training_rsi.orchestration import (
    CandidateObservation,
    PolicyInvariantError,
    RSIDecisionPolicy,
    RSIPolicyLimits,
)

NOW = "2026-08-14T02:00:00Z"


def limits(**overrides: float | int) -> RSIPolicyLimits:
    values: dict[str, float | int] = {
        "max_iterations": 5,
        "plateau_patience": 2,
        "min_improvement": 0.01,
        "regression_tolerance": 0.20,
        "per_iteration_budget_usd": 30.0,
        "total_budget_usd": 100.0,
    }
    values.update(overrides)
    return RSIPolicyLimits(**values)  # type: ignore[arg-type]


def evaluation_snapshot(
    *,
    iteration: int = 1,
    peak_score: float = 0.50,
    peak_checkpoint_id: str | None = "ckpt-peak",
    plateau_count: int = 0,
    total_cost_usd: float = 0.0,
) -> StateSnapshot:
    return StateSnapshot(
        snapshot_id=f"snapshot-evaluate-{iteration}",
        run_id="run-001",
        iteration=iteration,
        cycle=0,
        state=ControlState.EVALUATE,
        entered_at=NOW,
        active_checkpoint_id=peak_checkpoint_id,
        candidate_checkpoint_id=f"ckpt-candidate-{iteration}",
        peak_checkpoint_id=peak_checkpoint_id,
        peak_score=peak_score,
        plateau_count=plateau_count,
        total_cost_usd=total_cost_usd,
        evidence_ids=(f"ev-state-{iteration}",),
    )


def candidate(
    *,
    iteration: int = 1,
    score: float = 0.52,
    parent_checkpoint_id: str | None = "ckpt-peak",
    iteration_cost_usd: float = 5.0,
) -> CandidateObservation:
    return CandidateObservation(
        checkpoint_id=f"ckpt-candidate-{iteration}",
        parent_checkpoint_id=parent_checkpoint_id,
        iteration=iteration,
        score=score,
        iteration_cost_usd=iteration_cost_usd,
        evaluated_at=NOW,
        evidence_ids=(f"ev-eval-{iteration}", f"ev-cost-{iteration}"),
    )


def test_candidate_above_strict_delta_promotes_then_continues() -> None:
    step = RSIDecisionPolicy(limits()).evaluate(
        evaluation_snapshot(),
        candidate(score=0.5100001),
    )

    assert [item.action for item in step.decisions] == [
        DecisionAction.PROMOTE,
        DecisionAction.CONTINUE,
    ]
    assert [item.event for item in step.transitions] == [
        ControlEvent.CANDIDATE_IMPROVED,
        ControlEvent.NEXT_ITERATION_REQUESTED,
    ]
    assert [item.state for item in step.snapshots] == [
        ControlState.PROMOTED,
        ControlState.DIAGNOSE,
    ]
    assert step.final_snapshot.iteration == 2
    assert step.final_snapshot.active_checkpoint_id == "ckpt-candidate-1"
    assert step.final_snapshot.peak_checkpoint_id == "ckpt-candidate-1"
    assert step.final_snapshot.peak_score == pytest.approx(0.5100001)
    assert step.final_snapshot.plateau_count == 0
    assert not step.terminal


def test_equal_to_improvement_boundary_rejects_and_increments_plateau() -> None:
    step = RSIDecisionPolicy(limits()).evaluate(
        evaluation_snapshot(),
        candidate(score=0.51),
    )

    assert step.decisions[0].action is DecisionAction.REJECT
    assert step.snapshots[0].state is ControlState.REJECTED
    assert step.final_snapshot.state is ControlState.DIAGNOSE
    assert step.final_snapshot.active_checkpoint_id == "ckpt-peak"
    assert step.final_snapshot.peak_checkpoint_id == "ckpt-peak"
    assert step.final_snapshot.peak_score == 0.50
    assert step.final_snapshot.plateau_count == 1


def test_plateau_rejects_then_stops_with_explicit_reason() -> None:
    step = RSIDecisionPolicy(limits()).evaluate(
        evaluation_snapshot(plateau_count=1),
        candidate(score=0.505),
    )

    assert [item.action for item in step.decisions] == [
        DecisionAction.REJECT,
        DecisionAction.STOP,
    ]
    assert step.transitions[-1].event is ControlEvent.PLATEAU_REACHED
    assert step.final_snapshot.state is ControlState.STOPPED
    assert step.final_snapshot.stop_reason is StopReason.PLATEAU
    assert step.final_snapshot.plateau_count == 2
    assert step.terminal


def test_final_iteration_can_promote_peak_before_max_iteration_stop() -> None:
    step = RSIDecisionPolicy(limits(max_iterations=3)).evaluate(
        evaluation_snapshot(iteration=3),
        candidate(iteration=3, score=0.70),
    )

    assert [item.action for item in step.decisions] == [
        DecisionAction.PROMOTE,
        DecisionAction.STOP,
    ]
    assert step.transitions[-1].event is ControlEvent.MAX_ITERATIONS_REACHED
    assert step.final_snapshot.state is ControlState.STOPPED
    assert step.final_snapshot.stop_reason is StopReason.MAX_ITERATIONS
    assert step.final_snapshot.active_checkpoint_id == "ckpt-candidate-3"
    assert step.final_snapshot.peak_checkpoint_id == "ckpt-candidate-3"
    assert step.final_snapshot.peak_score == 0.70


def test_regression_beyond_tolerance_rolls_back_without_changing_peak() -> None:
    step = RSIDecisionPolicy(limits(regression_tolerance=0.05)).evaluate(
        evaluation_snapshot(),
        candidate(score=0.40),
    )

    assert len(step.decisions) == 1
    assert step.decisions[0].action is DecisionAction.ROLLBACK
    assert step.transitions[0].event is ControlEvent.REGRESSION_DETECTED
    assert step.final_snapshot.state is ControlState.ROLLED_BACK
    assert step.final_snapshot.stop_reason is StopReason.REGRESSION_ROLLBACK
    assert step.final_snapshot.active_checkpoint_id == "ckpt-peak"
    assert step.final_snapshot.peak_checkpoint_id == "ckpt-peak"
    assert step.final_snapshot.peak_score == 0.50
    assert step.terminal


@pytest.mark.parametrize(
    ("policy_limits", "current", "observation", "expected_reason"),
    [
        (
            limits(per_iteration_budget_usd=10.0),
            evaluation_snapshot(),
            candidate(iteration_cost_usd=10.0001),
            StopReason.PER_ITERATION_BUDGET_EXCEEDED,
        ),
        (
            limits(total_budget_usd=20.0, per_iteration_budget_usd=20.0),
            evaluation_snapshot(total_cost_usd=18.0),
            candidate(iteration_cost_usd=2.0001),
            StopReason.TOTAL_BUDGET_EXCEEDED,
        ),
    ],
)
def test_budget_boundaries_abort_fail_closed(
    policy_limits: RSIPolicyLimits,
    current: StateSnapshot,
    observation: CandidateObservation,
    expected_reason: StopReason,
) -> None:
    step = RSIDecisionPolicy(policy_limits).evaluate(current, observation)

    assert step.decisions[0].action is DecisionAction.ABORT
    assert step.transitions[0].event is ControlEvent.BUDGET_EXCEEDED
    assert step.final_snapshot.state is ControlState.ABORTED
    assert step.final_snapshot.stop_reason is expected_reason
    assert step.final_snapshot.active_checkpoint_id == "ckpt-peak"
    assert step.terminal


def test_exact_budget_limits_are_allowed() -> None:
    step = RSIDecisionPolicy(
        limits(total_budget_usd=20.0, per_iteration_budget_usd=10.0)
    ).evaluate(
        evaluation_snapshot(total_cost_usd=10.0),
        candidate(score=0.52, iteration_cost_usd=10.0),
    )

    assert step.decisions[0].action is DecisionAction.PROMOTE
    assert step.final_snapshot.total_cost_usd == 20.0


def test_candidate_parent_must_be_current_accepted_peak() -> None:
    with pytest.raises(PolicyInvariantError, match="candidate parent"):
        RSIDecisionPolicy(limits()).evaluate(
            evaluation_snapshot(),
            candidate(parent_checkpoint_id="ckpt-rejected"),
        )


def test_active_checkpoint_must_match_peak_checkpoint() -> None:
    current = replace(
        evaluation_snapshot(),
        active_checkpoint_id="ckpt-not-peak",
    )
    with pytest.raises(PolicyInvariantError, match="active checkpoint"):
        RSIDecisionPolicy(limits()).evaluate(current, candidate())


def test_policy_requires_evaluate_state_and_matching_iteration() -> None:
    with pytest.raises(PolicyInvariantError, match="EVALUATE"):
        RSIDecisionPolicy(limits()).evaluate(
            replace(evaluation_snapshot(), state=ControlState.DIAGNOSE),
            candidate(),
        )

    with pytest.raises(PolicyInvariantError, match="iteration"):
        RSIDecisionPolicy(limits()).evaluate(
            evaluation_snapshot(iteration=2),
            candidate(iteration=1),
        )


def test_policy_records_are_deterministic_and_json_round_trippable() -> None:
    policy = RSIDecisionPolicy(limits())
    first = policy.evaluate(evaluation_snapshot(), candidate(score=0.52))
    second = policy.evaluate(evaluation_snapshot(), candidate(score=0.52))

    assert first == second
    for decision in first.decisions:
        assert decision.from_dict(decision.to_dict()) == decision
    for transition in first.transitions:
        assert transition.from_dict(transition.to_dict()) == transition
    for snapshot in first.snapshots:
        assert snapshot.from_dict(snapshot.to_dict()) == snapshot


def test_policy_limits_reject_invalid_boundaries() -> None:
    with pytest.raises(ValueError, match="max_iterations"):
        limits(max_iterations=0)
    with pytest.raises(ValueError, match="budget limits"):
        limits(per_iteration_budget_usd=0.0)
    with pytest.raises(ValueError, match="cannot exceed"):
        limits(per_iteration_budget_usd=101.0, total_budget_usd=100.0)


def test_candidate_observation_requires_evidence_and_distinct_parent() -> None:
    with pytest.raises(ValueError, match="requires evidence_ids"):
        CandidateObservation(
            checkpoint_id="ckpt-candidate",
            parent_checkpoint_id="ckpt-parent",
            iteration=1,
            score=0.5,
            iteration_cost_usd=1.0,
            evaluated_at=NOW,
            evidence_ids=(),
        )
    with pytest.raises(ValueError, match="own parent"):
        CandidateObservation(
            checkpoint_id="ckpt-same",
            parent_checkpoint_id="ckpt-same",
            iteration=1,
            score=0.5,
            iteration_cost_usd=1.0,
            evaluated_at=NOW,
            evidence_ids=("ev-001",),
        )
