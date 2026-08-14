from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..adapter_runtime.errors import AdapterError
from ..adapter_runtime.factory import AdapterRuntime, build_adapter_runtime
from ..adapter_runtime.integrity import canonical_sha256, sha256_path
from ..adapter_runtime.lifecycle import evaluate_checkpoint_with_serving
from ..approval import (
    ApprovalCandidate,
    ApprovalDecisionBundle,
    ApprovalPolicy,
    ApprovalService,
    ApprovalState,
    ApprovalStore,
    record_sha256,
)
from ..config import PipelineConfig
from ..control_plane import (
    ControlEvent,
    ControlState,
    DecisionAction,
    DecisionRecord,
    DecisionSubject,
    EvidenceKind,
    EvidenceRecord,
    StateSnapshot,
    StopReason,
    TransitionRecord,
)
from ..control_plane.validation import canonical_json, validate_id
from ..cost import APICircuitOpen, BudgetExceeded, CostLedger
from ..lineage import (
    ArtifactStore,
    CheckpointBundle,
    CheckpointBundleStore,
    ControlRecordStore,
    LineageManifest,
    PeakPointer,
    PeakPointerStore,
    QuarantineMarker,
    QuarantineStore,
)
from ..lineage._io import LineageConflictError, LineageIntegrityError
from ..models import EvaluationResult, SyntheticExample, TrainingResult
from ..verification.pipeline import VerificationPipeline
from .evidence import RSIEvidenceFactory, write_canonical_json
from .rsi_policy import (
    CandidateObservation,
    RSIDecisionPolicy,
    RSIPolicyLimits,
    RSIPolicyStep,
)
from .run_state import (
    LogicalRunClock,
    RunMetadataStore,
    config_sha256,
    latest_snapshot,
)

_TERMINAL_STATES = {
    ControlState.COMPLETED,
    ControlState.STOPPED,
    ControlState.ABORTED,
    ControlState.ROLLED_BACK,
}


@dataclass(frozen=True, slots=True)
class ConvergedRSIResult:
    run_id: str
    status: str
    state: str
    iteration: int
    peak_checkpoint_id: str | None
    peak_score: float | None
    total_cost_usd: float
    stop_reason: str | None
    pending_approval_request_id: str | None
    latest_snapshot_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "state": self.state,
            "iteration": self.iteration,
            "peak_checkpoint_id": self.peak_checkpoint_id,
            "peak_score": self.peak_score,
            "total_cost_usd": self.total_cost_usd,
            "stop_reason": self.stop_reason,
            "pending_approval_request_id": self.pending_approval_request_id,
            "latest_snapshot_id": self.latest_snapshot_id,
        }


