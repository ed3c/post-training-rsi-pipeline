from __future__ import annotations

import hashlib
import math
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
    ModelBenchmarkObservation,
    ModelCandidateArtifact,
    ModelInnerContractError,
    ModelPromotionCommitObservation,
    ModelReviewObservation,
    ModelRollbackCommitObservation,
)

_EPSILON = 1e-12


class ModelInnerPolicyInvariantError(ModelInnerContractError):
    """Raised when model-inner-loop observations contradict accepted lineage."""


@dataclass(frozen=True, slots=True)
class ModelInnerLimits:
    min_improvement: float = 0.005
    regression_tolerance: float = 0.05
    per_stage_budget_usd: float = 30.0
    total_budget_usd: float = 100.0
    approval_required: bool = False

    def __post_init__(self) -> None:
        validate_nonnegative_number(self.min_improvement, "min_improvement")
        validate_nonnegative_number(
            self.regression_tolerance,
            "regression_tolerance",
        )
        validate_nonnegative_number(
            self.per_stage_budget_usd,
            "per_stage_budget_usd",
        )
        validate_nonnegative_number(self.total_budget_usd, "total_budget_usd")
        if self.per_stage_budget_usd <= 0 or self.total_budget_usd <= 0:
            raise ModelInnerContractError("budget limits must be positive")
        if self.per_stage_budget_usd > self.total_budget_usd:
            raise ModelInnerContractError(
                "per-stage budget cannot exceed total budget"
            )
        if not isinstance(self.approval_required, bool):
            raise ModelInnerContractError("approval_required must be a boolean")


@dataclass(frozen=True, slots=True)
class ModelInnerPolicyStep:
    decisions: tuple[DecisionRecord, ...]
    transitions: tuple[TransitionRecord, ...]
    snapshots: tuple[StateSnapshot, ...]

    def __post_init__(self) -> None:
        if not self.decisions or not self.transitions or not self.snapshots:
            raise ModelInnerContractError(
                "model policy step requires Decisions, Transitions, and Snapshots"
            )
        if not (
            len(self.decisions) == len(self.transitions) == len(self.snapshots)
        ):
            raise ModelInnerContractError(
                "each model policy edge requires one Decision, Transition, and Snapshot"
            )
        for decision, transition, snapshot in zip(
            self.decisions,
            self.transitions,
            self.snapshots,
            strict=True,
        ):
            if transition.decision_id != decision.decision_id:
                raise ModelInnerContractError(
                    "model transition must reference its paired Decision"
                )
            if snapshot.metadata.get("decision_id") != decision.decision_id:
                raise ModelInnerContractError(
                    "model Snapshot must reference its paired Decision"
                )
            if not (decision.run_id == transition.run_id == snapshot.run_id):
                raise ModelInnerContractError(
                    "paired model records must belong to one Run"
                )

    @property
    def final_snapshot(self) -> StateSnapshot:
        return self.snapshots[-1]

    @property
    def slim_harness_handoff(self) -> bool:
        return self.final_snapshot.state is ControlState.SLIM_HARNESS

    @property
    def next_cycle_handoff(self) -> bool:
        return self.final_snapshot.state is ControlState.FREEZE_MODEL

    @property
    def terminal(self) -> bool:
        return self.final_snapshot.state is ControlState.ABORTED


