from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..config import BudgetConfig, RSIConfig
from ..control_plane import (
    ControlEvent,
    ControlState,
    DecisionAction,
    DecisionRecord,
    DecisionSubject,
    JSONValue,
    StateSnapshot,
    StopReason,
    TransitionRecord,
)
from ..control_plane.validation import (
    normalize_timestamp,
    validate_finite_number,
    validate_id,
    validate_id_tuple,
    validate_nonnegative_int,
    validate_nonnegative_number,
)

_EPSILON = 1e-12


class PolicyInvariantError(ValueError):
    """Raised when policy input contradicts the accepted-model lineage."""


@dataclass(frozen=True, slots=True)
class RSIPolicyLimits:
    """Hard boundaries for the pure candidate-decision state machine."""

    max_iterations: int
    plateau_patience: int
    min_improvement: float
    regression_tolerance: float
    per_iteration_budget_usd: float
    total_budget_usd: float

    def __post_init__(self) -> None:
        validate_nonnegative_int(self.max_iterations, "max_iterations")
        validate_nonnegative_int(self.plateau_patience, "plateau_patience")
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        if self.plateau_patience < 1:
            raise ValueError("plateau_patience must be positive")
        validate_nonnegative_number(self.min_improvement, "min_improvement")
        validate_nonnegative_number(self.regression_tolerance, "regression_tolerance")
        validate_nonnegative_number(
            self.per_iteration_budget_usd,
            "per_iteration_budget_usd",
        )
        validate_nonnegative_number(self.total_budget_usd, "total_budget_usd")
        if self.per_iteration_budget_usd <= 0 or self.total_budget_usd <= 0:
            raise ValueError("budget limits must be positive")
        if self.per_iteration_budget_usd > self.total_budget_usd:
            raise ValueError("per-iteration budget cannot exceed total budget")

    @classmethod
    def from_config(
        cls,
        rsi: RSIConfig,
        budget: BudgetConfig,
        *,
        regression_tolerance: float = 0.05,
    ) -> RSIPolicyLimits:
        return cls(
            max_iterations=rsi.max_iterations,
            plateau_patience=rsi.plateau_patience,
            min_improvement=rsi.min_improvement,
            regression_tolerance=regression_tolerance,
            per_iteration_budget_usd=budget.per_iteration_limit_usd,
            total_budget_usd=budget.total_limit_usd,
        )