class ConvergedRSIController:
    """Evidence-first multi-iteration RSI controller composed from PR #3-#6."""

    def __init__(
        self,
        config: PipelineConfig,
        *,
        workspace: str | Path,
        run_id: str = "rsi-run-default",
        adapter_runtime: AdapterRuntime | None = None,
        verifier: VerificationPipeline | None = None,
        started_at: str | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.run_id = validate_id(run_id, "run_id")
        self.now = now or _utc_now
        self.artifacts = ArtifactStore(self.workspace)
        self.control_store = ControlRecordStore(self.workspace)
        self.checkpoint_store = CheckpointBundleStore(
            self.workspace,
            control_store=self.control_store,
        )
        self.peak_store = PeakPointerStore(
            self.workspace,
            control_store=self.control_store,
            checkpoint_store=self.checkpoint_store,
        )
        self.quarantine_store = QuarantineStore(
            self.workspace,
            control_store=self.control_store,
        )
        self.runtime = adapter_runtime or build_adapter_runtime(
            config,
            workspace=self.workspace,
            clock=self.now,
        )
        self.verifier = verifier or VerificationPipeline(
            config.verification,
            benchmark_texts=config.benchmark_texts,
        )
        self._warm_verifier_history()
        self.policy = RSIDecisionPolicy(
            RSIPolicyLimits.from_config(
                config.rsi,
                config.budget,
                regression_tolerance=config.rsi.regression_tolerance,
            )
        )
        self.approval_policy = ApprovalPolicy(
            policy_id="hitl-rsi-v1",
            dataset_review_required=(
                config.approval.dataset_review_required
            ),
            checkpoint_review_required=(
                config.approval.checkpoint_review_required
            ),
            harness_review_required=(
                config.approval.harness_review_required
            ),
            sample_rate=config.approval.sample_rate,
            min_sample_items=config.approval.min_sample_items,
            max_sample_items=config.approval.max_sample_items,
            decision_ttl_seconds=config.approval.decision_ttl_seconds,
            allowed_reviewer_roles=(
                config.approval.allowed_reviewer_roles
            ),
        )
        self.approval_service = ApprovalService(
            store=ApprovalStore(self.workspace),
            policy=self.approval_policy,
            clock=self.now,
        )
        self.metadata = RunMetadataStore(self.workspace).load_or_create(
            run_id=self.run_id,
            config_sha256=config_sha256(config.to_dict()),
            started_at=started_at or self.now(),
            code_git_commit=self.artifacts.git_commit_hash(),
        )
        self.clock = LogicalRunClock(self.metadata.started_at)
        self.evidence = RSIEvidenceFactory(run_id=self.run_id)

    def run(self) -> ConvergedRSIResult:
        snapshot = latest_snapshot(self.control_store, run_id=self.run_id)
        if snapshot is None:
            snapshot = self._bootstrap()
        snapshot = self._reconcile_snapshot(snapshot)

        maximum_steps = self.config.rsi.max_iterations * 4 + 12
        for _ in range(maximum_steps):
            if snapshot.state in _TERMINAL_STATES:
                return self._finish(snapshot)
            if snapshot.state is ControlState.DATA_REVIEW_PENDING:
                next_snapshot = self._resume_data_review(snapshot)
                if next_snapshot.snapshot_id == snapshot.snapshot_id:
                    return self._finish(snapshot)
                snapshot = self._reconcile_snapshot(next_snapshot)
                continue
            if snapshot.state is ControlState.MODEL_REVIEW_PENDING:
                next_snapshot = self._resume_model_review(snapshot)
                if next_snapshot.snapshot_id == snapshot.snapshot_id:
                    return self._finish(snapshot)
                snapshot = self._reconcile_snapshot(next_snapshot)
                continue
            if snapshot.state is ControlState.TRAIN:
                snapshot = self._reconcile_snapshot(
                    self._train_evaluate_decide(snapshot)
                )
                continue
            if snapshot.state is ControlState.DIAGNOSE:
                snapshot = self._reconcile_snapshot(
                    self._run_iteration(snapshot)
                )
                continue
            raise LineageIntegrityError(
                f"unsupported resumable RSI state: {snapshot.state.value}"
            )
        raise RuntimeError("RSI controller exceeded its bounded transition budget")

    def _bootstrap(self) -> StateSnapshot:
        existing_peak = self.peak_store.load()
        if existing_peak is not None:
            raise LineageIntegrityError(
                "Peak exists but no resumable StateSnapshot was found"
            )
        timestamp = self.clock.at(iteration=0, offset_seconds=1)
        descriptor = {
            "format": "model-reference-v1",
            "model_id": self.config.model_id,
            "role": "initial-active-model",
            "run_id": self.run_id,
            "config_sha256": self.metadata.config_sha256,
        }
        checkpoint_id = f"ckpt-base-{canonical_sha256(descriptor)[:20]}"
        artifact_path = write_canonical_json(
            self.workspace
            / "model_artifacts"
            / checkpoint_id
            / "model-reference.json",
            descriptor,
        )
        artifact_sha256 = sha256_path(artifact_path)
        checkpoint_evidence = EvidenceRecord(
            evidence_id=f"ev.rsi.bootstrap.checkpoint.{artifact_sha256[:24]}",
            run_id=self.run_id,
            iteration=0,
            kind=EvidenceKind.CHECKPOINT,
            producer="orchestration.rsi.bootstrap",
            uri=artifact_path.as_uri(),
            created_at=timestamp,
            sha256=artifact_sha256,
            metadata={
                "checkpoint_id": checkpoint_id,
                "model_id": self.config.model_id,
                "role": "initial-active-model",
            },
        )
        evaluation_payload = {
            "checkpoint_id": checkpoint_id,
            "benchmark_id": self.config.rsi.benchmark_id,
            "score": self.config.rsi.initial_score,
            "source": "configured-initial-score",
        }
        evaluation_evidence = self.evidence.inline(
            iteration=0,
            kind=EvidenceKind.EVALUATION_RESULT,
            stage="bootstrap-evaluation",
            value=evaluation_payload,
            created_at=timestamp,
        )
        evidence_ids = (
            checkpoint_evidence.evidence_id,
            evaluation_evidence.evidence_id,
        )
        promote_decision = DecisionRecord(
            decision_id=_id("decision", self.run_id, 0, "bootstrap-promote"),
            run_id=self.run_id,
            iteration=0,
            subject_type=DecisionSubject.CHECKPOINT,
            subject_id=checkpoint_id,
            action=DecisionAction.PROMOTE,
            reason_code="bootstrap-peak",
            reason="Initialize the immutable historical Peak from the configured base model.",
            evidence_ids=evidence_ids,
            created_at=timestamp,
            metadata={"initial_score": self.config.rsi.initial_score},
        )
        promote_transition = TransitionRecord(
            transition_id=_id("transition", self.run_id, 0, "bootstrap-promote"),
            run_id=self.run_id,
            iteration=0,
            from_state=None,
            event=ControlEvent.START,
            to_state=ControlState.PROMOTED,
            occurred_at=timestamp,
            idempotency_key=_id("idempotency", self.run_id, 0, "bootstrap"),
            decision_id=promote_decision.decision_id,
            evidence_ids=evidence_ids,
        )
        promoted_snapshot = StateSnapshot(
            snapshot_id=_id("snapshot", self.run_id, 0, "bootstrap-promoted"),
            run_id=self.run_id,
            iteration=0,
            cycle=0,
            state=ControlState.PROMOTED,
            entered_at=timestamp,
            active_checkpoint_id=checkpoint_id,
            peak_checkpoint_id=checkpoint_id,
            peak_score=self.config.rsi.initial_score,
            total_cost_usd=0.0,
            evidence_ids=evidence_ids,
            metadata={"decision_id": promote_decision.decision_id},
        )
        next_timestamp = self.clock.at(iteration=0, offset_seconds=2)
        continue_decision = DecisionRecord(
            decision_id=_id("decision", self.run_id, 0, "bootstrap-continue"),
            run_id=self.run_id,
            iteration=0,
            subject_type=DecisionSubject.RUN,
            subject_id=self.run_id,
            action=DecisionAction.CONTINUE,
            reason_code="bootstrap-complete",
            reason="Begin the first diagnostic iteration from the accepted base Peak.",
            evidence_ids=evidence_ids,
            created_at=next_timestamp,
        )
        continue_transition = TransitionRecord(
            transition_id=_id("transition", self.run_id, 0, "bootstrap-diagnose"),
            run_id=self.run_id,
            iteration=0,
            from_state=ControlState.PROMOTED,
            event=ControlEvent.NEXT_ITERATION_REQUESTED,
            to_state=ControlState.DIAGNOSE,
            occurred_at=next_timestamp,
            idempotency_key=_id(
                "idempotency",
                self.run_id,
                0,
                "bootstrap-diagnose",
            ),
            decision_id=continue_decision.decision_id,
            evidence_ids=evidence_ids,
        )
        diagnose_snapshot = StateSnapshot(
            snapshot_id=_id("snapshot", self.run_id, 1, "diagnose"),
            run_id=self.run_id,
            iteration=1,
            cycle=0,
            state=ControlState.DIAGNOSE,
            entered_at=next_timestamp,
            active_checkpoint_id=checkpoint_id,
            peak_checkpoint_id=checkpoint_id,
            peak_score=self.config.rsi.initial_score,
            plateau_count=0,
            total_cost_usd=0.0,
            evidence_ids=evidence_ids,
            metadata={"decision_id": continue_decision.decision_id},
        )
        transaction_id = _id("tx", self.run_id, 0, "bootstrap")
        transaction = self.control_store.commit(
            transaction_id=transaction_id,
            run_id=self.run_id,
            iteration=0,
            created_at=next_timestamp,
            records=(
                checkpoint_evidence,
                evaluation_evidence,
                promote_decision,
                promote_transition,
                promoted_snapshot,
                continue_decision,
                continue_transition,
                diagnose_snapshot,
            ),
        )
        empty_hash = hashlib.sha256(b"").hexdigest()
        lineage = LineageManifest(
            checkpoint_id=checkpoint_id,
            run_id=self.run_id,
            iteration=0,
            model_id=self.config.model_id,
            parent_checkpoint_id=None,
            dataset_commit_hash=empty_hash,
            dataset_path="bootstrap://empty-dataset",
            teacher_model="bootstrap",
            teacher_api_version="bootstrap-v1",
            teacher_prompt_hash=empty_hash,
            filter_config_version="bootstrap-v1",
            rejected_data_count=0,
            training_loss_final=0.0,
            benchmark_id=self.config.rsi.benchmark_id,
            benchmark_score=self.config.rsi.initial_score,
            code_git_commit=self.metadata.code_git_commit,
            status="PEAK",
            artifact_sha256=artifact_sha256,
            control_transaction_id=transaction.transaction_id,
            created_at=next_timestamp,
        )
        bundle = self.checkpoint_store.commit(
            checkpoint_payload={
                "checkpoint_id": checkpoint_id,
                "run_id": self.run_id,
                "iteration": 0,
                "model_id": self.config.model_id,
                "parent_checkpoint_id": None,
                "dataset_commit_hash": empty_hash,
                "final_loss": 0.0,
                "benchmark_score": self.config.rsi.initial_score,
                "status": "PEAK",
                "artifact_sha256": artifact_sha256,
                "control_transaction_id": transaction.transaction_id,
                "code_git_commit": self.metadata.code_git_commit,
                "metadata": {"role": "initial-active-model"},
            },
            checkpoint_id=checkpoint_id,
            run_id=self.run_id,
            iteration=0,
            lineage_manifest=lineage,
            artifact_path=artifact_path,
            artifact_uri=artifact_path.resolve().as_uri(),
            control_transaction_id=transaction.transaction_id,
            created_at=next_timestamp,
        )
        self.peak_store.compare_and_swap(
            PeakPointer(
                checkpoint_id=checkpoint_id,
                previous_checkpoint_id=None,
                run_id=self.run_id,
                iteration=0,
                model_id=self.config.model_id,
                score=self.config.rsi.initial_score,
                checkpoint_bundle_sha256=bundle.manifest_sha256,
                control_transaction_id=transaction.transaction_id,
                decision_id=promote_decision.decision_id,
                updated_at=next_timestamp,
            ),
            expected_previous_checkpoint_id=None,
        )
        return diagnose_snapshot

    def _run_iteration(self, current: StateSnapshot) -> StateSnapshot:
        iteration = current.iteration
        if iteration > self.config.rsi.max_iterations:
            return self._stop_without_candidate(
                current,
                stop_reason=StopReason.MAX_ITERATIONS,
                reason_code="max-iterations-before-synthesis",
                reason="The run reached the configured maximum before synthesis.",
            )
        diagnostic = self._diagnostic(current)
        hypothesis = self._hypothesis(current, diagnostic)
        iteration_dir = self.artifacts.iteration_dir(iteration)
        diagnostic_path = write_canonical_json(
            iteration_dir / "diagnostic.json",
            diagnostic,
        )
        hypothesis_path = write_canonical_json(
            iteration_dir / "hypothesis.json",
            hypothesis,
        )
        diagnostic_time = self.clock.at(iteration=iteration, offset_seconds=10)
        hypothesis_time = self.clock.at(iteration=iteration, offset_seconds=20)
        diagnostic_evidence = self.evidence.file(
            iteration=iteration,
            kind=EvidenceKind.DIAGNOSTIC_REPORT,
            stage="diagnostic",
            path=diagnostic_path,
            created_at=diagnostic_time,
            metadata=diagnostic,
        )
        hypothesis_evidence = self.evidence.file(
            iteration=iteration,
            kind=EvidenceKind.HYPOTHESIS,
            stage="hypothesis",
            path=hypothesis_path,
            created_at=hypothesis_time,
            metadata=hypothesis,
        )
        ledger = CostLedger(self.config.budget)
        ledger.total_charged_usd = current.total_cost_usd
        try:
            batch = self.runtime.teacher.synthesize(
                hypothesis=str(hypothesis["text"]),
                count=self.config.rsi.examples_per_iteration,
                iteration=iteration,
            )
            ledger.charge(
                batch.estimated_cost_usd,
                iteration=iteration,
                category="teacher_synthesis",
            )
        except BudgetExceeded as exc:
            return self._abort_before_candidate(
                current,
                evidence=(diagnostic_evidence, hypothesis_evidence),
                stop_reason=_budget_stop_reason(str(exc)),
                reason=str(exc),
            )
        except APICircuitOpen as exc:
            return self._abort_before_candidate(
                current,
                evidence=(diagnostic_evidence, hypothesis_evidence),
                stop_reason=StopReason.PROVIDER_CIRCUIT_OPEN,
                reason=str(exc),
            )
        except Exception as exc:
            return self._abort_before_candidate(
                current,
                evidence=(diagnostic_evidence, hypothesis_evidence),
                stop_reason=StopReason.INTERNAL_ERROR,
                reason=f"Teacher synthesis failed: {type(exc).__name__}",
            )

        verification = self.verifier.verify(batch.examples)
        dataset_path, dataset_hash = self.artifacts.write_iteration_bundle(
            iteration=iteration,
            raw_examples=list(batch.examples),
            verification=verification,
            synthesis_manifest=batch.manifest(),
        )
        cost_path = self.artifacts.write_json(
            iteration_dir / "cost_ledger.json",
            ledger.snapshot(),
        )
        synthesis_time = self.clock.at(iteration=iteration, offset_seconds=30)
        verify_time = self.clock.at(iteration=iteration, offset_seconds=40)
        synthesis_evidence = self.runtime.evidence.synthesis(
            batch,
            run_id=self.run_id,
            iteration=iteration,
            manifest_uri=(iteration_dir / "synthesis_manifest.json").as_uri(),
        )
        raw_evidence = self.evidence.file(
            iteration=iteration,
            kind=EvidenceKind.RAW_DATASET,
            stage="raw-dataset",
            path=iteration_dir / "raw.jsonl",
            created_at=synthesis_time,
            metadata={"example_count": len(batch.examples)},
        )
        verification_evidence = self.evidence.file(
            iteration=iteration,
            kind=EvidenceKind.VERIFICATION_AUDIT,
            stage="verification-audit",
            path=iteration_dir / "filter_audit.jsonl",
            created_at=verify_time,
            metadata={
                "accepted_count": len(verification.accepted),
                "rejected_count": len(verification.quarantined),
                "acceptance_rate": verification.acceptance_rate,
                "filter_config_hash": self.verifier.config_hash,
                "rejection_counts": verification.rejection_counts,
            },
        )
        accepted_evidence = self.evidence.file(
            iteration=iteration,
            kind=EvidenceKind.ACCEPTED_DATASET,
            stage="accepted-dataset",
            path=dataset_path,
            created_at=verify_time,
            metadata={
                "dataset_id": f"dataset-{dataset_hash[:24]}",
                "dataset_hash": dataset_hash,
                "accepted_count": len(verification.accepted),
            },
        )
        quarantine_evidence = self.evidence.file(
            iteration=iteration,
            kind=EvidenceKind.QUARANTINE_DATASET,
            stage="quarantine-dataset",
            path=iteration_dir / "quarantine.jsonl",
            created_at=verify_time,
            metadata={
                "rejected_count": len(verification.quarantined),
                "rejection_counts": verification.rejection_counts,
            },
        )
        cost_evidence = self.evidence.file(
            iteration=iteration,
            kind=EvidenceKind.COST_LEDGER,
            stage="cost-ledger",
            path=cost_path,
            created_at=verify_time,
            metadata={
                "iteration_cost_usd": ledger.iteration_total(iteration),
                "run_total_if_committed_usd": ledger.total_charged_usd,
            },
        )
        evidence_records = (
            diagnostic_evidence,
            hypothesis_evidence,
            synthesis_evidence,
            raw_evidence,
            verification_evidence,
            accepted_evidence,
            quarantine_evidence,
            cost_evidence,
        )
        context = {
            "data_transaction_id": _id("tx", self.run_id, iteration, "data"),
            "dataset_id": f"dataset-{dataset_hash[:24]}",
            "dataset_path": str(dataset_path.resolve()),
            "dataset_hash": dataset_hash,
            "raw_count": len(batch.examples),
            "accepted_count": len(verification.accepted),
            "rejected_count": len(verification.quarantined),
            "acceptance_rate": verification.acceptance_rate,
            "hypothesis": str(hypothesis["text"]),
            "teacher_model": batch.teacher_model,
            "teacher_api_version": batch.api_version,
            "teacher_prompt_hash": batch.teacher_prompt_hash,
            "filter_config_hash": self.verifier.config_hash,
            "iteration_cost_usd": ledger.iteration_total(iteration),
            "data_evidence_ids": [item.evidence_id for item in evidence_records],
            "code_git_commit": self.metadata.code_git_commit,
        }
        stage_records = list(
            self._data_stage_records(
                current=current,
                evidence_records=evidence_records,
                context=context,
            )
        )
        if (
            not verification.accepted
            or verification.acceptance_rate
            < self.config.verification.min_acceptance_rate
        ):
            return self._commit_data_rejection(
                current=current,
                evidence_records=evidence_records,
                stage_records=tuple(stage_records),
                context=context,
            )

        if self.approval_policy.requires_review(DecisionSubject.DATASET):
            candidates = tuple(
                ApprovalCandidate(
                    item_id=example.example_id,
                    content_sha256=hashlib.sha256(
                        canonical_json(example.to_dict()).encode("utf-8")
                    ).hexdigest(),
                    metadata={
                        "record_type": "synthetic-example",
                        "dataset_hash": dataset_hash,
                    },
                )
                for example in verification.accepted
            )
            request_bundle = self.approval_service.create_request(
                run_id=self.run_id,
                iteration=iteration,
                subject_type=DecisionSubject.DATASET,
                subject_id=str(context["dataset_id"]),
                candidates=candidates,
                source_evidence_ids=(
                    accepted_evidence.evidence_id,
                    verification_evidence.evidence_id,
                    quarantine_evidence.evidence_id,
                ),
                selection_seed=f"data-{self.config.seed}-{iteration}",
                requested_at=self.clock.at(
                    iteration=iteration,
                    offset_seconds=50,
                ),
                metadata={
                    "dataset_hash": dataset_hash,
                    "filter_config_hash": self.verifier.config_hash,
                },
            )
            request_sha256 = record_sha256(request_bundle.request.to_dict())
            request_decision = DecisionRecord(
                decision_id=_id(
                    "decision",
                    self.run_id,
                    iteration,
                    "dataset-review-request",
                ),
                run_id=self.run_id,
                iteration=iteration,
                subject_type=DecisionSubject.DATASET,
                subject_id=str(context["dataset_id"]),
                action=DecisionAction.REQUEST_APPROVAL,
                reason_code="dataset-review-required",
                reason="The verified Dataset requires immutable human review before training.",
                evidence_ids=tuple(
                    dict.fromkeys(
                        (
                            accepted_evidence.evidence_id,
                            verification_evidence.evidence_id,
                            request_bundle.request_evidence.evidence_id,
                        )
                    )
                ),
                created_at=request_bundle.request.requested_at,
                metadata={
                    "approval_request_id": request_bundle.request.request_id,
                    "approval_request_sha256": request_sha256,
                },
            )
            pending_transition = TransitionRecord(
                transition_id=_id(
                    "transition",
                    self.run_id,
                    iteration,
                    "data-review-pending",
                ),
                run_id=self.run_id,
                iteration=iteration,
                from_state=ControlState.VERIFY,
                event=ControlEvent.DATA_REVIEW_REQUESTED,
                to_state=ControlState.DATA_REVIEW_PENDING,
                occurred_at=request_bundle.request.requested_at,
                idempotency_key=_id(
                    "idempotency",
                    self.run_id,
                    iteration,
                    "data-review-pending",
                ),
                decision_id=request_decision.decision_id,
                evidence_ids=request_decision.evidence_ids,
            )
            pending_context = dict(context)
            pending_context.update(
                {
                    "approval_request_id": request_bundle.request.request_id,
                    "approval_request_sha256": request_sha256,
                    "decision_id": request_decision.decision_id,
                }
            )
            pending_snapshot = self._snapshot(
                base=current,
                state=ControlState.DATA_REVIEW_PENDING,
                entered_at=request_bundle.request.requested_at,
                evidence_ids=request_decision.evidence_ids,
                metadata=pending_context,
                snapshot_tag="data-review-pending",
            )
            records = (
                *evidence_records,
                *stage_records,
                request_bundle.request_evidence,
                request_decision,
                pending_transition,
                pending_snapshot,
            )
            self.control_store.commit(
                transaction_id=str(context["data_transaction_id"]),
                run_id=self.run_id,
                iteration=iteration,
                created_at=request_bundle.request.requested_at,
                records=records,
            )
            return pending_snapshot

        train_transition, train_snapshot = self._data_train_records(
            current=current,
            context=context,
            evidence_ids=tuple(item.evidence_id for item in evidence_records),
        )
        records = (
            *evidence_records,
            *stage_records,
            train_transition,
            train_snapshot,
        )
        self.control_store.commit(
            transaction_id=str(context["data_transaction_id"]),
            run_id=self.run_id,
            iteration=iteration,
            created_at=train_snapshot.entered_at,
            records=records,
        )
        return self._train_evaluate_decide(train_snapshot)

    def _resume_data_review(self, pending: StateSnapshot) -> StateSnapshot:
        request_id = _metadata_string(pending.metadata, "approval_request_id")
        request_sha256 = _metadata_string(
            pending.metadata,
            "approval_request_sha256",
        )
        status = self.approval_service.status(
            request_id,
            as_of=_not_before_timestamp(self.now(), pending.entered_at),
        )
        if status.state is ApprovalState.PENDING:
            return pending
        if status.state is ApprovalState.APPROVED:
            bundle = self._load_approval_decision_bundle(request_id)
            approved_time = bundle.decision.decided_at
            transition, train_snapshot = self._data_train_records(
                current=pending,
                context=dict(pending.metadata),
                evidence_ids=tuple(
                    dict.fromkeys(
                        (
                            *pending.evidence_ids,
                            bundle.request_evidence.evidence_id,
                            bundle.decision_evidence.evidence_id,
                        )
                    )
                ),
                from_state=ControlState.DATA_REVIEW_PENDING,
                event=ControlEvent.DATA_APPROVED,
                decision_id=bundle.control_decision.decision_id,
                entered_at=approved_time,
            )
            self.control_store.commit(
                transaction_id=_id(
                    "tx",
                    self.run_id,
                    pending.iteration,
                    "data-review-approved",
                ),
                run_id=self.run_id,
                iteration=pending.iteration,
                created_at=approved_time,
                records=(
                    bundle.decision_evidence,
                    bundle.control_decision,
                    transition,
                    train_snapshot,
                ),
            )
            return self._train_evaluate_decide(train_snapshot)
        return self._stop_for_approval_failure(
            pending,
            request_id=request_id,
            request_sha256=request_sha256,
            subject_type=DecisionSubject.DATASET,
            event=ControlEvent.DATA_DENIED,
            status=status.state,
        )

    def _train_evaluate_decide(self, train_snapshot: StateSnapshot) -> StateSnapshot:
        context = dict(train_snapshot.metadata)
        iteration = train_snapshot.iteration
        examples = _load_examples(Path(_metadata_string(context, "dataset_path")))
        dataset_hash = _metadata_string(context, "dataset_hash")
        parent_checkpoint_id = train_snapshot.active_checkpoint_id
        try:
            training = self.runtime.trainer.train(
                examples=examples,
                dataset_path=Path(_metadata_string(context, "dataset_path")),
                dataset_hash=dataset_hash,
                model_id=self.config.model_id,
                parent_checkpoint_id=parent_checkpoint_id,
                iteration=iteration,
                output_root=self.workspace / "model_artifacts",
            )
            training_evidence = self.runtime.evidence.training(
                training,
                run_id=self.run_id,
                iteration=iteration,
            )
            served = evaluate_checkpoint_with_serving(
                self.runtime,
                checkpoint=training,
                run_id=self.run_id,
                iteration=iteration,
                benchmark_id=self.config.rsi.benchmark_id,
            )
        except (AdapterError, ValueError, OSError) as exc:
            return self._abort_after_data(
                train_snapshot,
                reason=f"Candidate execution failed: {type(exc).__name__}: {exc}",
            )
        evaluation = served.evaluation
        iteration_cost = _metadata_float(context, "iteration_cost_usd")
        iteration_cost += evaluation.estimated_cost_usd
        candidate_evidence = (
            *training_evidence,
            *served.evidence,
        )
        evaluation_path = write_canonical_json(
            self.artifacts.iteration_dir(iteration) / "evaluation.json",
            evaluation.to_dict(),
        )
        evaluation_file_evidence = self.evidence.file(
            iteration=iteration,
            kind=EvidenceKind.EVALUATION_RESULT,
            stage="evaluation-file",
            path=evaluation_path,
            created_at=self.clock.at(iteration=iteration, offset_seconds=80),
            metadata={
                "checkpoint_id": training.checkpoint_id,
                "benchmark_id": evaluation.benchmark_id,
                "score": evaluation.score,
            },
        )
        all_candidate_evidence = (
            *candidate_evidence,
            evaluation_file_evidence,
        )
        evaluate_time = self.clock.at(iteration=iteration, offset_seconds=80)
        serve_transition = TransitionRecord(
            transition_id=_id("transition", self.run_id, iteration, "serve"),
            run_id=self.run_id,
            iteration=iteration,
            from_state=ControlState.TRAIN,
            event=ControlEvent.SERVING_READY,
            to_state=ControlState.SERVE,
            occurred_at=self.clock.at(iteration=iteration, offset_seconds=70),
            idempotency_key=_id("idempotency", self.run_id, iteration, "serve"),
            evidence_ids=(served.evidence[0].evidence_id,),
        )
        serve_snapshot = self._snapshot(
            base=train_snapshot,
            state=ControlState.SERVE,
            entered_at=serve_transition.occurred_at,
            candidate_checkpoint_id=training.checkpoint_id,
            evidence_ids=serve_transition.evidence_ids,
            metadata=context,
            snapshot_tag="serve",
        )
        evaluate_transition = TransitionRecord(
            transition_id=_id("transition", self.run_id, iteration, "evaluate"),
            run_id=self.run_id,
            iteration=iteration,
            from_state=ControlState.SERVE,
            event=ControlEvent.EVALUATION_COMPLETED,
            to_state=ControlState.EVALUATE,
            occurred_at=evaluate_time,
            idempotency_key=_id(
                "idempotency",
                self.run_id,
                iteration,
                "evaluate",
            ),
            evidence_ids=tuple(item.evidence_id for item in all_candidate_evidence),
        )
        candidate_context = dict(context)
        candidate_context.update(
            {
                "checkpoint_id": training.checkpoint_id,
                "checkpoint_path": str(training.checkpoint_path.resolve()),
                "model_id": training.model_id,
                "parent_checkpoint_id": training.parent_checkpoint_id,
                "final_loss": training.final_loss,
                "artifact_sha256": str(
                    training.metadata.get("artifact_sha256")
                    or sha256_path(training.checkpoint_path)
                ),
                "candidate_score": evaluation.score,
                "benchmark_id": evaluation.benchmark_id,
                "evaluation_metrics": evaluation.metrics,
                "evaluation_failure_traces": evaluation.failure_traces,
                "evaluation_cost_usd": evaluation.estimated_cost_usd,
                "iteration_cost_usd": iteration_cost,
                "candidate_evidence_ids": [
                    item.evidence_id for item in all_candidate_evidence
                ],
                "rsi_policy_sha256": self._policy_sha256(),
            }
        )
        evaluate_snapshot = self._snapshot(
            base=train_snapshot,
            state=ControlState.EVALUATE,
            entered_at=evaluate_time,
            candidate_checkpoint_id=training.checkpoint_id,
            candidate_score=evaluation.score,
            evidence_ids=evaluate_transition.evidence_ids,
            metadata=candidate_context,
            snapshot_tag="evaluate",
        )
        observation = CandidateObservation(
            checkpoint_id=training.checkpoint_id,
            parent_checkpoint_id=training.parent_checkpoint_id,
            iteration=iteration,
            score=evaluation.score,
            iteration_cost_usd=iteration_cost,
            evaluated_at=evaluate_time,
            evidence_ids=evaluate_transition.evidence_ids,
        )
        policy_step = self.policy.evaluate(evaluate_snapshot, observation)
        primary = policy_step.decisions[0]

        if (
            primary.action is DecisionAction.PROMOTE
            and self.approval_policy.requires_review(DecisionSubject.CHECKPOINT)
        ):
            request_bundle = self.approval_service.create_request(
                run_id=self.run_id,
                iteration=iteration,
                subject_type=DecisionSubject.CHECKPOINT,
                subject_id=training.checkpoint_id,
                candidates=(
                    ApprovalCandidate(
                        item_id=training.checkpoint_id,
                        content_sha256=_metadata_string(
                            candidate_context,
                            "artifact_sha256",
                        ),
                        metadata={
                            "benchmark_id": evaluation.benchmark_id,
                            "candidate_score": evaluation.score,
                            "peak_score": evaluate_snapshot.peak_score,
                            "dataset_hash": dataset_hash,
                        },
                    ),
                ),
                source_evidence_ids=evaluate_transition.evidence_ids,
                selection_seed=f"model-{self.config.seed}-{iteration}",
                requested_at=self.clock.at(
                    iteration=iteration,
                    offset_seconds=90,
                ),
                metadata={
                    "policy_decision_id": primary.decision_id,
                    "rsi_policy_sha256": self._policy_sha256(),
                },
            )
            request_sha256 = record_sha256(request_bundle.request.to_dict())
            request_decision = DecisionRecord(
                decision_id=_id(
                    "decision",
                    self.run_id,
                    iteration,
                    "model-review-request",
                ),
                run_id=self.run_id,
                iteration=iteration,
                subject_type=DecisionSubject.CHECKPOINT,
                subject_id=training.checkpoint_id,
                action=DecisionAction.REQUEST_APPROVAL,
                reason_code="model-review-required",
                reason="The score-qualified Candidate requires immutable human review before Peak promotion.",
                evidence_ids=tuple(
                    dict.fromkeys(
                        (
                            *evaluate_transition.evidence_ids,
                            request_bundle.request_evidence.evidence_id,
                        )
                    )
                ),
                created_at=request_bundle.request.requested_at,
                metadata={
                    "approval_request_id": request_bundle.request.request_id,
                    "approval_request_sha256": request_sha256,
                    "policy_decision_id": primary.decision_id,
                },
            )
            pending_transition = TransitionRecord(
                transition_id=_id(
                    "transition",
                    self.run_id,
                    iteration,
                    "model-review-pending",
                ),
                run_id=self.run_id,
                iteration=iteration,
                from_state=ControlState.EVALUATE,
                event=ControlEvent.MODEL_REVIEW_REQUESTED,
                to_state=ControlState.MODEL_REVIEW_PENDING,
                occurred_at=request_bundle.request.requested_at,
                idempotency_key=_id(
                    "idempotency",
                    self.run_id,
                    iteration,
                    "model-review-pending",
                ),
                decision_id=request_decision.decision_id,
                evidence_ids=request_decision.evidence_ids,
            )
            pending_context = dict(candidate_context)
            pending_context.update(
                {
                    "approval_request_id": request_bundle.request.request_id,
                    "approval_request_sha256": request_sha256,
                    "decision_id": request_decision.decision_id,
                }
            )
            pending_snapshot = self._snapshot(
                base=evaluate_snapshot,
                state=ControlState.MODEL_REVIEW_PENDING,
                entered_at=request_bundle.request.requested_at,
                candidate_checkpoint_id=training.checkpoint_id,
                candidate_score=evaluation.score,
                evidence_ids=request_decision.evidence_ids,
                metadata=pending_context,
                snapshot_tag="model-review-pending",
            )
            transaction_id = _id(
                "tx",
                self.run_id,
                iteration,
                "candidate-pending-review",
            )
            self.control_store.commit(
                transaction_id=transaction_id,
                run_id=self.run_id,
                iteration=iteration,
                created_at=pending_snapshot.entered_at,
                records=(
                    *all_candidate_evidence,
                    serve_transition,
                    serve_snapshot,
                    evaluate_transition,
                    evaluate_snapshot,
                    request_bundle.request_evidence,
                    request_decision,
                    pending_transition,
                    pending_snapshot,
                ),
            )
            return pending_snapshot

        return self._commit_policy_step(
            evaluate_snapshot=evaluate_snapshot,
            training=training,
            evaluation=evaluation,
            policy_step=policy_step,
            new_evidence=all_candidate_evidence,
            stage_records=(
                serve_transition,
                serve_snapshot,
                evaluate_transition,
                evaluate_snapshot,
            ),
            approval_bundle=None,
            transaction_tag="candidate",
        )

    def _resume_model_review(self, pending: StateSnapshot) -> StateSnapshot:
        request_id = _metadata_string(pending.metadata, "approval_request_id")
        request_sha256 = _metadata_string(
            pending.metadata,
            "approval_request_sha256",
        )
        status = self.approval_service.status(
            request_id,
            as_of=_not_before_timestamp(self.now(), pending.entered_at),
        )
        if status.state is ApprovalState.PENDING:
            return pending
        if status.state is ApprovalState.APPROVED:
            approval = self._load_approval_decision_bundle(request_id)
            if _metadata_string(pending.metadata, "rsi_policy_sha256") != self._policy_sha256():
                raise LineageConflictError(
                    "RSI policy changed while a model approval was pending"
                )
            training = _training_from_metadata(pending.metadata)
            evaluation = _evaluation_from_metadata(pending.metadata)
            evaluate_snapshot = self._snapshot(
                base=pending,
                state=ControlState.EVALUATE,
                entered_at=approval.decision.decided_at,
                candidate_checkpoint_id=training.checkpoint_id,
                candidate_score=evaluation.score,
                evidence_ids=tuple(
                    _metadata_string_list(
                        pending.metadata,
                        "candidate_evidence_ids",
                    )
                ),
                metadata=dict(pending.metadata),
                snapshot_tag="evaluate-after-model-approval",
            )
            policy_step = self.policy.evaluate(
                evaluate_snapshot,
                CandidateObservation(
                    checkpoint_id=training.checkpoint_id,
                    parent_checkpoint_id=training.parent_checkpoint_id,
                    iteration=pending.iteration,
                    score=evaluation.score,
                    iteration_cost_usd=_metadata_float(
                        pending.metadata,
                        "iteration_cost_usd",
                    ),
                    evaluated_at=approval.decision.decided_at,
                    evidence_ids=evaluate_snapshot.evidence_ids,
                ),
            )
            if policy_step.decisions[0].action is not DecisionAction.PROMOTE:
                raise LineageConflictError(
                    "approved Candidate no longer satisfies the frozen promotion policy"
                )
            return self._commit_policy_step(
                evaluate_snapshot=evaluate_snapshot,
                training=training,
                evaluation=evaluation,
                policy_step=policy_step,
                new_evidence=(approval.decision_evidence,),
                stage_records=(
                    approval.control_decision,
                    evaluate_snapshot,
                ),
                approval_bundle=approval,
                transaction_tag="model-review-approved",
            )
        return self._stop_for_approval_failure(
            pending,
            request_id=request_id,
            request_sha256=request_sha256,
            subject_type=DecisionSubject.CHECKPOINT,
            event=ControlEvent.MODEL_DENIED,
            status=status.state,
        )

    def _commit_policy_step(
        self,
        *,
        evaluate_snapshot: StateSnapshot,
        training: TrainingResult,
        evaluation: EvaluationResult,
        policy_step: RSIPolicyStep,
        new_evidence: tuple[EvidenceRecord, ...],
        stage_records: tuple[Any, ...],
        approval_bundle: ApprovalDecisionBundle | None,
        transaction_tag: str,
    ) -> StateSnapshot:
        iteration = evaluate_snapshot.iteration
        primary = policy_step.decisions[0]
        final = policy_step.final_snapshot
        transaction_id = _id(
            "tx",
            self.run_id,
            iteration,
            transaction_tag,
        )
        materialization_context = dict(evaluate_snapshot.metadata)
        materialization_context.update(
            {
                "control_transaction_id": transaction_id,
                "primary_decision_id": primary.decision_id,
                "primary_decision_action": primary.action.value,
                "checkpoint_status": _checkpoint_status(primary.action),
                "decision_id": final.metadata.get("decision_id"),
                "source_iteration": iteration,
                "materialize_checkpoint": True,
            }
        )
        materialized_snapshot = StateSnapshot(
            snapshot_id=_id(
                "snapshot",
                self.run_id,
                iteration,
                transaction_tag,
                training.checkpoint_id,
                "materialized",
            ),
            run_id=final.run_id,
            iteration=final.iteration,
            cycle=final.cycle,
            state=final.state,
            entered_at=self.clock.at(
                iteration=iteration,
                offset_seconds=110,
            ),
            active_checkpoint_id=final.active_checkpoint_id,
            candidate_checkpoint_id=final.candidate_checkpoint_id,
            peak_checkpoint_id=final.peak_checkpoint_id,
            active_harness_id=final.active_harness_id,
            candidate_harness_id=final.candidate_harness_id,
            candidate_score=final.candidate_score,
            peak_score=final.peak_score,
            plateau_count=final.plateau_count,
            total_cost_usd=final.total_cost_usd,
            stop_reason=final.stop_reason,
            evidence_ids=final.evidence_ids,
            metadata=materialization_context,
        )
        records: list[Any] = [*new_evidence, *stage_records]
        if approval_bundle is not None:
            if approval_bundle.decision_evidence not in records:
                records.append(approval_bundle.decision_evidence)
            if approval_bundle.control_decision not in records:
                records.append(approval_bundle.control_decision)
        records.extend(policy_step.decisions)
        records.extend(policy_step.transitions)
        records.extend(policy_step.snapshots)
        records.append(materialized_snapshot)
        self.control_store.commit(
            transaction_id=transaction_id,
            run_id=self.run_id,
            iteration=iteration,
            created_at=materialized_snapshot.entered_at,
            records=records,
        )
        self._materialize_checkpoint(
            snapshot=materialized_snapshot,
            training=training,
            evaluation=evaluation,
        )
        return materialized_snapshot

    def _reconcile_snapshot(self, snapshot: StateSnapshot) -> StateSnapshot:
        if snapshot.metadata.get("materialize_checkpoint") is True:
            training = _training_from_metadata(snapshot.metadata)
            evaluation = _evaluation_from_metadata(snapshot.metadata)
            self._materialize_checkpoint(
                snapshot=snapshot,
                training=training,
                evaluation=evaluation,
            )
        return snapshot

    def _materialize_checkpoint(
        self,
        *,
        snapshot: StateSnapshot,
        training: TrainingResult,
        evaluation: EvaluationResult,
    ) -> CheckpointBundle:
        context = snapshot.metadata
        transaction_id = _metadata_string(context, "control_transaction_id")
        transaction = self.control_store.load_transaction(transaction_id)
        source_iteration = transaction.iteration
        training_iteration = training.metadata.get("iteration")
        if training_iteration is not None:
            if (
                isinstance(training_iteration, bool)
                or not isinstance(training_iteration, int)
                or training_iteration != source_iteration
            ):
                raise LineageIntegrityError(
                    "training iteration does not match the committed control transaction"
                )
        decision_id = _metadata_string(context, "primary_decision_id")
        action = DecisionAction(
            _metadata_string(context, "primary_decision_action")
        )
        status = _metadata_string(context, "checkpoint_status")
        artifact_sha256 = _metadata_string(context, "artifact_sha256")
        lineage = LineageManifest(
            checkpoint_id=training.checkpoint_id,
            run_id=self.run_id,
            iteration=source_iteration,
            model_id=training.model_id,
            parent_checkpoint_id=training.parent_checkpoint_id,
            dataset_commit_hash=training.dataset_hash,
            dataset_path=_metadata_string(context, "dataset_path"),
            teacher_model=_metadata_string(context, "teacher_model"),
            teacher_api_version=_metadata_string(
                context,
                "teacher_api_version",
            ),
            teacher_prompt_hash=_metadata_string(
                context,
                "teacher_prompt_hash",
            ),
            filter_config_version=_metadata_string(
                context,
                "filter_config_hash",
            ),
            rejected_data_count=_metadata_int(context, "rejected_count"),
            training_loss_final=training.final_loss,
            benchmark_id=evaluation.benchmark_id,
            benchmark_score=evaluation.score,
            code_git_commit=_metadata_string(context, "code_git_commit"),
            status=status,
            artifact_sha256=artifact_sha256,
            control_transaction_id=transaction_id,
            created_at=snapshot.entered_at,
        )
        bundle = self.checkpoint_store.commit(
            checkpoint_payload={
                "checkpoint_id": training.checkpoint_id,
                "run_id": self.run_id,
                "iteration": source_iteration,
                "model_id": training.model_id,
                "parent_checkpoint_id": training.parent_checkpoint_id,
                "dataset_commit_hash": training.dataset_hash,
                "final_loss": training.final_loss,
                "benchmark_score": evaluation.score,
                "status": status,
                "artifact_sha256": artifact_sha256,
                "control_transaction_id": transaction_id,
                "code_git_commit": _metadata_string(
                    context,
                    "code_git_commit",
                ),
                "metadata": {
                    "primary_decision_id": decision_id,
                    "primary_decision_action": action.value,
                    "acceptance_rate": context.get("acceptance_rate"),
                },
            },
            checkpoint_id=training.checkpoint_id,
            run_id=self.run_id,
            iteration=lineage.iteration,
            lineage_manifest=lineage,
            artifact_path=training.checkpoint_path,
            artifact_uri=training.checkpoint_path.resolve().as_uri(),
            control_transaction_id=transaction_id,
            created_at=snapshot.entered_at,
        )
        decision = self.control_store.load_decision(decision_id)
        if action is DecisionAction.PROMOTE:
            self.peak_store.compare_and_swap(
                PeakPointer(
                    checkpoint_id=training.checkpoint_id,
                    previous_checkpoint_id=training.parent_checkpoint_id,
                    run_id=self.run_id,
                    iteration=lineage.iteration,
                    model_id=training.model_id,
                    score=evaluation.score,
                    checkpoint_bundle_sha256=bundle.manifest_sha256,
                    control_transaction_id=transaction_id,
                    decision_id=decision_id,
                    updated_at=decision.created_at,
                ),
                expected_previous_checkpoint_id=training.parent_checkpoint_id,
            )
        elif action in {DecisionAction.REJECT, DecisionAction.ROLLBACK}:
            self.quarantine_store.commit(
                QuarantineMarker(
                    run_id=self.run_id,
                    iteration=lineage.iteration,
                    subject_type=DecisionSubject.CHECKPOINT,
                    subject_id=training.checkpoint_id,
                    decision_id=decision_id,
                    control_transaction_id=transaction_id,
                    reason_code=decision.reason_code,
                    reason=decision.reason,
                    evidence_ids=decision.evidence_ids,
                    created_at=decision.created_at,
                )
            )
        return bundle

    def _data_stage_records(
        self,
        *,
        current: StateSnapshot,
        evidence_records: tuple[EvidenceRecord, ...],
        context: dict[str, Any],
    ) -> tuple[Any, ...]:
        iteration = current.iteration
        diagnostic_id = evidence_records[0].evidence_id
        hypothesis_id = evidence_records[1].evidence_id
        synthesis_ids = tuple(
            item.evidence_id
            for item in evidence_records
            if item.kind
            in {
                EvidenceKind.SYNTHESIS_MANIFEST,
                EvidenceKind.RAW_DATASET,
                EvidenceKind.COST_LEDGER,
            }
        )
        verify_ids = tuple(
            item.evidence_id
            for item in evidence_records
            if item.kind
            in {
                EvidenceKind.VERIFICATION_AUDIT,
                EvidenceKind.ACCEPTED_DATASET,
                EvidenceKind.QUARANTINE_DATASET,
            }
        )
        hypothesis_transition = TransitionRecord(
            transition_id=_id("transition", self.run_id, iteration, "hypothesis"),
            run_id=self.run_id,
            iteration=iteration,
            from_state=ControlState.DIAGNOSE,
            event=ControlEvent.DIAGNOSIS_COMPLETED,
            to_state=ControlState.HYPOTHESIS,
            occurred_at=self.clock.at(iteration=iteration, offset_seconds=10),
            idempotency_key=_id(
                "idempotency",
                self.run_id,
                iteration,
                "hypothesis",
            ),
            evidence_ids=(diagnostic_id,),
        )
        hypothesis_snapshot = self._snapshot(
            base=current,
            state=ControlState.HYPOTHESIS,
            entered_at=hypothesis_transition.occurred_at,
            evidence_ids=(diagnostic_id,),
            metadata=context,
            snapshot_tag="hypothesis",
        )
        synthesize_transition = TransitionRecord(
            transition_id=_id("transition", self.run_id, iteration, "synthesize"),
            run_id=self.run_id,
            iteration=iteration,
            from_state=ControlState.HYPOTHESIS,
            event=ControlEvent.HYPOTHESIS_SELECTED,
            to_state=ControlState.SYNTHESIZE,
            occurred_at=self.clock.at(iteration=iteration, offset_seconds=20),
            idempotency_key=_id(
                "idempotency",
                self.run_id,
                iteration,
                "synthesize",
            ),
            evidence_ids=(hypothesis_id,),
        )
        synthesize_snapshot = self._snapshot(
            base=current,
            state=ControlState.SYNTHESIZE,
            entered_at=synthesize_transition.occurred_at,
            evidence_ids=(hypothesis_id,),
            metadata=context,
            snapshot_tag="synthesize",
        )
        verify_transition = TransitionRecord(
            transition_id=_id("transition", self.run_id, iteration, "verify"),
            run_id=self.run_id,
            iteration=iteration,
            from_state=ControlState.SYNTHESIZE,
            event=ControlEvent.SYNTHESIS_COMPLETED,
            to_state=ControlState.VERIFY,
            occurred_at=self.clock.at(iteration=iteration, offset_seconds=40),
            idempotency_key=_id(
                "idempotency",
                self.run_id,
                iteration,
                "verify",
            ),
            evidence_ids=tuple(dict.fromkeys((*synthesis_ids, *verify_ids))),
        )
        verify_snapshot = self._snapshot(
            base=current,
            state=ControlState.VERIFY,
            entered_at=verify_transition.occurred_at,
            evidence_ids=verify_transition.evidence_ids,
            metadata=context,
            snapshot_tag="verify",
        )
        return (
            hypothesis_transition,
            hypothesis_snapshot,
            synthesize_transition,
            synthesize_snapshot,
            verify_transition,
            verify_snapshot,
        )

    def _data_train_records(
        self,
        *,
        current: StateSnapshot,
        context: dict[str, Any],
        evidence_ids: tuple[str, ...],
        from_state: ControlState = ControlState.VERIFY,
        event: ControlEvent = ControlEvent.VERIFICATION_COMPLETED,
        decision_id: str | None = None,
        entered_at: str | None = None,
    ) -> tuple[TransitionRecord, StateSnapshot]:
        timestamp = entered_at or self.clock.at(
            iteration=current.iteration,
            offset_seconds=50,
        )
        transition = TransitionRecord(
            transition_id=_id(
                "transition",
                self.run_id,
                current.iteration,
                f"{from_state.value.lower()}-train",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            from_state=from_state,
            event=event,
            to_state=ControlState.TRAIN,
            occurred_at=timestamp,
            idempotency_key=_id(
                "idempotency",
                self.run_id,
                current.iteration,
                f"{from_state.value.lower()}-train",
            ),
            decision_id=decision_id,
            evidence_ids=evidence_ids,
        )
        metadata = dict(context)
        if decision_id is not None:
            metadata["decision_id"] = decision_id
        snapshot = self._snapshot(
            base=current,
            state=ControlState.TRAIN,
            entered_at=timestamp,
            evidence_ids=evidence_ids,
            metadata=metadata,
            snapshot_tag="train",
        )
        return transition, snapshot

    def _commit_data_rejection(
        self,
        *,
        current: StateSnapshot,
        evidence_records: tuple[EvidenceRecord, ...],
        stage_records: tuple[Any, ...],
        context: dict[str, Any],
    ) -> StateSnapshot:
        iteration = current.iteration
        stop_reason = (
            StopReason.NO_ACCEPTED_DATA
            if _metadata_int(context, "accepted_count") == 0
            else StopReason.LOW_ACCEPTANCE
        )
        evidence_ids = tuple(item.evidence_id for item in evidence_records)
        quarantine_time = self.clock.at(iteration=iteration, offset_seconds=50)
        quarantine_decision = DecisionRecord(
            decision_id=_id(
                "decision",
                self.run_id,
                iteration,
                "dataset-quarantine",
            ),
            run_id=self.run_id,
            iteration=iteration,
            subject_type=DecisionSubject.DATASET,
            subject_id=_metadata_string(context, "dataset_id"),
            action=DecisionAction.QUARANTINE,
            reason_code=stop_reason.value.lower(),
            reason="The verified Dataset did not satisfy the configured admission floor.",
            evidence_ids=evidence_ids,
            created_at=quarantine_time,
            stop_reason=stop_reason,
        )
        quarantine_transition = TransitionRecord(
            transition_id=_id(
                "transition",
                self.run_id,
                iteration,
                "dataset-quarantined",
            ),
            run_id=self.run_id,
            iteration=iteration,
            from_state=ControlState.VERIFY,
            event=ControlEvent.DATASET_QUARANTINED,
            to_state=ControlState.QUARANTINED,
            occurred_at=quarantine_time,
            idempotency_key=_id(
                "idempotency",
                self.run_id,
                iteration,
                "dataset-quarantined",
            ),
            decision_id=quarantine_decision.decision_id,
            evidence_ids=evidence_ids,
        )
        quarantine_snapshot = self._snapshot(
            base=current,
            state=ControlState.QUARANTINED,
            entered_at=quarantine_time,
            evidence_ids=evidence_ids,
            metadata={
                **context,
                "decision_id": quarantine_decision.decision_id,
            },
            snapshot_tag="dataset-quarantined",
        )
        stopped_time = self.clock.at(iteration=iteration, offset_seconds=51)
        stop_decision = DecisionRecord(
            decision_id=_id(
                "decision",
                self.run_id,
                iteration,
                "dataset-stop",
            ),
            run_id=self.run_id,
            iteration=iteration,
            subject_type=DecisionSubject.RUN,
            subject_id=self.run_id,
            action=DecisionAction.STOP,
            reason_code=stop_reason.value.lower(),
            reason="Stop the run because no admissible Dataset is available for training.",
            evidence_ids=evidence_ids,
            created_at=stopped_time,
            stop_reason=stop_reason,
        )
        stop_transition = TransitionRecord(
            transition_id=_id(
                "transition",
                self.run_id,
                iteration,
                "dataset-stop",
            ),
            run_id=self.run_id,
            iteration=iteration,
            from_state=ControlState.QUARANTINED,
            event=ControlEvent.STOP_REQUESTED,
            to_state=ControlState.STOPPED,
            occurred_at=stopped_time,
            idempotency_key=_id(
                "idempotency",
                self.run_id,
                iteration,
                "dataset-stop",
            ),
            decision_id=stop_decision.decision_id,
            evidence_ids=evidence_ids,
        )
        stopped_snapshot = StateSnapshot(
            snapshot_id=_id(
                "snapshot",
                self.run_id,
                iteration,
                "dataset-stopped",
            ),
            run_id=self.run_id,
            iteration=iteration,
            cycle=current.cycle,
            state=ControlState.STOPPED,
            entered_at=stopped_time,
            active_checkpoint_id=current.active_checkpoint_id,
            peak_checkpoint_id=current.peak_checkpoint_id,
            peak_score=current.peak_score,
            plateau_count=current.plateau_count,
            total_cost_usd=(
                current.total_cost_usd
                + _metadata_float(context, "iteration_cost_usd")
            ),
            stop_reason=stop_reason,
            evidence_ids=evidence_ids,
            metadata={
                **context,
                "decision_id": stop_decision.decision_id,
            },
        )
        transaction_id = _metadata_string(context, "data_transaction_id")
        self.control_store.commit(
            transaction_id=transaction_id,
            run_id=self.run_id,
            iteration=iteration,
            created_at=stopped_time,
            records=(
                *evidence_records,
                *stage_records,
                quarantine_decision,
                quarantine_transition,
                quarantine_snapshot,
                stop_decision,
                stop_transition,
                stopped_snapshot,
            ),
        )
        self.quarantine_store.commit(
            QuarantineMarker(
                run_id=self.run_id,
                iteration=iteration,
                subject_type=DecisionSubject.DATASET,
                subject_id=_metadata_string(context, "dataset_id"),
                decision_id=quarantine_decision.decision_id,
                control_transaction_id=transaction_id,
                reason_code=quarantine_decision.reason_code,
                reason=quarantine_decision.reason,
                evidence_ids=quarantine_decision.evidence_ids,
                created_at=quarantine_decision.created_at,
            )
        )
        return stopped_snapshot

    def _stop_for_approval_failure(
        self,
        pending: StateSnapshot,
        *,
        request_id: str,
        request_sha256: str,
        subject_type: DecisionSubject,
        event: ControlEvent,
        status: ApprovalState,
    ) -> StateSnapshot:
        records: list[Any] = []
        evidence_ids = list(pending.evidence_ids)
        decision: DecisionRecord
        timestamp = self.clock.at(
            iteration=pending.iteration,
            offset_seconds=120,
        )
        if status is ApprovalState.DENIED:
            approval = self._load_approval_decision_bundle(request_id)
            records.extend(
                (approval.decision_evidence, approval.control_decision)
            )
            decision = approval.control_decision
            timestamp = approval.decision.decided_at
            evidence_ids.extend(
                (
                    approval.request_evidence.evidence_id,
                    approval.decision_evidence.evidence_id,
                )
            )
        else:
            request_bundle = self.approval_service.store.load_request(request_id)
            if record_sha256(request_bundle.to_dict()) != request_sha256:
                raise LineageIntegrityError(
                    "pending approval request SHA-256 changed"
                )
            request_evidence = self.approval_service._request_evidence(request_bundle)
            records.append(request_evidence)
            evidence_ids.append(request_evidence.evidence_id)
            decision = DecisionRecord(
                decision_id=_id(
                    "decision",
                    self.run_id,
                    pending.iteration,
                    f"approval-{status.value.lower()}",
                ),
                run_id=self.run_id,
                iteration=pending.iteration,
                subject_type=subject_type,
                subject_id=request_bundle.subject_id,
                action=DecisionAction.REJECT,
                reason_code=f"approval-{status.value.lower()}",
                reason=(
                    "The required human approval was not granted before the "
                    "controller resumed."
                ),
                evidence_ids=tuple(dict.fromkeys(evidence_ids)),
                created_at=timestamp,
                stop_reason=StopReason.APPROVAL_NOT_GRANTED,
                metadata={"approval_request_id": request_id},
            )
            records.append(decision)
        evidence_tuple = tuple(dict.fromkeys(evidence_ids))
        transition = TransitionRecord(
            transition_id=_id(
                "transition",
                self.run_id,
                pending.iteration,
                f"approval-{status.value.lower()}-stop",
            ),
            run_id=self.run_id,
            iteration=pending.iteration,
            from_state=pending.state,
            event=event,
            to_state=ControlState.STOPPED,
            occurred_at=timestamp,
            idempotency_key=_id(
                "idempotency",
                self.run_id,
                pending.iteration,
                f"approval-{status.value.lower()}-stop",
            ),
            decision_id=decision.decision_id,
            evidence_ids=evidence_tuple,
        )
        stopped = StateSnapshot(
            snapshot_id=_id(
                "snapshot",
                self.run_id,
                pending.iteration,
                f"approval-{status.value.lower()}-stopped",
            ),
            run_id=self.run_id,
            iteration=pending.iteration,
            cycle=pending.cycle,
            state=ControlState.STOPPED,
            entered_at=timestamp,
            active_checkpoint_id=pending.active_checkpoint_id,
            candidate_checkpoint_id=pending.candidate_checkpoint_id,
            peak_checkpoint_id=pending.peak_checkpoint_id,
            candidate_score=pending.candidate_score,
            peak_score=pending.peak_score,
            plateau_count=pending.plateau_count,
            total_cost_usd=(
                pending.total_cost_usd
                + _metadata_float(pending.metadata, "iteration_cost_usd")
            ),
            stop_reason=StopReason.APPROVAL_NOT_GRANTED,
            evidence_ids=evidence_tuple,
            metadata={
                **pending.metadata,
                "decision_id": decision.decision_id,
                "approval_state": status.value,
            },
        )
        records.extend((transition, stopped))
        transaction_id = _id(
            "tx",
            self.run_id,
            pending.iteration,
            f"approval-{status.value.lower()}-stop",
        )
        self.control_store.commit(
            transaction_id=transaction_id,
            run_id=self.run_id,
            iteration=pending.iteration,
            created_at=timestamp,
            records=records,
        )
        if subject_type is DecisionSubject.DATASET:
            self.quarantine_store.commit(
                QuarantineMarker(
                    run_id=self.run_id,
                    iteration=pending.iteration,
                    subject_type=DecisionSubject.DATASET,
                    subject_id=decision.subject_id,
                    decision_id=decision.decision_id,
                    control_transaction_id=transaction_id,
                    reason_code=decision.reason_code,
                    reason=decision.reason,
                    evidence_ids=decision.evidence_ids,
                    created_at=decision.created_at,
                )
            )
        elif pending.candidate_checkpoint_id is not None:
            training = _training_from_metadata(pending.metadata)
            evaluation = _evaluation_from_metadata(pending.metadata)
            materialized = replace(
                stopped,
                snapshot_id=_id(
                    "snapshot",
                    self.run_id,
                    pending.iteration,
                    f"approval-{status.value.lower()}-materialized",
                ),
                metadata={
                    **stopped.metadata,
                    "control_transaction_id": transaction_id,
                    "primary_decision_id": decision.decision_id,
                    "primary_decision_action": DecisionAction.REJECT.value,
                    "checkpoint_status": "REJECTED_BY_HUMAN",
                    "materialize_checkpoint": True,
                },
            )
            self._materialize_checkpoint(
                snapshot=materialized,
                training=training,
                evaluation=evaluation,
            )
        return stopped

    def _abort_before_candidate(
        self,
        current: StateSnapshot,
        *,
        evidence: tuple[EvidenceRecord, ...],
        stop_reason: StopReason,
        reason: str,
    ) -> StateSnapshot:
        timestamp = self.clock.at(
            iteration=current.iteration,
            offset_seconds=35,
        )
        decision = DecisionRecord(
            decision_id=_id(
                "decision",
                self.run_id,
                current.iteration,
                "pre-candidate-abort",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            subject_type=DecisionSubject.RUN,
            subject_id=self.run_id,
            action=DecisionAction.ABORT,
            reason_code=stop_reason.value.lower(),
            reason=reason,
            evidence_ids=tuple(item.evidence_id for item in evidence),
            created_at=timestamp,
            stop_reason=stop_reason,
        )
        transition = TransitionRecord(
            transition_id=_id(
                "transition",
                self.run_id,
                current.iteration,
                "pre-candidate-abort",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            from_state=current.state,
            event=(
                ControlEvent.BUDGET_EXCEEDED
                if stop_reason
                in {
                    StopReason.PER_ITERATION_BUDGET_EXCEEDED,
                    StopReason.TOTAL_BUDGET_EXCEEDED,
                }
                else ControlEvent.PROVIDER_CIRCUIT_OPEN
            ),
            to_state=ControlState.ABORTED,
            occurred_at=timestamp,
            idempotency_key=_id(
                "idempotency",
                self.run_id,
                current.iteration,
                "pre-candidate-abort",
            ),
            decision_id=decision.decision_id,
            evidence_ids=decision.evidence_ids,
        )
        snapshot = StateSnapshot(
            snapshot_id=_id(
                "snapshot",
                self.run_id,
                current.iteration,
                "pre-candidate-aborted",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            cycle=current.cycle,
            state=ControlState.ABORTED,
            entered_at=timestamp,
            active_checkpoint_id=current.active_checkpoint_id,
            peak_checkpoint_id=current.peak_checkpoint_id,
            peak_score=current.peak_score,
            plateau_count=current.plateau_count,
            total_cost_usd=current.total_cost_usd,
            stop_reason=stop_reason,
            evidence_ids=decision.evidence_ids,
            metadata={"decision_id": decision.decision_id},
        )
        self.control_store.commit(
            transaction_id=_id(
                "tx",
                self.run_id,
                current.iteration,
                "pre-candidate-abort",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            created_at=timestamp,
            records=(*evidence, decision, transition, snapshot),
        )
        return snapshot

    def _abort_after_data(
        self,
        current: StateSnapshot,
        *,
        reason: str,
    ) -> StateSnapshot:
        evidence_ids = current.evidence_ids
        timestamp = self.clock.at(
            iteration=current.iteration,
            offset_seconds=75,
        )
        decision = DecisionRecord(
            decision_id=_id(
                "decision",
                self.run_id,
                current.iteration,
                "candidate-execution-abort",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            subject_type=DecisionSubject.RUN,
            subject_id=self.run_id,
            action=DecisionAction.ABORT,
            reason_code="candidate-execution-failed",
            reason=reason,
            evidence_ids=evidence_ids,
            created_at=timestamp,
            stop_reason=StopReason.INTERNAL_ERROR,
        )
        transition = TransitionRecord(
            transition_id=_id(
                "transition",
                self.run_id,
                current.iteration,
                "candidate-execution-abort",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            from_state=current.state,
            event=ControlEvent.TRAINING_FAILED,
            to_state=ControlState.ABORTED,
            occurred_at=timestamp,
            idempotency_key=_id(
                "idempotency",
                self.run_id,
                current.iteration,
                "candidate-execution-abort",
            ),
            decision_id=decision.decision_id,
            evidence_ids=evidence_ids,
        )
        snapshot = StateSnapshot(
            snapshot_id=_id(
                "snapshot",
                self.run_id,
                current.iteration,
                "candidate-execution-aborted",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            cycle=current.cycle,
            state=ControlState.ABORTED,
            entered_at=timestamp,
            active_checkpoint_id=current.active_checkpoint_id,
            peak_checkpoint_id=current.peak_checkpoint_id,
            peak_score=current.peak_score,
            plateau_count=current.plateau_count,
            total_cost_usd=current.total_cost_usd,
            stop_reason=StopReason.INTERNAL_ERROR,
            evidence_ids=evidence_ids,
            metadata={**current.metadata, "decision_id": decision.decision_id},
        )
        self.control_store.commit(
            transaction_id=_id(
                "tx",
                self.run_id,
                current.iteration,
                "candidate-execution-abort",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            created_at=timestamp,
            records=(decision, transition, snapshot),
        )
        return snapshot

    def _stop_without_candidate(
        self,
        current: StateSnapshot,
        *,
        stop_reason: StopReason,
        reason_code: str,
        reason: str,
    ) -> StateSnapshot:
        timestamp = self.clock.at(
            iteration=current.iteration,
            offset_seconds=5,
        )
        evidence_ids = current.evidence_ids
        decision = DecisionRecord(
            decision_id=_id(
                "decision",
                self.run_id,
                current.iteration,
                reason_code,
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            subject_type=DecisionSubject.RUN,
            subject_id=self.run_id,
            action=DecisionAction.STOP,
            reason_code=reason_code,
            reason=reason,
            evidence_ids=evidence_ids,
            created_at=timestamp,
            stop_reason=stop_reason,
        )
        transition = TransitionRecord(
            transition_id=_id(
                "transition",
                self.run_id,
                current.iteration,
                reason_code,
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            from_state=current.state,
            event=ControlEvent.MAX_ITERATIONS_REACHED,
            to_state=ControlState.STOPPED,
            occurred_at=timestamp,
            idempotency_key=_id(
                "idempotency",
                self.run_id,
                current.iteration,
                reason_code,
            ),
            decision_id=decision.decision_id,
            evidence_ids=evidence_ids,
        )
        snapshot = replace(
            current,
            snapshot_id=_id(
                "snapshot",
                self.run_id,
                current.iteration,
                reason_code,
            ),
            state=ControlState.STOPPED,
            entered_at=timestamp,
            stop_reason=stop_reason,
            metadata={**current.metadata, "decision_id": decision.decision_id},
        )
        self.control_store.commit(
            transaction_id=_id(
                "tx",
                self.run_id,
                current.iteration,
                reason_code,
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            created_at=timestamp,
            records=(decision, transition, snapshot),
        )
        return snapshot

    def _snapshot(
        self,
        *,
        base: StateSnapshot,
        state: ControlState,
        entered_at: str,
        evidence_ids: tuple[str, ...],
        metadata: dict[str, Any],
        snapshot_tag: str,
        candidate_checkpoint_id: str | None = None,
        candidate_score: float | None = None,
    ) -> StateSnapshot:
        return StateSnapshot(
            snapshot_id=_id(
                "snapshot",
                self.run_id,
                base.iteration,
                snapshot_tag,
            ),
            run_id=self.run_id,
            iteration=base.iteration,
            cycle=base.cycle,
            state=state,
            entered_at=entered_at,
            active_checkpoint_id=base.active_checkpoint_id,
            candidate_checkpoint_id=candidate_checkpoint_id,
            peak_checkpoint_id=base.peak_checkpoint_id,
            active_harness_id=base.active_harness_id,
            candidate_harness_id=base.candidate_harness_id,
            candidate_score=candidate_score,
            peak_score=base.peak_score,
            plateau_count=base.plateau_count,
            total_cost_usd=base.total_cost_usd,
            evidence_ids=evidence_ids,
            metadata=metadata,
        )

    def _diagnostic(self, current: StateSnapshot) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "iteration": current.iteration,
            "active_checkpoint_id": current.active_checkpoint_id,
            "peak_checkpoint_id": current.peak_checkpoint_id,
            "peak_score": current.peak_score,
            "plateau_count": current.plateau_count,
            "failure_focus": (
                "tool argument validity, intermediate state verification, "
                "and bounded error recovery"
            ),
            "previous_primary_decision": current.metadata.get(
                "primary_decision_action"
            ),
        }

    def _hypothesis(
        self,
        current: StateSnapshot,
        diagnostic: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "iteration": current.iteration,
            "text": (
                f"Improve {diagnostic['failure_focus']} with novel, "
                f"auditable examples for iteration {current.iteration}."
            ),
            "parent_checkpoint_id": current.active_checkpoint_id,
            "peak_score": current.peak_score,
        }

    def _policy_sha256(self) -> str:
        return canonical_sha256(
            {
                "max_iterations": self.policy.limits.max_iterations,
                "plateau_patience": self.policy.limits.plateau_patience,
                "min_improvement": self.policy.limits.min_improvement,
                "regression_tolerance": self.policy.limits.regression_tolerance,
                "per_iteration_budget_usd": (
                    self.policy.limits.per_iteration_budget_usd
                ),
                "total_budget_usd": self.policy.limits.total_budget_usd,
            }
        )

    def _load_approval_decision_bundle(
        self,
        request_id: str,
    ) -> ApprovalDecisionBundle:
        decision = self.approval_service.store.load_decision(request_id)
        return self.approval_service.review(
            request_id=request_id,
            expected_request_sha256=decision.request_sha256,
            approved=decision.approved,
            reviewer_id=decision.reviewer_id,
            reviewer_role=decision.reviewer_role,
            reason=decision.reason,
            decided_at=decision.decided_at,
            reviewer_evidence_ids=tuple(
                evidence_id
                for evidence_id in decision.evidence_ids
                if not evidence_id.startswith("ev.approval.request.")
            ),
            metadata=dict(decision.metadata),
        )

    def _warm_verifier_history(self) -> None:
        for path in sorted((self.workspace / "iterations").glob("iter-*/accepted.jsonl")):
            try:
                examples = _load_examples(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            self.verifier.verify(examples)

    def _finish(self, snapshot: StateSnapshot) -> ConvergedRSIResult:
        pointer = self.peak_store.load()
        pending_request_id = (
            str(snapshot.metadata.get("approval_request_id"))
            if snapshot.state
            in {
                ControlState.DATA_REVIEW_PENDING,
                ControlState.MODEL_REVIEW_PENDING,
            }
            else None
        )
        pending_cost = 0.0
        if pending_request_id is not None:
            pending_cost = float(snapshot.metadata.get("iteration_cost_usd", 0.0))
        if snapshot.state in {
            ControlState.DATA_REVIEW_PENDING,
            ControlState.MODEL_REVIEW_PENDING,
        }:
            status = "awaiting_approval"
        elif snapshot.state in {ControlState.STOPPED, ControlState.COMPLETED}:
            status = "completed"
        elif snapshot.state is ControlState.ROLLED_BACK:
            status = "rolled_back"
        elif snapshot.state is ControlState.ABORTED:
            status = "aborted"
        else:
            status = "running"
        result = ConvergedRSIResult(
            run_id=self.run_id,
            status=status,
            state=snapshot.state.value,
            iteration=snapshot.iteration,
            peak_checkpoint_id=(
                pointer.checkpoint_id if pointer is not None else None
            ),
            peak_score=pointer.score if pointer is not None else None,
            total_cost_usd=snapshot.total_cost_usd + pending_cost,
            stop_reason=(
                snapshot.stop_reason.value
                if snapshot.stop_reason is not None
                else None
            ),
            pending_approval_request_id=pending_request_id,
            latest_snapshot_id=snapshot.snapshot_id,
        )
        self.artifacts.write_report(
            f"rsi-run-summary-{self.run_id}.json",
            result.to_dict(),
        )
        return result


def build_converged_rsi_controller(
    config: PipelineConfig,
    *,
    workspace: str | Path,
    run_id: str = "rsi-run-default",
    adapter_runtime: AdapterRuntime | None = None,
    verifier: VerificationPipeline | None = None,
    started_at: str | None = None,
    now: Callable[[], str] | None = None,
) -> ConvergedRSIController:
    return ConvergedRSIController(
        config,
        workspace=workspace,
        run_id=run_id,
        adapter_runtime=adapter_runtime,
        verifier=verifier,
        started_at=started_at,
        now=now,
    )


def _load_examples(path: Path) -> list[SyntheticExample]:
    examples: list[SyntheticExample] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(
                    f"Dataset line {line_number} must be a JSON object"
                )
            examples.append(SyntheticExample.from_dict(value))
    if not examples:
        raise ValueError("accepted Dataset is empty")
    return examples


def _training_from_metadata(metadata: dict[str, Any]) -> TrainingResult:
    parent = metadata.get("parent_checkpoint_id")
    if parent is not None and not isinstance(parent, str):
        raise LineageIntegrityError(
            "parent_checkpoint_id metadata must be a string or null"
        )
    training_metadata: dict[str, Any] = {
        "artifact_sha256": _metadata_string(
            metadata,
            "artifact_sha256",
        ),
    }
    if "source_iteration" in metadata:
        training_metadata["iteration"] = _metadata_int(
            metadata,
            "source_iteration",
        )
    elif "iteration" in metadata:
        training_metadata["iteration"] = _metadata_int(
            metadata,
            "iteration",
        )
    return TrainingResult(
        checkpoint_id=_metadata_string(metadata, "checkpoint_id"),
        checkpoint_path=Path(_metadata_string(metadata, "checkpoint_path")),
        model_id=_metadata_string(metadata, "model_id"),
        parent_checkpoint_id=parent,
        dataset_hash=_metadata_string(metadata, "dataset_hash"),
        final_loss=_metadata_float(metadata, "final_loss"),
        metadata=training_metadata,
    )


def _evaluation_from_metadata(metadata: dict[str, Any]) -> EvaluationResult:
    metrics = metadata.get("evaluation_metrics", {})
    traces = metadata.get("evaluation_failure_traces", [])
    if not isinstance(metrics, dict):
        raise LineageIntegrityError("evaluation_metrics must be a JSON object")
    if not isinstance(traces, list) or any(
        not isinstance(item, dict) for item in traces
    ):
        raise LineageIntegrityError(
            "evaluation_failure_traces must be an array of objects"
        )
    normalized_metrics: dict[str, float] = {}
    for key, value in metrics.items():
        if not isinstance(key, str) or isinstance(value, bool) or not isinstance(
            value,
            (int, float),
        ):
            raise LineageIntegrityError("evaluation_metrics are invalid")
        number = float(value)
        if not math.isfinite(number):
            raise LineageIntegrityError("evaluation_metrics must be finite")
        normalized_metrics[key] = number
    return EvaluationResult(
        score=_metadata_float(metadata, "candidate_score"),
        benchmark_id=_metadata_string(metadata, "benchmark_id"),
        metrics=normalized_metrics,
        failure_traces=[dict(item) for item in traces],
        estimated_cost_usd=_metadata_float(
            metadata,
            "evaluation_cost_usd",
        ),
    )


def _id(prefix: str, *parts: object) -> str:
    prefix = validate_id(prefix, "prefix")
    digest = hashlib.sha256(
        "\x00".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest[:28]}"


def _metadata_string(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise LineageIntegrityError(f"snapshot metadata.{key} must be a string")
    return value


def _metadata_string_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise LineageIntegrityError(
            f"snapshot metadata.{key} must be an array of strings"
        )
    return list(value)


def _metadata_float(metadata: dict[str, Any], key: str) -> float:
    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LineageIntegrityError(f"snapshot metadata.{key} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise LineageIntegrityError(f"snapshot metadata.{key} must be finite")
    return number


def _metadata_int(
    metadata: dict[str, Any],
    key: str,
    default: int | None = None,
) -> int:
    value = metadata.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise LineageIntegrityError(f"snapshot metadata.{key} must be an integer")
    return value


def _checkpoint_status(action: DecisionAction) -> str:
    if action is DecisionAction.PROMOTE:
        return "PEAK"
    if action is DecisionAction.ROLLBACK:
        return "ROLLED_BACK"
    if action is DecisionAction.REJECT:
        return "REJECTED"
    if action is DecisionAction.ABORT:
        return "ABORTED"
    return action.value


def _budget_stop_reason(message: str) -> StopReason:
    return (
        StopReason.PER_ITERATION_BUDGET_EXCEEDED
        if message.startswith("iteration")
        else StopReason.TOTAL_BUDGET_EXCEEDED
    )


def _not_before_timestamp(value: str, minimum: str) -> str:
    normalized_value = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
    normalized_minimum = datetime.fromisoformat(
        minimum.replace("Z", "+00:00")
    )
    return value if normalized_value >= normalized_minimum else minimum


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
