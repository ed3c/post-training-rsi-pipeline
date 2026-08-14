from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ...control_plane import (
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
from ...control_plane.validation import (
    validate_finite_number,
    validate_id,
    validate_id_tuple,
    validate_nonnegative_int,
    validate_nonnegative_number,
)
from .contracts import (
    HarnessBenchmarkResult,
    HarnessContractError,
    HarnessReviewObservation,
    HarnessSpec,
    HarnessValidationResult,
)

_EPSILON = 1e-12


class HarnessPolicyInvariantError(HarnessContractError):
    """Raised when an outer-loop operation contradicts the frozen-model lineage."""


@dataclass(frozen=True, slots=True)
class HarnessOuterLimits:
    max_iterations: int = 8
    plateau_patience: int = 3
    min_improvement: float = 0.005
    per_iteration_budget_usd: float = 10.0
    total_budget_usd: float = 50.0
    approval_required: bool = False

    def __post_init__(self) -> None:
        validate_nonnegative_int(self.max_iterations, "max_iterations")
        validate_nonnegative_int(self.plateau_patience, "plateau_patience")
        if self.max_iterations < 1:
            raise HarnessContractError("max_iterations must be positive")
        if self.plateau_patience < 1:
            raise HarnessContractError("plateau_patience must be positive")
        validate_nonnegative_number(self.min_improvement, "min_improvement")
        validate_nonnegative_number(
            self.per_iteration_budget_usd,
            "per_iteration_budget_usd",
        )
        validate_nonnegative_number(self.total_budget_usd, "total_budget_usd")
        if self.per_iteration_budget_usd <= 0 or self.total_budget_usd <= 0:
            raise HarnessContractError("budget limits must be positive")
        if self.per_iteration_budget_usd > self.total_budget_usd:
            raise HarnessContractError(
                "per-iteration budget cannot exceed total budget"
            )
        if not isinstance(self.approval_required, bool):
            raise HarnessContractError("approval_required must be a boolean")


@dataclass(frozen=True, slots=True)
class HarnessPolicyStep:
    decisions: tuple[DecisionRecord, ...]
    transitions: tuple[TransitionRecord, ...]
    snapshots: tuple[StateSnapshot, ...]

    def __post_init__(self) -> None:
        if not self.decisions or not self.transitions or not self.snapshots:
            raise HarnessContractError(
                "Harness policy step requires decisions, transitions, and snapshots"
            )
        if not (
            len(self.decisions) == len(self.transitions) == len(self.snapshots)
        ):
            raise HarnessContractError(
                "each Harness policy edge requires one Decision, Transition, and Snapshot"
            )
        for decision, transition, snapshot in zip(
            self.decisions,
            self.transitions,
            self.snapshots,
            strict=True,
        ):
            if transition.decision_id != decision.decision_id:
                raise HarnessContractError(
                    "Harness transition must reference its paired Decision"
                )
            if snapshot.metadata.get("decision_id") != decision.decision_id:
                raise HarnessContractError(
                    "Harness Snapshot must reference its paired Decision"
                )
            if not (decision.run_id == transition.run_id == snapshot.run_id):
                raise HarnessContractError(
                    "paired Harness records must belong to one Run"
                )

    @property
    def final_snapshot(self) -> StateSnapshot:
        return self.snapshots[-1]

    @property
    def terminal(self) -> bool:
        return self.final_snapshot.state in {
            ControlState.ABORTED,
            ControlState.STOPPED,
            ControlState.ROLLED_BACK,
        }

    @property
    def trace_handoff(self) -> bool:
        return self.final_snapshot.state is ControlState.HARVEST_TRACES


class HarnessOuterPolicy:
    """Pure frozen-model Harness mutation/evaluation/accept/plateau policy."""

    def __init__(self, limits: HarnessOuterLimits) -> None:
        self.limits = limits

    def start(
        self,
        *,
        run_id: str,
        cycle: int,
        active_model_checkpoint_id: str,
        active_harness: HarnessSpec,
        active_score: float,
        started_at: str,
        evidence_ids: tuple[str, ...],
        total_cost_usd: float = 0.0,
    ) -> HarnessPolicyStep:
        run_id = validate_id(run_id, "run_id")
        active_model_checkpoint_id = validate_id(
            active_model_checkpoint_id,
            "active_model_checkpoint_id",
        )
        validate_nonnegative_int(cycle, "cycle")
        validate_finite_number(active_score, "active_score")
        validate_nonnegative_number(total_cost_usd, "total_cost_usd")
        evidence_ids = validate_id_tuple(evidence_ids, "evidence_ids")
        if not evidence_ids:
            raise HarnessContractError("outer-loop start requires evidence_ids")

        frozen = self._edge(
            run_id=run_id,
            cycle=cycle,
            iteration=0,
            from_state=None,
            to_state=ControlState.FREEZE_MODEL,
            event=ControlEvent.START,
            action=DecisionAction.CONTINUE,
            subject_type=DecisionSubject.CHECKPOINT,
            subject_id=active_model_checkpoint_id,
            reason_code="freeze_active_model",
            reason="Freeze the accepted model while the Harness outer loop searches.",
            timestamp=started_at,
            evidence_ids=evidence_ids,
            active_checkpoint_id=active_model_checkpoint_id,
            peak_checkpoint_id=active_model_checkpoint_id,
            active_harness_id=active_harness.harness_id,
            candidate_harness_id=None,
            candidate_score=None,
            peak_score=active_score,
            plateau_count=0,
            total_cost_usd=total_cost_usd,
            stop_reason=None,
            metadata={
                "active_harness_sha256": active_harness.content_sha256,
                "active_harness_version": active_harness.version,
            },
        )
        mutate = self._edge_from_snapshot(
            frozen.final_snapshot,
            to_state=ControlState.MUTATE_HARNESS,
            event=ControlEvent.NEXT_ITERATION_REQUESTED,
            action=DecisionAction.CONTINUE,
            subject_type=DecisionSubject.HARNESS,
            subject_id=active_harness.harness_id,
            reason_code="begin_harness_search",
            reason="Begin the first Harness mutation attempt under the frozen model.",
            timestamp=started_at,
            evidence_ids=evidence_ids,
            next_iteration=1,
            active_harness_id=active_harness.harness_id,
            candidate_harness_id=None,
            candidate_score=None,
            peak_score=active_score,
            plateau_count=0,
            metadata={"frozen_model_checkpoint_id": active_model_checkpoint_id},
        )
        return _combine(frozen, mutate)

    def candidate_created(
        self,
        current: StateSnapshot,
        candidate: HarnessSpec,
        *,
        created_at: str,
        evidence_ids: tuple[str, ...],
    ) -> HarnessPolicyStep:
        self._require_state(current, ControlState.MUTATE_HARNESS)
        self._require_frozen_model(current)
        if current.active_harness_id is None:
            raise HarnessPolicyInvariantError("active Harness is missing")
        if candidate.parent_harness_id != current.active_harness_id:
            raise HarnessPolicyInvariantError(
                "Harness Candidate parent must equal the active Harness"
            )
        if candidate.harness_id == current.active_harness_id:
            raise HarnessPolicyInvariantError(
                "Harness Candidate must differ from the active Harness"
            )
        evidence_ids = validate_id_tuple(evidence_ids, "evidence_ids")
        if not evidence_ids:
            raise HarnessContractError("Harness mutation requires evidence_ids")

        return self._edge_from_snapshot(
            current,
            to_state=ControlState.VALIDATE_HARNESS,
            event=ControlEvent.HARNESS_MUTATED,
            action=DecisionAction.CONTINUE,
            subject_type=DecisionSubject.HARNESS,
            subject_id=candidate.harness_id,
            reason_code="harness_candidate_created",
            reason="A deterministic Harness mutation produced a Candidate for validation.",
            timestamp=created_at,
            evidence_ids=evidence_ids,
            active_harness_id=current.active_harness_id,
            candidate_harness_id=candidate.harness_id,
            candidate_score=None,
            peak_score=self._required_active_score(current),
            plateau_count=current.plateau_count,
            metadata={
                "candidate_harness_sha256": candidate.content_sha256,
                "candidate_harness_version": candidate.version,
                "parent_harness_id": candidate.parent_harness_id,
            },
        )

    def validation_completed(
        self,
        current: StateSnapshot,
        result: HarnessValidationResult,
    ) -> HarnessPolicyStep:
        self._require_state(current, ControlState.VALIDATE_HARNESS)
        self._require_frozen_model(current)
        self._require_candidate(current, result.candidate_harness_id)

        if result.valid:
            return self._edge_from_snapshot(
                current,
                to_state=ControlState.EVALUATE_HARNESS,
                event=ControlEvent.HARNESS_VALIDATED,
                action=DecisionAction.CONTINUE,
                subject_type=DecisionSubject.HARNESS,
                subject_id=result.candidate_harness_id,
                reason_code="harness_validation_passed",
                reason="Harness Candidate passed static and policy validation.",
                timestamp=result.validated_at,
                evidence_ids=result.evidence_ids,
                active_harness_id=current.active_harness_id,
                candidate_harness_id=result.candidate_harness_id,
                candidate_score=None,
                peak_score=self._required_active_score(current),
                plateau_count=current.plateau_count,
                metadata={"validation_metrics": dict(result.metrics)},
            )

        rejected = self._edge_from_snapshot(
            current,
            to_state=ControlState.REJECT_HARNESS,
            event=ControlEvent.HARNESS_INVALID,
            action=DecisionAction.REJECT,
            subject_type=DecisionSubject.HARNESS,
            subject_id=result.candidate_harness_id,
            reason_code="harness_validation_failed",
            reason="Harness Candidate failed static or policy validation.",
            timestamp=result.validated_at,
            evidence_ids=result.evidence_ids,
            active_harness_id=current.active_harness_id,
            candidate_harness_id=result.candidate_harness_id,
            candidate_score=None,
            peak_score=self._required_active_score(current),
            plateau_count=current.plateau_count + 1,
            metadata={
                "rejection_reasons": list(result.reasons),
                "validation_metrics": dict(result.metrics),
            },
        )
        return self._advance(rejected, timestamp=result.validated_at)

    def evaluation_completed(
        self,
        current: StateSnapshot,
        result: HarnessBenchmarkResult,
    ) -> HarnessPolicyStep:
        self._require_state(current, ControlState.EVALUATE_HARNESS)
        self._require_frozen_model(current)
        self._require_candidate(current, result.harness_id)
        active_score = self._required_active_score(current)
        next_total_cost = current.total_cost_usd + result.cost_usd

        if result.cost_usd > self.limits.per_iteration_budget_usd + _EPSILON:
            return self._budget_abort(
                current,
                result,
                next_total_cost=next_total_cost,
                stop_reason=StopReason.PER_ITERATION_BUDGET_EXCEEDED,
            )
        if next_total_cost > self.limits.total_budget_usd + _EPSILON:
            return self._budget_abort(
                current,
                result,
                next_total_cost=next_total_cost,
                stop_reason=StopReason.TOTAL_BUDGET_EXCEEDED,
            )

        score_delta = result.score - active_score
        evaluation_metadata: dict[str, JSONValue] = {
            "benchmark_id": result.benchmark_id,
            "candidate_score": result.score,
            "active_harness_score": active_score,
            "score_delta": score_delta,
            "min_improvement": self.limits.min_improvement,
            "task_family_scores": {
                key: value for key, value in result.task_family_scores.items()
            },
            "task_count": len(result.task_results),
            "cost_usd": result.cost_usd,
        }
        evaluation_metadata.update(result.metadata)

        if result.score > active_score + self.limits.min_improvement:
            if self.limits.approval_required:
                return self._edge_from_snapshot(
                    current,
                    to_state=ControlState.HARNESS_REVIEW_PENDING,
                    event=ControlEvent.HARNESS_REVIEW_REQUESTED,
                    action=DecisionAction.REQUEST_APPROVAL,
                    subject_type=DecisionSubject.HARNESS,
                    subject_id=result.harness_id,
                    reason_code="harness_improved_review_required",
                    reason=(
                        "Harness Candidate strictly improved under the frozen model and "
                        "requires human approval before acceptance."
                    ),
                    timestamp=result.evaluated_at,
                    evidence_ids=result.evidence_ids,
                    active_harness_id=current.active_harness_id,
                    candidate_harness_id=result.harness_id,
                    candidate_score=result.score,
                    peak_score=active_score,
                    plateau_count=current.plateau_count,
                    total_cost_usd=next_total_cost,
                    metadata=evaluation_metadata,
                )

            accepted = self._edge_from_snapshot(
                current,
                to_state=ControlState.ACCEPT_HARNESS,
                event=ControlEvent.HARNESS_IMPROVED,
                action=DecisionAction.ACCEPT,
                subject_type=DecisionSubject.HARNESS,
                subject_id=result.harness_id,
                reason_code="harness_strictly_improved",
                reason=(
                    "Harness Candidate exceeded the active Harness by more than the "
                    "configured minimum improvement."
                ),
                timestamp=result.evaluated_at,
                evidence_ids=result.evidence_ids,
                active_harness_id=result.harness_id,
                candidate_harness_id=result.harness_id,
                candidate_score=result.score,
                peak_score=result.score,
                plateau_count=0,
                total_cost_usd=next_total_cost,
                metadata=evaluation_metadata,
            )
            return self._advance(accepted, timestamp=result.evaluated_at)

        rejected = self._edge_from_snapshot(
            current,
            to_state=ControlState.REJECT_HARNESS,
            event=ControlEvent.HARNESS_NOT_IMPROVED,
            action=DecisionAction.REJECT,
            subject_type=DecisionSubject.HARNESS,
            subject_id=result.harness_id,
            reason_code="harness_not_above_active",
            reason=(
                "Harness Candidate did not exceed the active Harness by more than the "
                "configured minimum improvement."
            ),
            timestamp=result.evaluated_at,
            evidence_ids=result.evidence_ids,
            active_harness_id=current.active_harness_id,
            candidate_harness_id=result.harness_id,
            candidate_score=result.score,
            peak_score=active_score,
            plateau_count=current.plateau_count + 1,
            total_cost_usd=next_total_cost,
            metadata=evaluation_metadata,
        )
        return self._advance(rejected, timestamp=result.evaluated_at)

    def review_completed(
        self,
        current: StateSnapshot,
        review: HarnessReviewObservation,
    ) -> HarnessPolicyStep:
        self._require_state(current, ControlState.HARNESS_REVIEW_PENDING)
        self._require_frozen_model(current)
        self._require_candidate(current, review.candidate_harness_id)
        if current.candidate_score is None:
            raise HarnessPolicyInvariantError(
                "Harness review requires the evaluated Candidate score"
            )

        metadata: dict[str, JSONValue] = {
            "approval_request_id": review.request_id,
            "reviewer_id": review.reviewer_id,
            "reviewer_role": review.reviewer_role,
            "candidate_score": current.candidate_score,
            "active_harness_score": self._required_active_score(current),
        }
        if review.approved:
            accepted = self._edge_from_snapshot(
                current,
                to_state=ControlState.ACCEPT_HARNESS,
                event=ControlEvent.HARNESS_APPROVED,
                action=DecisionAction.ACCEPT,
                subject_type=DecisionSubject.HARNESS,
                subject_id=review.candidate_harness_id,
                reason_code="harness_approval_granted",
                reason="Authorized human review granted Harness acceptance authority.",
                timestamp=review.decided_at,
                evidence_ids=review.evidence_ids,
                active_harness_id=review.candidate_harness_id,
                candidate_harness_id=review.candidate_harness_id,
                candidate_score=current.candidate_score,
                peak_score=current.candidate_score,
                plateau_count=0,
                metadata=metadata,
            )
            return self._advance(accepted, timestamp=review.decided_at)

        rejected = self._edge_from_snapshot(
            current,
            to_state=ControlState.REJECT_HARNESS,
            event=ControlEvent.HARNESS_DENIED,
            action=DecisionAction.REJECT,
            subject_type=DecisionSubject.HARNESS,
            subject_id=review.candidate_harness_id,
            reason_code="harness_approval_not_granted",
            reason="Human review did not grant Harness acceptance authority.",
            timestamp=review.decided_at,
            evidence_ids=review.evidence_ids,
            active_harness_id=current.active_harness_id,
            candidate_harness_id=review.candidate_harness_id,
            candidate_score=current.candidate_score,
            peak_score=self._required_active_score(current),
            plateau_count=current.plateau_count + 1,
            metadata=metadata,
        )
        return self._advance(rejected, timestamp=review.decided_at)

    def _budget_abort(
        self,
        current: StateSnapshot,
        result: HarnessBenchmarkResult,
        *,
        next_total_cost: float,
        stop_reason: StopReason,
    ) -> HarnessPolicyStep:
        budget_name = (
            "per_iteration"
            if stop_reason is StopReason.PER_ITERATION_BUDGET_EXCEEDED
            else "total"
        )
        return self._edge_from_snapshot(
            current,
            to_state=ControlState.ABORTED,
            event=ControlEvent.BUDGET_EXCEEDED,
            action=DecisionAction.ABORT,
            subject_type=DecisionSubject.HARNESS,
            subject_id=result.harness_id,
            reason_code=f"harness_{budget_name}_budget_exceeded",
            reason=f"Harness evaluation crossed the {budget_name} budget boundary.",
            timestamp=result.evaluated_at,
            evidence_ids=result.evidence_ids,
            active_harness_id=current.active_harness_id,
            candidate_harness_id=result.harness_id,
            candidate_score=result.score,
            peak_score=self._required_active_score(current),
            plateau_count=current.plateau_count,
            total_cost_usd=next_total_cost,
            stop_reason=stop_reason,
            metadata={
                "iteration_cost_usd": result.cost_usd,
                "run_total_usd": next_total_cost,
                "per_iteration_limit_usd": self.limits.per_iteration_budget_usd,
                "total_limit_usd": self.limits.total_budget_usd,
            },
        )

    def _advance(
        self,
        intermediate: HarnessPolicyStep,
        *,
        timestamp: str,
    ) -> HarnessPolicyStep:
        current = intermediate.final_snapshot
        if current.state not in {
            ControlState.ACCEPT_HARNESS,
            ControlState.REJECT_HARNESS,
        }:
            raise HarnessPolicyInvariantError(
                "outer-loop advance requires ACCEPT_HARNESS or REJECT_HARNESS"
            )

        if current.plateau_count >= self.limits.plateau_patience:
            handoff = self._edge_from_snapshot(
                current,
                to_state=ControlState.HARVEST_TRACES,
                event=ControlEvent.HARNESS_PLATEAU_REACHED,
                action=DecisionAction.CONTINUE,
                subject_type=DecisionSubject.RUN,
                subject_id=current.run_id,
                reason_code="harness_plateau_trace_handoff",
                reason=(
                    "Harness search reached the configured plateau and hands control "
                    "to successful-trace harvesting."
                ),
                timestamp=timestamp,
                evidence_ids=current.evidence_ids,
                active_harness_id=current.active_harness_id,
                candidate_harness_id=None,
                candidate_score=None,
                peak_score=self._required_active_score(current),
                plateau_count=current.plateau_count,
                metadata={"handoff": "trace_harvesting"},
            )
            return _combine(intermediate, handoff)

        if current.iteration >= self.limits.max_iterations:
            handoff = self._edge_from_snapshot(
                current,
                to_state=ControlState.HARVEST_TRACES,
                event=ControlEvent.MAX_ITERATIONS_REACHED,
                action=DecisionAction.CONTINUE,
                subject_type=DecisionSubject.RUN,
                subject_id=current.run_id,
                reason_code="harness_iteration_limit_trace_handoff",
                reason=(
                    "Harness search reached its iteration limit and hands control to "
                    "successful-trace harvesting."
                ),
                timestamp=timestamp,
                evidence_ids=current.evidence_ids,
                active_harness_id=current.active_harness_id,
                candidate_harness_id=None,
                candidate_score=None,
                peak_score=self._required_active_score(current),
                plateau_count=current.plateau_count,
                metadata={"handoff": "trace_harvesting"},
            )
            return _combine(intermediate, handoff)

        next_attempt = self._edge_from_snapshot(
            current,
            to_state=ControlState.MUTATE_HARNESS,
            event=ControlEvent.NEXT_ITERATION_REQUESTED,
            action=DecisionAction.CONTINUE,
            subject_type=DecisionSubject.HARNESS,
            subject_id=current.active_harness_id or current.run_id,
            reason_code="continue_harness_search",
            reason="Continue Harness mutation search under the same frozen model.",
            timestamp=timestamp,
            evidence_ids=current.evidence_ids,
            next_iteration=current.iteration + 1,
            active_harness_id=current.active_harness_id,
            candidate_harness_id=None,
            candidate_score=None,
            peak_score=self._required_active_score(current),
            plateau_count=current.plateau_count,
            metadata={"frozen_model_checkpoint_id": current.active_checkpoint_id},
        )
        return _combine(intermediate, next_attempt)

    def _edge_from_snapshot(
        self,
        current: StateSnapshot,
        *,
        to_state: ControlState,
        event: ControlEvent,
        action: DecisionAction,
        subject_type: DecisionSubject,
        subject_id: str,
        reason_code: str,
        reason: str,
        timestamp: str,
        evidence_ids: tuple[str, ...],
        active_harness_id: str | None,
        candidate_harness_id: str | None,
        candidate_score: float | None,
        peak_score: float,
        plateau_count: int,
        total_cost_usd: float | None = None,
        stop_reason: StopReason | None = None,
        next_iteration: int | None = None,
        metadata: dict[str, JSONValue] | None = None,
    ) -> HarnessPolicyStep:
        return self._edge(
            run_id=current.run_id,
            cycle=current.cycle,
            iteration=current.iteration if next_iteration is None else next_iteration,
            from_state=current.state,
            to_state=to_state,
            event=event,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            reason_code=reason_code,
            reason=reason,
            timestamp=timestamp,
            evidence_ids=evidence_ids,
            active_checkpoint_id=current.active_checkpoint_id,
            peak_checkpoint_id=current.peak_checkpoint_id,
            active_harness_id=active_harness_id,
            candidate_harness_id=candidate_harness_id,
            candidate_score=candidate_score,
            peak_score=peak_score,
            plateau_count=plateau_count,
            total_cost_usd=(
                current.total_cost_usd
                if total_cost_usd is None
                else total_cost_usd
            ),
            stop_reason=stop_reason,
            metadata=metadata or {},
        )

    def _edge(
        self,
        *,
        run_id: str,
        cycle: int,
        iteration: int,
        from_state: ControlState | None,
        to_state: ControlState,
        event: ControlEvent,
        action: DecisionAction,
        subject_type: DecisionSubject,
        subject_id: str,
        reason_code: str,
        reason: str,
        timestamp: str,
        evidence_ids: tuple[str, ...],
        active_checkpoint_id: str | None,
        peak_checkpoint_id: str | None,
        active_harness_id: str | None,
        candidate_harness_id: str | None,
        candidate_score: float | None,
        peak_score: float,
        plateau_count: int,
        total_cost_usd: float,
        stop_reason: StopReason | None,
        metadata: dict[str, JSONValue],
    ) -> HarnessPolicyStep:
        subject_id = validate_id(subject_id, "subject_id")
        evidence_ids = validate_id_tuple(evidence_ids, "evidence_ids")
        if not evidence_ids:
            raise HarnessContractError("Harness policy edge requires evidence_ids")
        validate_nonnegative_int(iteration, "iteration")
        validate_nonnegative_int(cycle, "cycle")
        validate_finite_number(peak_score, "peak_score")
        validate_nonnegative_int(plateau_count, "plateau_count")
        validate_nonnegative_number(total_cost_usd, "total_cost_usd")
        phase = to_state.value.lower()
        record_seed = (
            candidate_harness_id
            or active_harness_id
            or subject_id
        )
        decision_id = _record_id(
            "harness-decision",
            run_id,
            cycle,
            iteration,
            phase,
            record_seed,
        )
        transition_id = _record_id(
            "harness-transition",
            run_id,
            cycle,
            iteration,
            phase,
            record_seed,
        )
        snapshot_id = _record_id(
            "harness-snapshot",
            run_id,
            cycle,
            iteration,
            phase,
            record_seed,
        )
        decision_metadata: dict[str, JSONValue] = dict(metadata)
        decision_metadata.update(
            {
                "frozen_model_checkpoint_id": active_checkpoint_id,
                "active_harness_id": active_harness_id,
                "candidate_harness_id": candidate_harness_id,
            }
        )
        decision = DecisionRecord(
            decision_id=decision_id,
            run_id=run_id,
            iteration=iteration,
            subject_type=subject_type,
            subject_id=subject_id,
            action=action,
            reason_code=reason_code,
            reason=reason,
            evidence_ids=evidence_ids,
            created_at=timestamp,
            stop_reason=stop_reason,
            metadata=decision_metadata,
        )
        transition = TransitionRecord(
            transition_id=transition_id,
            run_id=run_id,
            iteration=iteration,
            from_state=from_state,
            event=event,
            to_state=to_state,
            occurred_at=timestamp,
            idempotency_key=_record_id(
                "harness-idempotency",
                run_id,
                cycle,
                iteration,
                phase,
                record_seed,
            ),
            decision_id=decision_id,
            evidence_ids=evidence_ids,
            metadata={
                "cycle": cycle,
                "subject_type": subject_type.value,
                "subject_id": subject_id,
            },
        )
        snapshot_metadata: dict[str, JSONValue] = dict(metadata)
        snapshot_metadata["decision_id"] = decision_id
        snapshot = StateSnapshot(
            snapshot_id=snapshot_id,
            run_id=run_id,
            iteration=iteration,
            cycle=cycle,
            state=to_state,
            entered_at=timestamp,
            active_checkpoint_id=active_checkpoint_id,
            candidate_checkpoint_id=None,
            peak_checkpoint_id=peak_checkpoint_id,
            active_harness_id=active_harness_id,
            candidate_harness_id=candidate_harness_id,
            candidate_score=candidate_score,
            peak_score=peak_score,
            plateau_count=plateau_count,
            total_cost_usd=total_cost_usd,
            stop_reason=stop_reason,
            evidence_ids=evidence_ids,
            metadata=snapshot_metadata,
        )
        return HarnessPolicyStep(
            decisions=(decision,),
            transitions=(transition,),
            snapshots=(snapshot,),
        )

    @staticmethod
    def _require_state(current: StateSnapshot, expected: ControlState) -> None:
        if current.state is not expected:
            raise HarnessPolicyInvariantError(
                f"Harness policy requires {expected.value}, found {current.state.value}"
            )

    @staticmethod
    def _require_frozen_model(current: StateSnapshot) -> None:
        if current.active_checkpoint_id is None:
            raise HarnessPolicyInvariantError("frozen model Checkpoint is missing")
        if current.active_checkpoint_id != current.peak_checkpoint_id:
            raise HarnessPolicyInvariantError(
                "active model must equal the accepted Peak during Harness search"
            )

    @staticmethod
    def _require_candidate(current: StateSnapshot, candidate_harness_id: str) -> None:
        if current.candidate_harness_id != candidate_harness_id:
            raise HarnessPolicyInvariantError(
                "Harness observation does not match the active Candidate"
            )

    @staticmethod
    def _required_active_score(current: StateSnapshot) -> float:
        if current.peak_score is None:
            raise HarnessPolicyInvariantError("active Harness score is missing")
        validate_finite_number(current.peak_score, "peak_score")
        return current.peak_score


def _record_id(
    prefix: str,
    run_id: str,
    cycle: int,
    iteration: int,
    phase: str,
    subject_id: str,
) -> str:
    payload = (
        f"{prefix}|{run_id}|{cycle}|{iteration}|{phase}|{subject_id}"
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _combine(*steps: HarnessPolicyStep) -> HarnessPolicyStep:
    return HarnessPolicyStep(
        decisions=tuple(
            decision for step in steps for decision in step.decisions
        ),
        transitions=tuple(
            transition for step in steps for transition in step.transitions
        ),
        snapshots=tuple(
            snapshot for step in steps for snapshot in step.snapshots
        ),
    )