class ModelInnerPolicy:
    """Pure Trace-Dataset train/evaluate/review/promote/rollback policy."""

    def __init__(self, limits: ModelInnerLimits) -> None:
        self.limits = limits

    def training_completed(
        self,
        current: StateSnapshot,
        candidate: ModelCandidateArtifact,
    ) -> ModelInnerPolicyStep:
        self._require_state(current, ControlState.TRAIN_MODEL)
        self._validate_active_lineage(current)
        self._validate_candidate_lineage(current, candidate)
        next_total_cost = current.total_cost_usd + candidate.training_cost_usd

        if candidate.training_cost_usd > self.limits.per_stage_budget_usd + _EPSILON:
            return self._budget_abort(
                current,
                subject_id=candidate.checkpoint_id,
                stage="training",
                stage_cost_usd=candidate.training_cost_usd,
                next_total_cost=next_total_cost,
                timestamp=candidate.trained_at,
                evidence_ids=candidate.evidence_ids,
                stop_reason=StopReason.PER_ITERATION_BUDGET_EXCEEDED,
            )
        if next_total_cost > self.limits.total_budget_usd + _EPSILON:
            return self._budget_abort(
                current,
                subject_id=candidate.checkpoint_id,
                stage="training",
                stage_cost_usd=candidate.training_cost_usd,
                next_total_cost=next_total_cost,
                timestamp=candidate.trained_at,
                evidence_ids=candidate.evidence_ids,
                stop_reason=StopReason.TOTAL_BUDGET_EXCEEDED,
            )

        metadata: dict[str, JSONValue] = {
            "training_request_id": candidate.request_id,
            "candidate_checkpoint_id": candidate.checkpoint_id,
            "candidate_model_id": candidate.model_id,
            "parent_checkpoint_id": candidate.parent_checkpoint_id,
            "trace_dataset_id": candidate.dataset_id,
            "trace_dataset_sha256": candidate.dataset_sha256,
            "artifact_path": candidate.artifact_path,
            "artifact_sha256": candidate.artifact_sha256,
            "training_loss": candidate.training_loss,
            "training_cost_usd": candidate.training_cost_usd,
            "active_model_score": self._active_model_score(current),
        }
        metadata.update(candidate.metadata)
        return self._edge_from_snapshot(
            current,
            to_state=ControlState.EVALUATE_MODEL,
            event=ControlEvent.MODEL_TRAINED,
            action=DecisionAction.CONTINUE,
            subject_type=DecisionSubject.CHECKPOINT,
            subject_id=candidate.checkpoint_id,
            reason_code="trace_dataset_model_candidate_trained",
            reason=(
                "A Candidate model was trained from the verified Trace Dataset and "
                "passed artifact-lineage validation."
            ),
            timestamp=candidate.trained_at,
            evidence_ids=candidate.evidence_ids,
            candidate_checkpoint_id=candidate.checkpoint_id,
            candidate_score=None,
            active_checkpoint_id=current.active_checkpoint_id,
            peak_checkpoint_id=current.peak_checkpoint_id,
            total_cost_usd=next_total_cost,
            decision_stop_reason=None,
            snapshot_stop_reason=None,
            metadata=metadata,
        )

    def evaluation_completed(
        self,
        current: StateSnapshot,
        observation: ModelBenchmarkObservation,
    ) -> ModelInnerPolicyStep:
        self._require_state(current, ControlState.EVALUATE_MODEL)
        self._validate_active_lineage(current)
        self._validate_evaluation_lineage(current, observation)
        next_total_cost = current.total_cost_usd + observation.evaluation_cost_usd

        if observation.evaluation_cost_usd > self.limits.per_stage_budget_usd + _EPSILON:
            return self._budget_abort(
                current,
                subject_id=observation.checkpoint_id,
                stage="evaluation",
                stage_cost_usd=observation.evaluation_cost_usd,
                next_total_cost=next_total_cost,
                timestamp=observation.evaluated_at,
                evidence_ids=observation.evidence_ids,
                stop_reason=StopReason.PER_ITERATION_BUDGET_EXCEEDED,
            )
        if next_total_cost > self.limits.total_budget_usd + _EPSILON:
            return self._budget_abort(
                current,
                subject_id=observation.checkpoint_id,
                stage="evaluation",
                stage_cost_usd=observation.evaluation_cost_usd,
                next_total_cost=next_total_cost,
                timestamp=observation.evaluated_at,
                evidence_ids=observation.evidence_ids,
                stop_reason=StopReason.TOTAL_BUDGET_EXCEEDED,
            )

        active_score = self._active_model_score(current)
        score_delta = observation.score - active_score
        regression = max(0.0, active_score - observation.score)
        metadata: dict[str, JSONValue] = {
            # Preserve the exact trained Candidate identity so an
            # immutable approval pause can reconstruct and resume it.
            **dict(current.metadata),
            "candidate_checkpoint_id": observation.checkpoint_id,
            "parent_checkpoint_id": observation.parent_checkpoint_id,
            "benchmark_id": observation.benchmark_id,
            "candidate_model_score": observation.score,
            "active_model_score": active_score,
            "score_delta": score_delta,
            "min_improvement": self.limits.min_improvement,
            "regression": regression,
            "regression_tolerance": self.limits.regression_tolerance,
            "task_family_scores": dict(observation.task_family_scores),
            "failure_trace_uris": list(observation.failure_trace_uris),
            "evaluation_cost_usd": observation.evaluation_cost_usd,
            "endpoint": observation.endpoint,
        }
        metadata.update(observation.metadata)

        if observation.score > active_score + self.limits.min_improvement:
            if self.limits.approval_required:
                return self._edge_from_snapshot(
                    current,
                    to_state=ControlState.MODEL_REVIEW_PENDING,
                    event=ControlEvent.MODEL_REVIEW_REQUESTED,
                    action=DecisionAction.REQUEST_APPROVAL,
                    subject_type=DecisionSubject.CHECKPOINT,
                    subject_id=observation.checkpoint_id,
                    reason_code="model_improved_review_required",
                    reason=(
                        "Candidate model strictly improved and requires immutable human "
                        "promotion authority."
                    ),
                    timestamp=observation.evaluated_at,
                    evidence_ids=observation.evidence_ids,
                    candidate_checkpoint_id=observation.checkpoint_id,
                    candidate_score=observation.score,
                    active_checkpoint_id=current.active_checkpoint_id,
                    peak_checkpoint_id=current.peak_checkpoint_id,
                    total_cost_usd=next_total_cost,
                    decision_stop_reason=None,
                    snapshot_stop_reason=None,
                    metadata=metadata,
                )
            return self._promotion_decision(
                current,
                checkpoint_id=observation.checkpoint_id,
                score=observation.score,
                timestamp=observation.evaluated_at,
                evidence_ids=observation.evidence_ids,
                total_cost_usd=next_total_cost,
                reason_code="model_strictly_improved",
                reason=(
                    "Candidate model exceeded the accepted model by more than the "
                    "configured minimum improvement."
                ),
                event=ControlEvent.MODEL_IMPROVED,
                metadata=metadata,
            )

        regressed = regression > self.limits.regression_tolerance + _EPSILON
        return self._rollback_decision(
            current,
            checkpoint_id=observation.checkpoint_id,
            score=observation.score,
            timestamp=observation.evaluated_at,
            evidence_ids=observation.evidence_ids,
            total_cost_usd=next_total_cost,
            reason_code=(
                "model_regressed_beyond_tolerance"
                if regressed
                else "model_not_above_active"
            ),
            reason=(
                "Candidate model regressed beyond tolerance; keep the accepted model."
                if regressed
                else (
                    "Candidate model did not strictly improve; keep the accepted model."
                )
            ),
            event=(
                ControlEvent.REGRESSION_DETECTED
                if regressed
                else ControlEvent.MODEL_NOT_IMPROVED
            ),
            decision_stop_reason=(
                StopReason.REGRESSION_ROLLBACK if regressed else None
            ),
            metadata=metadata,
        )

    def review_completed(
        self,
        current: StateSnapshot,
        review: ModelReviewObservation,
    ) -> ModelInnerPolicyStep:
        self._require_state(current, ControlState.MODEL_REVIEW_PENDING)
        self._validate_active_lineage(current)
        if current.candidate_checkpoint_id != review.checkpoint_id:
            raise ModelInnerPolicyInvariantError(
                "model review does not target the active Candidate"
            )
        if current.candidate_score is None:
            raise ModelInnerPolicyInvariantError(
                "model review requires an evaluated Candidate score"
            )
        active_score = self._active_model_score(current)
        if current.candidate_score <= active_score + self.limits.min_improvement:
            raise ModelInnerPolicyInvariantError(
                "review-pending Candidate no longer satisfies strict improvement"
            )
        metadata: dict[str, JSONValue] = {
            **dict(current.metadata),
            "approval_request_id": review.request_id,
            "reviewer_id": review.reviewer_id,
            "reviewer_role": review.reviewer_role,
        }
        if review.approved:
            return self._promotion_decision(
                current,
                checkpoint_id=review.checkpoint_id,
                score=current.candidate_score,
                timestamp=review.decided_at,
                evidence_ids=review.evidence_ids,
                total_cost_usd=current.total_cost_usd,
                reason_code="model_promotion_approval_granted",
                reason=(
                    "Authorized human review granted Candidate model promotion."
                ),
                event=ControlEvent.MODEL_APPROVED,
                metadata=metadata,
            )
        return self._rollback_decision(
            current,
            checkpoint_id=review.checkpoint_id,
            score=current.candidate_score,
            timestamp=review.decided_at,
            evidence_ids=review.evidence_ids,
            total_cost_usd=current.total_cost_usd,
            reason_code="model_promotion_approval_not_granted",
            reason="Human review did not grant model promotion authority.",
            event=ControlEvent.MODEL_DENIED,
            decision_stop_reason=StopReason.APPROVAL_NOT_GRANTED,
            metadata=metadata,
        )

    def promotion_committed(
        self,
        current: StateSnapshot,
        observation: ModelPromotionCommitObservation,
    ) -> ModelInnerPolicyStep:
        self._require_state(current, ControlState.PROMOTE_MODEL)
        self._validate_active_lineage(current)
        if current.candidate_checkpoint_id != observation.checkpoint_id:
            raise ModelInnerPolicyInvariantError(
                "promotion commit targets a different Candidate"
            )
        if current.active_checkpoint_id != observation.previous_checkpoint_id:
            raise ModelInnerPolicyInvariantError(
                "promotion commit previous Checkpoint mismatch"
            )
        if current.candidate_score is None or not math.isclose(
            current.candidate_score,
            observation.score,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ModelInnerPolicyInvariantError(
                "promotion commit score does not match the Candidate evaluation"
            )
        return self._edge_from_snapshot(
            current,
            to_state=ControlState.SLIM_HARNESS,
            event=ControlEvent.MODEL_HOT_SWAPPED,
            action=DecisionAction.CONTINUE,
            subject_type=DecisionSubject.CHECKPOINT,
            subject_id=observation.checkpoint_id,
            reason_code="model_promotion_committed",
            reason=(
                "The Candidate Checkpoint bundle and Peak update were committed and the "
                "model was hot-swapped; hand off to Harness slimming."
            ),
            timestamp=observation.committed_at,
            evidence_ids=observation.evidence_ids,
            candidate_checkpoint_id=None,
            candidate_score=None,
            active_checkpoint_id=observation.checkpoint_id,
            peak_checkpoint_id=observation.checkpoint_id,
            total_cost_usd=current.total_cost_usd,
            decision_stop_reason=None,
            snapshot_stop_reason=None,
            metadata={
                **dict(current.metadata),
                "previous_checkpoint_id": observation.previous_checkpoint_id,
                "active_model_score": observation.score,
                "checkpoint_bundle_sha256": observation.checkpoint_bundle_sha256,
                "handoff": "slim_harness",
            },
        )

    def rollback_committed(
        self,
        current: StateSnapshot,
        observation: ModelRollbackCommitObservation,
    ) -> ModelInnerPolicyStep:
        self._require_state(current, ControlState.ROLLBACK_MODEL)
        self._validate_active_lineage(current)
        if current.candidate_checkpoint_id != observation.rejected_checkpoint_id:
            raise ModelInnerPolicyInvariantError(
                "rollback commit targets a different Candidate"
            )
        if current.active_checkpoint_id != observation.active_checkpoint_id:
            raise ModelInnerPolicyInvariantError(
                "rollback commit changed the accepted model"
            )
        return self._edge_from_snapshot(
            current,
            to_state=ControlState.FREEZE_MODEL,
            event=ControlEvent.ROLLBACK_COMPLETED,
            action=DecisionAction.CONTINUE,
            subject_type=DecisionSubject.CHECKPOINT,
            subject_id=observation.active_checkpoint_id,
            reason_code="model_rollback_committed",
            reason=(
                "The rejected Candidate was quarantined and the accepted model remains "
                "active for the next Co-Evolution cycle."
            ),
            timestamp=observation.completed_at,
            evidence_ids=observation.evidence_ids,
            candidate_checkpoint_id=None,
            candidate_score=None,
            active_checkpoint_id=current.active_checkpoint_id,
            peak_checkpoint_id=current.peak_checkpoint_id,
            total_cost_usd=current.total_cost_usd,
            decision_stop_reason=None,
            snapshot_stop_reason=None,
            next_cycle=current.cycle + 1,
            next_iteration=0,
            metadata={
                **dict(current.metadata),
                "rejected_checkpoint_id": observation.rejected_checkpoint_id,
                "handoff": "freeze_model",
            },
        )

    def _promotion_decision(
        self,
        current: StateSnapshot,
        *,
        checkpoint_id: str,
        score: float,
        timestamp: str,
        evidence_ids: tuple[str, ...],
        total_cost_usd: float,
        reason_code: str,
        reason: str,
        event: ControlEvent,
        metadata: dict[str, JSONValue],
    ) -> ModelInnerPolicyStep:
        return self._edge_from_snapshot(
            current,
            to_state=ControlState.PROMOTE_MODEL,
            event=event,
            action=DecisionAction.PROMOTE,
            subject_type=DecisionSubject.CHECKPOINT,
            subject_id=checkpoint_id,
            reason_code=reason_code,
            reason=reason,
            timestamp=timestamp,
            evidence_ids=evidence_ids,
            candidate_checkpoint_id=checkpoint_id,
            candidate_score=score,
            active_checkpoint_id=current.active_checkpoint_id,
            peak_checkpoint_id=current.peak_checkpoint_id,
            total_cost_usd=total_cost_usd,
            decision_stop_reason=None,
            snapshot_stop_reason=None,
            metadata=metadata,
        )

    def _rollback_decision(
        self,
        current: StateSnapshot,
        *,
        checkpoint_id: str,
        score: float,
        timestamp: str,
        evidence_ids: tuple[str, ...],
        total_cost_usd: float,
        reason_code: str,
        reason: str,
        event: ControlEvent,
        decision_stop_reason: StopReason | None,
        metadata: dict[str, JSONValue],
    ) -> ModelInnerPolicyStep:
        return self._edge_from_snapshot(
            current,
            to_state=ControlState.ROLLBACK_MODEL,
            event=event,
            action=DecisionAction.ROLLBACK,
            subject_type=DecisionSubject.CHECKPOINT,
            subject_id=checkpoint_id,
            reason_code=reason_code,
            reason=reason,
            timestamp=timestamp,
            evidence_ids=evidence_ids,
            candidate_checkpoint_id=checkpoint_id,
            candidate_score=score,
            active_checkpoint_id=current.active_checkpoint_id,
            peak_checkpoint_id=current.peak_checkpoint_id,
            total_cost_usd=total_cost_usd,
            decision_stop_reason=decision_stop_reason,
            snapshot_stop_reason=None,
            metadata=metadata,
        )

    def _budget_abort(
        self,
        current: StateSnapshot,
        *,
        subject_id: str,
        stage: str,
        stage_cost_usd: float,
        next_total_cost: float,
        timestamp: str,
        evidence_ids: tuple[str, ...],
        stop_reason: StopReason,
    ) -> ModelInnerPolicyStep:
        budget_name = (
            "per_stage"
            if stop_reason is StopReason.PER_ITERATION_BUDGET_EXCEEDED
            else "total"
        )
        return self._edge_from_snapshot(
            current,
            to_state=ControlState.ABORTED,
            event=ControlEvent.BUDGET_EXCEEDED,
            action=DecisionAction.ABORT,
            subject_type=DecisionSubject.CHECKPOINT,
            subject_id=subject_id,
            reason_code=f"model_{stage}_{budget_name}_budget_exceeded",
            reason=f"Model {stage} crossed the {budget_name} budget boundary.",
            timestamp=timestamp,
            evidence_ids=evidence_ids,
            candidate_checkpoint_id=(
                subject_id
                if subject_id != current.active_checkpoint_id
                else current.candidate_checkpoint_id
            ),
            candidate_score=current.candidate_score,
            active_checkpoint_id=current.active_checkpoint_id,
            peak_checkpoint_id=current.peak_checkpoint_id,
            total_cost_usd=next_total_cost,
            decision_stop_reason=stop_reason,
            snapshot_stop_reason=stop_reason,
            metadata={
                **dict(current.metadata),
                "budget_stage": stage,
                "stage_cost_usd": stage_cost_usd,
                "run_total_usd": next_total_cost,
                "per_stage_limit_usd": self.limits.per_stage_budget_usd,
                "total_limit_usd": self.limits.total_budget_usd,
            },
        )

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
        candidate_checkpoint_id: str | None,
        candidate_score: float | None,
        active_checkpoint_id: str | None,
        peak_checkpoint_id: str | None,
        total_cost_usd: float,
        decision_stop_reason: StopReason | None,
        snapshot_stop_reason: StopReason | None,
        metadata: dict[str, JSONValue],
        next_cycle: int | None = None,
        next_iteration: int | None = None,
    ) -> ModelInnerPolicyStep:
        cycle = current.cycle if next_cycle is None else next_cycle
        iteration = current.iteration if next_iteration is None else next_iteration
        validate_nonnegative_int(cycle, "cycle")
        validate_nonnegative_int(iteration, "iteration")
        validate_nonnegative_number(total_cost_usd, "total_cost_usd")
        subject_id = validate_id(subject_id, "subject_id")
        evidence_ids = validate_id_tuple(evidence_ids, "evidence_ids")
        if not evidence_ids:
            raise ModelInnerContractError("model policy edge requires evidence_ids")
        record_subject = candidate_checkpoint_id or active_checkpoint_id or subject_id
        phase = to_state.value.lower()
        decision_id = _record_id(
            "model-decision",
            current.run_id,
            cycle,
            iteration,
            phase,
            record_subject,
        )
        transition_id = _record_id(
            "model-transition",
            current.run_id,
            cycle,
            iteration,
            phase,
            record_subject,
        )
        snapshot_id = _record_id(
            "model-snapshot",
            current.run_id,
            cycle,
            iteration,
            phase,
            record_subject,
        )
        decision = DecisionRecord(
            decision_id=decision_id,
            run_id=current.run_id,
            iteration=iteration,
            subject_type=subject_type,
            subject_id=subject_id,
            action=action,
            reason_code=reason_code,
            reason=reason,
            evidence_ids=evidence_ids,
            created_at=timestamp,
            stop_reason=decision_stop_reason,
            metadata={
                **metadata,
                "cycle": cycle,
                "active_checkpoint_id": active_checkpoint_id,
                "candidate_checkpoint_id": candidate_checkpoint_id,
            },
        )
        transition = TransitionRecord(
            transition_id=transition_id,
            run_id=current.run_id,
            iteration=iteration,
            from_state=current.state,
            event=event,
            to_state=to_state,
            occurred_at=timestamp,
            idempotency_key=_record_id(
                "model-idempotency",
                current.run_id,
                cycle,
                iteration,
                phase,
                record_subject,
            ),
            decision_id=decision_id,
            evidence_ids=evidence_ids,
            metadata={
                "cycle": cycle,
                "subject_type": subject_type.value,
                "subject_id": subject_id,
            },
        )
        snapshot_metadata = dict(metadata)
        snapshot_metadata["decision_id"] = decision_id
        snapshot = StateSnapshot(
            snapshot_id=snapshot_id,
            run_id=current.run_id,
            iteration=iteration,
            cycle=cycle,
            state=to_state,
            entered_at=timestamp,
            active_checkpoint_id=active_checkpoint_id,
            candidate_checkpoint_id=candidate_checkpoint_id,
            peak_checkpoint_id=peak_checkpoint_id,
            active_harness_id=current.active_harness_id,
            candidate_harness_id=None,
            candidate_score=candidate_score,
            peak_score=current.peak_score,
            plateau_count=current.plateau_count,
            total_cost_usd=total_cost_usd,
            stop_reason=snapshot_stop_reason,
            evidence_ids=evidence_ids,
            metadata=snapshot_metadata,
        )
        return ModelInnerPolicyStep(
            decisions=(decision,),
            transitions=(transition,),
            snapshots=(snapshot,),
        )

    @staticmethod
    def _require_state(current: StateSnapshot, expected: ControlState) -> None:
        if current.state is not expected:
            raise ModelInnerPolicyInvariantError(
                f"model policy requires {expected.value}, found {current.state.value}"
            )

    @staticmethod
    def _validate_active_lineage(current: StateSnapshot) -> None:
        if current.active_checkpoint_id is None:
            raise ModelInnerPolicyInvariantError("active model Checkpoint is missing")
        if current.active_checkpoint_id != current.peak_checkpoint_id:
            raise ModelInnerPolicyInvariantError(
                "active model must equal the accepted Peak"
            )
        if current.active_harness_id is None:
            raise ModelInnerPolicyInvariantError("active Harness is missing")

    @staticmethod
    def _active_model_score(current: StateSnapshot) -> float:
        value = current.metadata.get("active_model_score")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ModelInnerPolicyInvariantError(
                "Snapshot metadata.active_model_score must be a number"
            )
        score = float(value)
        validate_finite_number(score, "active_model_score")
        if not 0.0 <= score <= 1.0:
            raise ModelInnerPolicyInvariantError(
                "active_model_score must be in [0, 1]"
            )
        return score

    def _validate_candidate_lineage(
        self,
        current: StateSnapshot,
        candidate: ModelCandidateArtifact,
    ) -> None:
        if candidate.run_id != current.run_id or candidate.cycle != current.cycle:
            raise ModelInnerPolicyInvariantError("Candidate Run/cycle mismatch")
        if candidate.parent_checkpoint_id != current.active_checkpoint_id:
            raise ModelInnerPolicyInvariantError(
                "Candidate parent must equal the accepted model"
            )
        expected_dataset_id = current.metadata.get("trace_dataset_id")
        expected_dataset_sha256 = current.metadata.get("trace_dataset_sha256")
        if expected_dataset_id != candidate.dataset_id:
            raise ModelInnerPolicyInvariantError("Trace Dataset ID mismatch")
        if expected_dataset_sha256 != candidate.dataset_sha256:
            raise ModelInnerPolicyInvariantError("Trace Dataset SHA-256 mismatch")

    def _validate_evaluation_lineage(
        self,
        current: StateSnapshot,
        observation: ModelBenchmarkObservation,
    ) -> None:
        if observation.run_id != current.run_id or observation.cycle != current.cycle:
            raise ModelInnerPolicyInvariantError("evaluation Run/cycle mismatch")
        if observation.checkpoint_id != current.candidate_checkpoint_id:
            raise ModelInnerPolicyInvariantError(
                "evaluation does not target the active Candidate"
            )
        if observation.parent_checkpoint_id != current.active_checkpoint_id:
            raise ModelInnerPolicyInvariantError(
                "evaluation parent does not equal the accepted model"
            )


def _record_id(
    prefix: str,
    run_id: str,
    cycle: int,
    iteration: int,
    phase: str,
    subject_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{prefix}|{run_id}|{cycle}|{iteration}|{phase}|{subject_id}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"{prefix}-{digest[:24]}"
