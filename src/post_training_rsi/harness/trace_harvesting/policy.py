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
from .contracts import HarvestedTraceBatch, TraceContractError, TraceDatasetResult

_EPSILON = 1e-12


class TracePolicyInvariantError(TraceContractError):
    """Raised when trace harvesting contradicts the frozen model/Harness lineage."""


@dataclass(frozen=True, slots=True)
class TraceHarvestLimits:
    target_traces: int = 12
    max_batches: int = 4
    min_acceptance_rate: float = 0.25
    per_batch_budget_usd: float = 5.0
    total_budget_usd: float = 100.0

    def __post_init__(self) -> None:
        validate_nonnegative_int(self.target_traces, "target_traces")
        validate_nonnegative_int(self.max_batches, "max_batches")
        if self.target_traces < 1:
            raise TraceContractError("target_traces must be positive")
        if self.max_batches < 1:
            raise TraceContractError("max_batches must be positive")
        validate_finite_number(self.min_acceptance_rate, "min_acceptance_rate")
        if not 0.0 <= self.min_acceptance_rate <= 1.0:
            raise TraceContractError("min_acceptance_rate must be in [0, 1]")
        validate_nonnegative_number(
            self.per_batch_budget_usd,
            "per_batch_budget_usd",
        )
        validate_nonnegative_number(self.total_budget_usd, "total_budget_usd")
        if self.per_batch_budget_usd <= 0 or self.total_budget_usd <= 0:
            raise TraceContractError("budget limits must be positive")
        if self.per_batch_budget_usd > self.total_budget_usd:
            raise TraceContractError(
                "per-batch budget cannot exceed total budget"
            )


@dataclass(frozen=True, slots=True)
class TraceHarvestPolicyStep:
    decisions: tuple[DecisionRecord, ...]
    transitions: tuple[TransitionRecord, ...]
    snapshots: tuple[StateSnapshot, ...]

    def __post_init__(self) -> None:
        if not self.decisions or not self.transitions or not self.snapshots:
            raise TraceContractError(
                "Trace policy step requires Decisions, Transitions, and Snapshots"
            )
        if not (
            len(self.decisions) == len(self.transitions) == len(self.snapshots)
        ):
            raise TraceContractError(
                "each Trace policy edge requires one Decision, Transition, and Snapshot"
            )
        for decision, transition, snapshot in zip(
            self.decisions,
            self.transitions,
            self.snapshots,
            strict=True,
        ):
            if transition.decision_id != decision.decision_id:
                raise TraceContractError(
                    "Trace transition must reference its paired Decision"
                )
            if snapshot.metadata.get("decision_id") != decision.decision_id:
                raise TraceContractError(
                    "Trace Snapshot must reference its paired Decision"
                )
            if not (decision.run_id == transition.run_id == snapshot.run_id):
                raise TraceContractError(
                    "paired Trace records must belong to one Run"
                )

    @property
    def final_snapshot(self) -> StateSnapshot:
        return self.snapshots[-1]

    @property
    def training_handoff(self) -> bool:
        return self.final_snapshot.state is ControlState.TRAIN_MODEL

    @property
    def terminal(self) -> bool:
        return self.final_snapshot.state in {
            ControlState.STOPPED,
            ControlState.ABORTED,
        }