@dataclass(frozen=True, slots=True)
class CandidateObservation:
    """Evaluated candidate facts consumed by the decision policy."""

    checkpoint_id: str
    parent_checkpoint_id: str | None
    iteration: int
    score: float
    iteration_cost_usd: float
    evaluated_at: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "checkpoint_id",
            validate_id(self.checkpoint_id, "checkpoint_id"),
        )
        if self.parent_checkpoint_id is not None:
            object.__setattr__(
                self,
                "parent_checkpoint_id",
                validate_id(self.parent_checkpoint_id, "parent_checkpoint_id"),
            )
        if self.parent_checkpoint_id == self.checkpoint_id:
            raise ValueError("candidate checkpoint cannot be its own parent")
        validate_nonnegative_int(self.iteration, "iteration")
        if self.iteration < 1:
            raise ValueError("iteration must be at least 1")
        validate_finite_number(self.score, "score")
        validate_nonnegative_number(self.iteration_cost_usd, "iteration_cost_usd")
        object.__setattr__(self, "evaluated_at", normalize_timestamp(self.evaluated_at))
        object.__setattr__(
            self,
            "evidence_ids",
            validate_id_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ValueError("candidate observation requires evidence_ids")


@dataclass(frozen=True, slots=True)
class RSIPolicyStep:
    """Ordered, replayable records emitted for one candidate decision boundary."""

    decisions: tuple[DecisionRecord, ...]
    transitions: tuple[TransitionRecord, ...]
    snapshots: tuple[StateSnapshot, ...]

    def __post_init__(self) -> None:
        if not self.decisions or not self.transitions or not self.snapshots:
            raise ValueError("policy step requires decisions, transitions, and snapshots")
        if not (
            len(self.decisions) == len(self.transitions) == len(self.snapshots)
        ):
            raise ValueError("each policy decision requires one transition and one snapshot")
        for decision, transition, snapshot in zip(
            self.decisions,
            self.transitions,
            self.snapshots,
            strict=True,
        ):
            if transition.decision_id != decision.decision_id:
                raise ValueError("transition must reference its paired decision")
            if snapshot.metadata.get("decision_id") != decision.decision_id:
                raise ValueError("snapshot must reference its paired decision")
            if not (decision.run_id == transition.run_id == snapshot.run_id):
                raise ValueError("paired policy records must share one run_id")

    @property
    def final_snapshot(self) -> StateSnapshot:
        return self.snapshots[-1]

    @property
    def terminal(self) -> bool:
        return self.final_snapshot.state in {
            ControlState.STOPPED,
            ControlState.ABORTED,
            ControlState.ROLLED_BACK,
        }


class RSIDecisionPolicy:
    """Pure Peak/reject/rollback/plateau/budget policy for one evaluated candidate."""

    def __init__(self, limits: RSIPolicyLimits) -> None:
        self.limits = limits

    def evaluate(
        self,
        current: StateSnapshot,
        candidate: CandidateObservation,
    ) -> RSIPolicyStep:
        self._validate_input(current, candidate)
        next_total_cost = current.total_cost_usd + candidate.iteration_cost_usd

        if (
            candidate.iteration_cost_usd
            > self.limits.per_iteration_budget_usd + _EPSILON
        ):
            return self._abort_for_budget(
                current,
                candidate,
                next_total_cost,
                StopReason.PER_ITERATION_BUDGET_EXCEEDED,
            )
        if next_total_cost > self.limits.total_budget_usd + _EPSILON:
            return self._abort_for_budget(
                current,
                candidate,
                next_total_cost,
                StopReason.TOTAL_BUDGET_EXCEEDED,
            )

        peak_score = self._required_peak_score(current)
        score_delta = candidate.score - peak_score
        if candidate.score > peak_score + self.limits.min_improvement:
            intermediate = self._candidate_step(
                current=current,
                candidate=candidate,
                next_total_cost=next_total_cost,
                target_state=ControlState.PROMOTED,
                event=ControlEvent.CANDIDATE_IMPROVED,
                action=DecisionAction.PROMOTE,
                reason_code="candidate_above_peak",
                reason=(
                    "Candidate exceeded the historical Peak by more than the configured "
                    "minimum improvement."
                ),
                next_peak_checkpoint_id=candidate.checkpoint_id,
                next_peak_score=candidate.score,
                next_plateau_count=0,
                stop_reason=None,
                metadata={
                    "candidate_score": candidate.score,
                    "peak_score_before": peak_score,
                    "score_delta": score_delta,
                    "min_improvement": self.limits.min_improvement,
                },
            )
            return self._advance(intermediate, candidate)

        next_plateau_count = current.plateau_count + 1
        regression = max(0.0, peak_score - candidate.score)
        if regression > self.limits.regression_tolerance + _EPSILON:
            return self._candidate_step(
                current=current,
                candidate=candidate,
                next_total_cost=next_total_cost,
                target_state=ControlState.ROLLED_BACK,
                event=ControlEvent.REGRESSION_DETECTED,
                action=DecisionAction.ROLLBACK,
                reason_code="candidate_regressed",
                reason=(
                    "Candidate regressed beyond the configured tolerance; the accepted "
                    "Peak remains active."
                ),
                next_peak_checkpoint_id=current.peak_checkpoint_id,
                next_peak_score=peak_score,
                next_plateau_count=next_plateau_count,
                stop_reason=StopReason.REGRESSION_ROLLBACK,
                metadata={
                    "candidate_score": candidate.score,
                    "peak_score_before": peak_score,
                    "regression": regression,
                    "regression_tolerance": self.limits.regression_tolerance,
                },
            )

        intermediate = self._candidate_step(
            current=current,
            candidate=candidate,
            next_total_cost=next_total_cost,
            target_state=ControlState.REJECTED,
            event=ControlEvent.CANDIDATE_NOT_IMPROVED,
            action=DecisionAction.REJECT,
            reason_code="candidate_not_above_peak",
            reason=(
                "Candidate did not exceed the historical Peak by more than the configured "
                "minimum improvement."
            ),
            next_peak_checkpoint_id=current.peak_checkpoint_id,
            next_peak_score=peak_score,
            next_plateau_count=next_plateau_count,
            stop_reason=None,
            metadata={
                "candidate_score": candidate.score,
                "peak_score_before": peak_score,
                "score_delta": score_delta,
                "min_improvement": self.limits.min_improvement,
            },
        )
        return self._advance(intermediate, candidate)

    def _validate_input(
        self,
        current: StateSnapshot,
        candidate: CandidateObservation,
    ) -> None:
        if current.state is not ControlState.EVALUATE:
            raise PolicyInvariantError("candidate policy requires an EVALUATE snapshot")
        validate_id(current.run_id, "run_id")
        if current.iteration != candidate.iteration:
            raise PolicyInvariantError("candidate iteration does not match state iteration")
        if current.active_checkpoint_id != current.peak_checkpoint_id:
            raise PolicyInvariantError("active checkpoint must equal the accepted Peak")
        if candidate.parent_checkpoint_id != current.active_checkpoint_id:
            raise PolicyInvariantError(
                "candidate parent must be the current accepted checkpoint"
            )
        if (
            current.candidate_checkpoint_id is not None
            and current.candidate_checkpoint_id != candidate.checkpoint_id
        ):
            raise PolicyInvariantError(
                "candidate checkpoint does not match the EVALUATE snapshot"
            )

    @staticmethod
    def _required_peak_score(current: StateSnapshot) -> float:
        peak_score = current.peak_score
        if peak_score is None:
            raise PolicyInvariantError("candidate policy requires an existing Peak score")
        validate_finite_number(peak_score, "peak_score")
        return peak_score

    def _abort_for_budget(
        self,
        current: StateSnapshot,
        candidate: CandidateObservation,
        next_total_cost: float,
        stop_reason: StopReason,
    ) -> RSIPolicyStep:
        budget_name = (
            "per-iteration"
            if stop_reason is StopReason.PER_ITERATION_BUDGET_EXCEEDED
            else "total"
        )
        return self._candidate_step(
            current=current,
            candidate=candidate,
            next_total_cost=next_total_cost,
            target_state=ControlState.ABORTED,
            event=ControlEvent.BUDGET_EXCEEDED,
            action=DecisionAction.ABORT,
            reason_code=f"{budget_name}_budget_exceeded",
            reason=f"The evaluated candidate exceeded the {budget_name} budget boundary.",
            next_peak_checkpoint_id=current.peak_checkpoint_id,
            next_peak_score=self._required_peak_score(current),
            next_plateau_count=current.plateau_count,
            stop_reason=stop_reason,
            metadata={
                "iteration_cost_usd": candidate.iteration_cost_usd,
                "run_total_usd": next_total_cost,
                "per_iteration_limit_usd": self.limits.per_iteration_budget_usd,
                "total_limit_usd": self.limits.total_budget_usd,
            },
        )

    def _candidate_step(
        self,
        *,
        current: StateSnapshot,
        candidate: CandidateObservation,
        next_total_cost: float,
        target_state: ControlState,
        event: ControlEvent,
        action: DecisionAction,
        reason_code: str,
        reason: str,
        next_peak_checkpoint_id: str | None,
        next_peak_score: float,
        next_plateau_count: int,
        stop_reason: StopReason | None,
        metadata: dict[str, JSONValue],
    ) -> RSIPolicyStep:
        phase = target_state.value.lower()
        decision_id = _record_id(
            "decision",
            current.run_id,
            current.iteration,
            phase,
            candidate.checkpoint_id,
        )
        transition_id = _record_id(
            "transition",
            current.run_id,
            current.iteration,
            phase,
            candidate.checkpoint_id,
        )
        snapshot_id = _record_id(
            "snapshot",
            current.run_id,
            current.iteration,
            phase,
            candidate.checkpoint_id,
        )
        decision = DecisionRecord(
            decision_id=decision_id,
            run_id=current.run_id,
            iteration=current.iteration,
            subject_type=DecisionSubject.CHECKPOINT,
            subject_id=candidate.checkpoint_id,
            action=action,
            reason_code=reason_code,
            reason=reason,
            evidence_ids=candidate.evidence_ids,
            created_at=candidate.evaluated_at,
            stop_reason=stop_reason,
            metadata=metadata,
        )
        transition = TransitionRecord(
            transition_id=transition_id,
            run_id=current.run_id,
            iteration=current.iteration,
            from_state=current.state,
            event=event,
            to_state=target_state,
            occurred_at=candidate.evaluated_at,
            idempotency_key=_record_id(
                "idempotency",
                current.run_id,
                current.iteration,
                phase,
                candidate.checkpoint_id,
            ),
            decision_id=decision_id,
            evidence_ids=candidate.evidence_ids,
            metadata={"candidate_checkpoint_id": candidate.checkpoint_id},
        )
        promoted = target_state is ControlState.PROMOTED
        snapshot = StateSnapshot(
            snapshot_id=snapshot_id,
            run_id=current.run_id,
            iteration=current.iteration,
            cycle=current.cycle,
            state=target_state,
            entered_at=candidate.evaluated_at,
            active_checkpoint_id=(
                candidate.checkpoint_id if promoted else current.active_checkpoint_id
            ),
            candidate_checkpoint_id=candidate.checkpoint_id,
            peak_checkpoint_id=next_peak_checkpoint_id,
            active_harness_id=current.active_harness_id,
            candidate_harness_id=current.candidate_harness_id,
            candidate_score=candidate.score,
            peak_score=next_peak_score,
            plateau_count=next_plateau_count,
            total_cost_usd=next_total_cost,
            stop_reason=stop_reason,
            evidence_ids=candidate.evidence_ids,
            metadata={"decision_id": decision_id},
        )
        return RSIPolicyStep(
            decisions=(decision,),
            transitions=(transition,),
            snapshots=(snapshot,),
        )

    def _advance(
        self,
        intermediate: RSIPolicyStep,
        candidate: CandidateObservation,
    ) -> RSIPolicyStep:
        current = intermediate.final_snapshot
        if current.plateau_count >= self.limits.plateau_patience:
            return _append_run_decision(
                intermediate,
                candidate,
                target_state=ControlState.STOPPED,
                event=ControlEvent.PLATEAU_REACHED,
                action=DecisionAction.STOP,
                reason_code="plateau_reached",
                reason="No candidate exceeded the Peak within the configured patience.",
                stop_reason=StopReason.PLATEAU,
                next_iteration=current.iteration,
            )
        if current.iteration >= self.limits.max_iterations:
            return _append_run_decision(
                intermediate,
                candidate,
                target_state=ControlState.STOPPED,
                event=ControlEvent.MAX_ITERATIONS_REACHED,
                action=DecisionAction.STOP,
                reason_code="max_iterations_reached",
                reason="The run reached the configured maximum number of iterations.",
                stop_reason=StopReason.MAX_ITERATIONS,
                next_iteration=current.iteration,
            )
        return _append_run_decision(
            intermediate,
            candidate,
            target_state=ControlState.DIAGNOSE,
            event=ControlEvent.NEXT_ITERATION_REQUESTED,
            action=DecisionAction.CONTINUE,
            reason_code="next_iteration",
            reason="Budget and patience remain; begin the next diagnostic iteration.",
            stop_reason=None,
            next_iteration=current.iteration + 1,
        )


def _append_run_decision(
    step: RSIPolicyStep,
    candidate: CandidateObservation,
    *,
    target_state: ControlState,
    event: ControlEvent,
    action: DecisionAction,
    reason_code: str,
    reason: str,
    stop_reason: StopReason | None,
    next_iteration: int,
) -> RSIPolicyStep:
    current = step.final_snapshot
    phase = target_state.value.lower()
    decision_id = _record_id(
        "decision",
        current.run_id,
        current.iteration,
        phase,
        candidate.checkpoint_id,
    )
    decision = DecisionRecord(
        decision_id=decision_id,
        run_id=current.run_id,
        iteration=current.iteration,
        subject_type=DecisionSubject.RUN,
        subject_id=current.run_id,
        action=action,
        reason_code=reason_code,
        reason=reason,
        evidence_ids=candidate.evidence_ids,
        created_at=candidate.evaluated_at,
        stop_reason=stop_reason,
        metadata={
            "peak_checkpoint_id": current.peak_checkpoint_id,
            "peak_score": current.peak_score,
            "plateau_count": current.plateau_count,
        },
    )
    transition = TransitionRecord(
        transition_id=_record_id(
            "transition",
            current.run_id,
            current.iteration,
            phase,
            candidate.checkpoint_id,
        ),
        run_id=current.run_id,
        iteration=current.iteration,
        from_state=current.state,
        event=event,
        to_state=target_state,
        occurred_at=candidate.evaluated_at,
        idempotency_key=_record_id(
            "idempotency",
            current.run_id,
            current.iteration,
            phase,
            candidate.checkpoint_id,
        ),
        decision_id=decision_id,
        evidence_ids=candidate.evidence_ids,
        metadata={"previous_decision_id": step.decisions[-1].decision_id},
    )
    terminal = target_state is ControlState.STOPPED
    snapshot = StateSnapshot(
        snapshot_id=_record_id(
            "snapshot",
            current.run_id,
            current.iteration,
            phase,
            candidate.checkpoint_id,
        ),
        run_id=current.run_id,
        iteration=next_iteration,
        cycle=current.cycle,
        state=target_state,
        entered_at=candidate.evaluated_at,
        active_checkpoint_id=current.active_checkpoint_id,
        candidate_checkpoint_id=(
            current.candidate_checkpoint_id if terminal else None
        ),
        peak_checkpoint_id=current.peak_checkpoint_id,
        active_harness_id=current.active_harness_id,
        candidate_harness_id=current.candidate_harness_id,
        candidate_score=current.candidate_score if terminal else None,
        peak_score=current.peak_score,
        plateau_count=current.plateau_count,
        total_cost_usd=current.total_cost_usd,
        stop_reason=stop_reason,
        evidence_ids=candidate.evidence_ids,
        metadata={"decision_id": decision_id},
    )
    return RSIPolicyStep(
        decisions=step.decisions + (decision,),
        transitions=step.transitions + (transition,),
        snapshots=step.snapshots + (snapshot,),
    )


def _record_id(
    prefix: str,
    run_id: str,
    iteration: int,
    phase: str,
    identity: str,
) -> str:
    material = f"{run_id}:{iteration}:{phase}:{prefix}:{identity}".encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()[:16]
    return f"{prefix}-rsi-{iteration}-{phase}-{digest}"