class TraceHarvestPolicy:
    """Pure successful-trace harvest, verification, quarantine, and handoff policy."""

    def __init__(self, limits: TraceHarvestLimits) -> None:
        self.limits = limits

    def batch_harvested(
        self,
        current: StateSnapshot,
        batch: HarvestedTraceBatch,
    ) -> TraceHarvestPolicyStep:
        self._require_state(current, ControlState.HARVEST_TRACES)
        self._validate_frozen_lineage(current)
        self._validate_batch_lineage(current, batch)
        next_total_cost = current.total_cost_usd + batch.cost_usd

        if batch.cost_usd > self.limits.per_batch_budget_usd + _EPSILON:
            return self._budget_abort(
                current,
                batch,
                next_total_cost=next_total_cost,
                stop_reason=StopReason.PER_ITERATION_BUDGET_EXCEEDED,
            )
        if next_total_cost > self.limits.total_budget_usd + _EPSILON:
            return self._budget_abort(
                current,
                batch,
                next_total_cost=next_total_cost,
                stop_reason=StopReason.TOTAL_BUDGET_EXCEEDED,
            )

        verified_before = _metadata_int(current, "verified_trace_count", 0)
        batch_count_before = _metadata_int(current, "harvest_batch_count", 0)
        return self._edge_from_snapshot(
            current,
            to_state=ControlState.VERIFY_TRACES,
            event=ControlEvent.TRACE_BATCH_HARVESTED,
            action=DecisionAction.CONTINUE,
            subject_type=DecisionSubject.TRACE_BATCH,
            subject_id=batch.batch_id,
            reason_code="observable_trace_batch_harvested",
            reason=(
                "Successful observable trajectories were selected for the common "
                "verification pipeline."
            ),
            timestamp=batch.created_at,
            evidence_ids=batch.evidence_ids,
            total_cost_usd=next_total_cost,
            decision_stop_reason=None,
            snapshot_stop_reason=None,
            metadata={
                "trace_batch_id": batch.batch_id,
                "selected_trace_count": batch.selected_count,
                "harvest_rejected_count": batch.rejected_count,
                "verified_trace_count": verified_before,
                "harvest_batch_count": batch_count_before + 1,
                "selection_seed": batch.selection_seed,
                "model_checkpoint_id": batch.model_checkpoint_id,
                "harness_id": batch.harness_id,
            },
        )

    def verification_completed(
        self,
        current: StateSnapshot,
        result: TraceDatasetResult,
    ) -> TraceHarvestPolicyStep:
        self._require_state(current, ControlState.VERIFY_TRACES)
        self._validate_frozen_lineage(current)
        self._validate_dataset_lineage(current, result)
        expected_batch_id = current.metadata.get("trace_batch_id")
        if expected_batch_id != result.batch_id:
            raise TracePolicyInvariantError(
                "Trace Dataset result does not match the harvested batch"
            )

        verified_before = _metadata_int(current, "verified_trace_count", 0)
        batch_count = _metadata_int(current, "harvest_batch_count", 0)
        verified_after = verified_before + result.accepted_count
        common_metadata: dict[str, JSONValue] = {
            "trace_batch_id": result.batch_id,
            "trace_dataset_id": result.dataset_id,
            "trace_dataset_path": result.dataset_path,
            "trace_dataset_sha256": result.dataset_sha256,
            "trace_audit_path": result.audit_path,
            "raw_trace_example_count": result.raw_count,
            "accepted_trace_count": result.accepted_count,
            "rejected_trace_count": result.rejected_count,
            "acceptance_rate": result.acceptance_rate,
            "rejection_counts": dict(result.rejection_counts),
            "verified_trace_count": verified_after,
            "harvest_batch_count": batch_count,
            "model_checkpoint_id": result.model_checkpoint_id,
            "harness_id": result.harness_id,
        }

        if result.accepted_count == 0:
            quarantined = self._edge_from_snapshot(
                current,
                to_state=ControlState.QUARANTINED,
                event=ControlEvent.DATASET_QUARANTINED,
                action=DecisionAction.QUARANTINE,
                subject_type=DecisionSubject.TRACE_BATCH,
                subject_id=result.batch_id,
                reason_code="trace_batch_no_accepted_data",
                reason=(
                    "No harvested Trace example passed the common data gates."
                ),
                timestamp=result.created_at,
                evidence_ids=result.evidence_ids,
                decision_stop_reason=StopReason.NO_ACCEPTED_DATA,
                snapshot_stop_reason=None,
                metadata=common_metadata,
            )
            return self._after_quarantine(quarantined, timestamp=result.created_at)

        if result.acceptance_rate + _EPSILON < self.limits.min_acceptance_rate:
            quarantined = self._edge_from_snapshot(
                current,
                to_state=ControlState.QUARANTINED,
                event=ControlEvent.DATASET_QUARANTINED,
                action=DecisionAction.QUARANTINE,
                subject_type=DecisionSubject.DATASET,
                subject_id=result.dataset_id,
                reason_code="trace_dataset_low_acceptance",
                reason=(
                    "Verified Trace Dataset acceptance rate fell below the configured "
                    "minimum."
                ),
                timestamp=result.created_at,
                evidence_ids=result.evidence_ids,
                decision_stop_reason=StopReason.LOW_ACCEPTANCE,
                snapshot_stop_reason=None,
                metadata=common_metadata,
            )
            return self._after_quarantine(quarantined, timestamp=result.created_at)

        if verified_after >= self.limits.target_traces:
            return self._edge_from_snapshot(
                current,
                to_state=ControlState.TRAIN_MODEL,
                event=ControlEvent.TRACE_TARGET_REACHED,
                action=DecisionAction.CONTINUE,
                subject_type=DecisionSubject.DATASET,
                subject_id=result.dataset_id,
                reason_code="verified_trace_target_reached",
                reason=(
                    "The cumulative verified observable Trace count reached the model "
                    "inner-loop training target."
                ),
                timestamp=result.created_at,
                evidence_ids=result.evidence_ids,
                decision_stop_reason=None,
                snapshot_stop_reason=None,
                metadata=common_metadata,
            )

        if batch_count >= self.limits.max_batches:
            return self._edge_from_snapshot(
                current,
                to_state=ControlState.STOPPED,
                event=ControlEvent.MAX_ITERATIONS_REACHED,
                action=DecisionAction.STOP,
                subject_type=DecisionSubject.RUN,
                subject_id=current.run_id,
                reason_code="trace_harvest_batch_limit_reached",
                reason=(
                    "Trace harvesting reached its batch limit before the verified "
                    "training target."
                ),
                timestamp=result.created_at,
                evidence_ids=result.evidence_ids,
                decision_stop_reason=StopReason.MAX_ITERATIONS,
                snapshot_stop_reason=StopReason.MAX_ITERATIONS,
                metadata=common_metadata,
            )

        return self._edge_from_snapshot(
            current,
            to_state=ControlState.HARVEST_TRACES,
            event=ControlEvent.TRACE_BATCH_VERIFIED,
            action=DecisionAction.CONTINUE,
            subject_type=DecisionSubject.DATASET,
            subject_id=result.dataset_id,
            reason_code="continue_trace_harvesting",
            reason=(
                "The Trace Dataset passed verification, but more accepted examples are "
                "required before model training."
            ),
            timestamp=result.created_at,
            evidence_ids=result.evidence_ids,
            next_iteration=current.iteration + 1,
            decision_stop_reason=None,
            snapshot_stop_reason=None,
            metadata=common_metadata,
        )

    def _after_quarantine(
        self,
        quarantine_step: TraceHarvestPolicyStep,
        *,
        timestamp: str,
    ) -> TraceHarvestPolicyStep:
        current = quarantine_step.final_snapshot
        batch_count = _metadata_int(current, "harvest_batch_count", 0)
        if batch_count >= self.limits.max_batches:
            stopped = self._edge_from_snapshot(
                current,
                to_state=ControlState.STOPPED,
                event=ControlEvent.MAX_ITERATIONS_REACHED,
                action=DecisionAction.STOP,
                subject_type=DecisionSubject.RUN,
                subject_id=current.run_id,
                reason_code="trace_harvest_batch_limit_after_quarantine",
                reason=(
                    "Trace harvesting exhausted its batch limit without a valid "
                    "training Dataset."
                ),
                timestamp=timestamp,
                evidence_ids=current.evidence_ids,
                decision_stop_reason=StopReason.MAX_ITERATIONS,
                snapshot_stop_reason=StopReason.MAX_ITERATIONS,
                metadata=dict(current.metadata),
            )
            return _combine(quarantine_step, stopped)

        retry = self._edge_from_snapshot(
            current,
            to_state=ControlState.HARVEST_TRACES,
            event=ControlEvent.NEXT_ITERATION_REQUESTED,
            action=DecisionAction.CONTINUE,
            subject_type=DecisionSubject.RUN,
            subject_id=current.run_id,
            reason_code="retry_trace_harvesting_after_quarantine",
            reason=(
                "The rejected Trace batch is quarantined; collect another observable "
                "batch under the same frozen model and active Harness."
            ),
            timestamp=timestamp,
            evidence_ids=current.evidence_ids,
            next_iteration=current.iteration + 1,
            decision_stop_reason=None,
            snapshot_stop_reason=None,
            metadata=dict(current.metadata),
        )
        return _combine(quarantine_step, retry)

    def _budget_abort(
        self,
        current: StateSnapshot,
        batch: HarvestedTraceBatch,
        *,
        next_total_cost: float,
        stop_reason: StopReason,
    ) -> TraceHarvestPolicyStep:
        budget_name = (
            "per_batch"
            if stop_reason is StopReason.PER_ITERATION_BUDGET_EXCEEDED
            else "total"
        )
        return self._edge_from_snapshot(
            current,
            to_state=ControlState.ABORTED,
            event=ControlEvent.BUDGET_EXCEEDED,
            action=DecisionAction.ABORT,
            subject_type=DecisionSubject.TRACE_BATCH,
            subject_id=batch.batch_id,
            reason_code=f"trace_{budget_name}_budget_exceeded",
            reason=f"Trace harvesting crossed the {budget_name} budget boundary.",
            timestamp=batch.created_at,
            evidence_ids=batch.evidence_ids,
            total_cost_usd=next_total_cost,
            decision_stop_reason=stop_reason,
            snapshot_stop_reason=stop_reason,
            metadata={
                "trace_batch_id": batch.batch_id,
                "batch_cost_usd": batch.cost_usd,
                "run_total_usd": next_total_cost,
                "per_batch_limit_usd": self.limits.per_batch_budget_usd,
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
        decision_stop_reason: StopReason | None,
        snapshot_stop_reason: StopReason | None,
        metadata: dict[str, JSONValue],
        next_iteration: int | None = None,
        total_cost_usd: float | None = None,
    ) -> TraceHarvestPolicyStep:
        iteration = current.iteration if next_iteration is None else next_iteration
        validate_nonnegative_int(iteration, "iteration")
        evidence_ids = validate_id_tuple(evidence_ids, "evidence_ids")
        if not evidence_ids:
            raise TraceContractError("Trace policy edge requires evidence_ids")
        subject_id = validate_id(subject_id, "subject_id")
        record_subject = (
            metadata.get("trace_batch_id")
            or metadata.get("trace_dataset_id")
            or subject_id
        )
        if not isinstance(record_subject, str):
            record_subject = subject_id
        phase = to_state.value.lower()
        decision_id = _record_id(
            "trace-decision",
            current.run_id,
            current.cycle,
            iteration,
            phase,
            record_subject,
        )
        transition_id = _record_id(
            "trace-transition",
            current.run_id,
            current.cycle,
            iteration,
            phase,
            record_subject,
        )
        snapshot_id = _record_id(
            "trace-snapshot",
            current.run_id,
            current.cycle,
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
                "frozen_model_checkpoint_id": current.active_checkpoint_id,
                "active_harness_id": current.active_harness_id,
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
                "trace-idempotency",
                current.run_id,
                current.cycle,
                iteration,
                phase,
                record_subject,
            ),
            decision_id=decision_id,
            evidence_ids=evidence_ids,
            metadata={
                "cycle": current.cycle,
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
            cycle=current.cycle,
            state=to_state,
            entered_at=timestamp,
            active_checkpoint_id=current.active_checkpoint_id,
            candidate_checkpoint_id=None,
            peak_checkpoint_id=current.peak_checkpoint_id,
            active_harness_id=current.active_harness_id,
            candidate_harness_id=None,
            candidate_score=None,
            peak_score=current.peak_score,
            plateau_count=current.plateau_count,
            total_cost_usd=(
                current.total_cost_usd
                if total_cost_usd is None
                else total_cost_usd
            ),
            stop_reason=snapshot_stop_reason,
            evidence_ids=evidence_ids,
            metadata=snapshot_metadata,
        )
        return TraceHarvestPolicyStep(
            decisions=(decision,),
            transitions=(transition,),
            snapshots=(snapshot,),
        )

    @staticmethod
    def _require_state(current: StateSnapshot, expected: ControlState) -> None:
        if current.state is not expected:
            raise TracePolicyInvariantError(
                f"Trace policy requires {expected.value}, found {current.state.value}"
            )

    @staticmethod
    def _validate_frozen_lineage(current: StateSnapshot) -> None:
        if current.active_checkpoint_id is None:
            raise TracePolicyInvariantError("frozen model Checkpoint is missing")
        if current.active_checkpoint_id != current.peak_checkpoint_id:
            raise TracePolicyInvariantError(
                "active model must equal the accepted Peak during trace harvesting"
            )
        if current.active_harness_id is None:
            raise TracePolicyInvariantError("active Harness is missing")

    @staticmethod
    def _validate_batch_lineage(
        current: StateSnapshot,
        batch: HarvestedTraceBatch,
    ) -> None:
        if batch.run_id != current.run_id or batch.cycle != current.cycle:
            raise TracePolicyInvariantError("Trace batch Run/cycle mismatch")
        if batch.model_checkpoint_id != current.active_checkpoint_id:
            raise TracePolicyInvariantError("Trace batch model mismatch")
        if batch.harness_id != current.active_harness_id:
            raise TracePolicyInvariantError("Trace batch Harness mismatch")

    @staticmethod
    def _validate_dataset_lineage(
        current: StateSnapshot,
        result: TraceDatasetResult,
    ) -> None:
        if result.run_id != current.run_id or result.cycle != current.cycle:
            raise TracePolicyInvariantError("Trace Dataset Run/cycle mismatch")
        if result.model_checkpoint_id != current.active_checkpoint_id:
            raise TracePolicyInvariantError("Trace Dataset model mismatch")
        if result.harness_id != current.active_harness_id:
            raise TracePolicyInvariantError("Trace Dataset Harness mismatch")


def _metadata_int(snapshot: StateSnapshot, key: str, default: int) -> int:
    value = snapshot.metadata.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TracePolicyInvariantError(f"Snapshot metadata.{key} must be an integer")
    validate_nonnegative_int(value, f"metadata.{key}")
    return value


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


def _combine(*steps: TraceHarvestPolicyStep) -> TraceHarvestPolicyStep:
    return TraceHarvestPolicyStep(
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
