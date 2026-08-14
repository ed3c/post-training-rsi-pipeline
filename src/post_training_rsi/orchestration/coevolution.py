from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeAlias

from ..approval import (
    ApprovalCandidate,
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
    JSONValue,
    StateSnapshot,
    StopReason,
    TransitionRecord,
)
from ..control_plane.validation import canonical_json
from ..harness.coevolution_store import (
    CoEvolutionRunMetadata,
    CoEvolutionRunStore,
)
from ..harness.model_inner_loop import (
    ModelInnerExecutor,
    ModelInnerLimits,
    ModelInnerPolicy,
    ModelPromotionCommitObservation,
    ModelReviewObservation,
    ModelRollbackCommitObservation,
    ModelTeardownObservation,
    ModelTrainingAlgorithm,
    ModelTrainingRequest,
)
from ..harness.outer_loop import (
    DeterministicHarnessEvaluator,
    HarnessMutationProposal,
    HarnessMutator,
    HarnessOuterLimits,
    HarnessOuterPolicy,
    HarnessReviewObservation,
    HarnessSpec,
    HarnessTaskResult,
    HarnessValidator,
    RetryPolicy,
)
from ..harness.persistence import (
    HarnessPointer,
    HarnessPointerStore,
    HarnessSnapshotStore,
)
from ..harness.reference_runtime import (
    ReferenceClock,
    ReferenceModelDeployer,
    ReferenceModelEvaluator,
    ReferenceModelTrainer,
    ReferenceTaskSuite,
    build_reference_trajectories,
    content_addressed_harness_id,
    reference_harness_candidate_score,
    reference_model_score,
)
from ..harness.trace_harvesting import (
    TraceHarvestConfig,
    TraceHarvester,
    TraceHarvestLimits,
    TraceHarvestPolicy,
    TraceVerificationService,
)
from ..lineage import (
    ArtifactStore,
    CheckpointBundleStore,
    ControlRecordStore,
    LineageManifest,
    PeakPointer,
    PeakPointerStore,
    QuarantineMarker,
    QuarantineStore,
)
from ..verification.pipeline import VerificationPipeline

ControlRecord: TypeAlias = (
    EvidenceRecord | DecisionRecord | TransitionRecord | StateSnapshot
)


@dataclass(frozen=True, slots=True)
class CoEvolutionRunResult:
    run_id: str
    status: str
    state: str
    current_cycle: int
    completed_cycles: int
    active_checkpoint_id: str
    active_model_score: float
    active_harness_id: str
    active_harness_score: float
    total_cost_usd: float
    pending_approval_request_id: str | None
    report_path: str

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "state": self.state,
            "current_cycle": self.current_cycle,
            "completed_cycles": self.completed_cycles,
            "active_checkpoint_id": self.active_checkpoint_id,
            "active_model_score": self.active_model_score,
            "active_harness_id": self.active_harness_id,
            "active_harness_score": self.active_harness_score,
            "total_cost_usd": self.total_cost_usd,
            "pending_approval_request_id": self.pending_approval_request_id,
            "report_path": self.report_path,
        }


@dataclass(frozen=True, slots=True)
class _CommittedRecords:
    final_transaction_id: str
    snapshot_transactions: dict[str, str]
    decision_transactions: dict[str, str]


class _ReferenceTeardown:
    def __init__(self, *, clock: ReferenceClock, cycle: int) -> None:
        self.clock = clock
        self.cycle = cycle
        self.calls = 0

    def teardown(self, lease: Any) -> ModelTeardownObservation:
        self.calls += 1
        return ModelTeardownObservation(
            deployment_id=lease.deployment_id,
            checkpoint_id=lease.checkpoint_id,
            torn_down=True,
            completed_at=self.clock.at(
                cycle=self.cycle,
                ordinal=730 + self.calls,
            ),
            evidence_ids=(
                f"ev-reference-teardown-c{self.cycle:02d}-{self.calls}",
            ),
        )


class CoEvolutionController:
    """Durable deterministic reference composition of outer/middle/inner loops."""

    def __init__(
        self,
        config: PipelineConfig,
        *,
        workspace: str | Path,
        run_id: str,
        clock: ReferenceClock | None = None,
    ) -> None:
        self.config = config
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.clock = clock or ReferenceClock(origin="2026-08-14T00:00:00Z")
        self.config_sha256 = hashlib.sha256(
            canonical_json(config.to_dict()).encode("utf-8")
        ).hexdigest()

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
        self.harness_snapshot_store = HarnessSnapshotStore(
            self.workspace,
            self.control_store,
        )
        self.harness_pointer_store = HarnessPointerStore(
            self.workspace,
            self.control_store,
            self.harness_snapshot_store,
        )
        self.run_store = CoEvolutionRunStore(self.workspace)
        self.verifier = VerificationPipeline(
            config.verification,
            benchmark_texts=config.benchmark_texts,
        )
        self.approval_service = ApprovalService(
            store=ApprovalStore(self.workspace),
            policy=ApprovalPolicy(
                policy_id="coevolution-reference-v1",
                dataset_review_required=config.approval.dataset_review_required,
                checkpoint_review_required=config.approval.checkpoint_review_required,
                harness_review_required=config.approval.harness_review_required,
                sample_rate=config.approval.sample_rate,
                min_sample_items=config.approval.min_sample_items,
                max_sample_items=config.approval.max_sample_items,
                decision_ttl_seconds=config.approval.decision_ttl_seconds,
                allowed_reviewer_roles=config.approval.allowed_reviewer_roles,
            ),
            clock=lambda: self.clock.at(cycle=1, ordinal=9_000),
        )
        self.task_suite = ReferenceTaskSuite.default()

    def run(self) -> CoEvolutionRunResult:
        if not self.run_store.exists():
            self._bootstrap()
        else:
            self.run_store.load(
                expected_run_id=self.run_id,
                expected_config_sha256=self.config_sha256,
            )

        for _ in range(256):
            metadata = self.run_store.load(
                expected_run_id=self.run_id,
                expected_config_sha256=self.config_sha256,
            )
            snapshot = self.control_store.load_snapshot(
                metadata.latest_snapshot_id
            )
            state = snapshot.state

            if state in {ControlState.STOPPED, ControlState.ABORTED}:
                return self._result(metadata, snapshot)
            if metadata.pending_approval_request_id is not None:
                progressed = self._resume_pending_approval(metadata, snapshot)
                if not progressed:
                    refreshed = self.run_store.load(
                        expected_run_id=self.run_id,
                        expected_config_sha256=self.config_sha256,
                    )
                    return self._result(
                        refreshed,
                        self.control_store.load_snapshot(
                            refreshed.latest_snapshot_id
                        ),
                    )
                continue
            if state is ControlState.MUTATE_HARNESS:
                self._run_harness_attempt(metadata, snapshot)
                continue
            if state is ControlState.HARVEST_TRACES:
                self._run_trace_batch(metadata, snapshot)
                continue
            if state is ControlState.TRAIN_MODEL:
                self._run_model_inner_loop(metadata, snapshot)
                continue
            if state is ControlState.SLIM_HARNESS:
                self._slim_harness_after_model_promotion(metadata, snapshot)
                continue
            if state is ControlState.FREEZE_MODEL:
                if metadata.completed_cycles >= self.config.co_evolution.max_cycles:
                    self._stop_cycle_limit(metadata, snapshot)
                else:
                    self._begin_outer_cycle(metadata, snapshot)
                continue
            raise RuntimeError(
                f"unsupported durable Co-Evolution state: {state.value}"
            )
        raise RuntimeError("Co-Evolution controller exceeded its bounded step limit")

    def _bootstrap(self) -> None:
        cycle = 1
        timestamp = self.clock.at(cycle=cycle, ordinal=1)
        initial_model_score = float(self.config.rsi.initial_score)
        initial_harness_score = 0.50
        initial_harness = self._bootstrap_harness_spec()

        model_artifact = self.workspace / "model-artifacts" / "bootstrap.json"
        model_artifact.parent.mkdir(parents=True, exist_ok=True)
        model_payload = {
            "schema_version": "reference-bootstrap-model/v1",
            "model_id": self.config.model_id,
            "score": initial_model_score,
        }
        model_bytes = _json_bytes(model_payload)
        _write_immutable_or_equal(model_artifact, model_bytes)
        model_sha256 = hashlib.sha256(model_bytes).hexdigest()
        checkpoint_id = f"checkpoint-bootstrap-{model_sha256[:20]}"

        config_path = self.workspace / "reports" / "coevolution-config.json"
        _write_immutable_or_equal(
            config_path,
            _json_bytes(self.config.to_dict()),
        )
        evidence = (
            EvidenceRecord(
                evidence_id=_record_id("ev-config", self.run_id, 0, "config"),
                run_id=self.run_id,
                iteration=0,
                kind=EvidenceKind.CONFIG,
                producer="coevolution-controller",
                uri=config_path.resolve().as_uri(),
                created_at=timestamp,
                sha256=_sha256_file(config_path),
                metadata={"config_sha256": self.config_sha256},
            ),
            EvidenceRecord(
                evidence_id=_record_id(
                    "ev-bootstrap-checkpoint",
                    self.run_id,
                    0,
                    checkpoint_id,
                ),
                run_id=self.run_id,
                iteration=0,
                kind=EvidenceKind.CHECKPOINT,
                producer="coevolution-controller",
                uri=model_artifact.resolve().as_uri(),
                created_at=timestamp,
                sha256=model_sha256,
                metadata={
                    "checkpoint_id": checkpoint_id,
                    "model_id": self.config.model_id,
                },
            ),
            EvidenceRecord(
                evidence_id=_record_id(
                    "ev-bootstrap-evaluation",
                    self.run_id,
                    0,
                    checkpoint_id,
                ),
                run_id=self.run_id,
                iteration=0,
                kind=EvidenceKind.EVALUATION_RESULT,
                producer="coevolution-controller",
                uri=f"artifact://bootstrap-evaluation/{checkpoint_id}.json",
                created_at=timestamp,
                metadata={
                    "checkpoint_id": checkpoint_id,
                    "score": initial_model_score,
                    "benchmark_id": self.task_suite.benchmark_id,
                },
            ),
        )
        evidence_ids = tuple(item.evidence_id for item in evidence)
        decision = DecisionRecord(
            decision_id=_record_id(
                "decision-bootstrap-model",
                self.run_id,
                0,
                checkpoint_id,
            ),
            run_id=self.run_id,
            iteration=0,
            subject_type=DecisionSubject.CHECKPOINT,
            subject_id=checkpoint_id,
            action=DecisionAction.PROMOTE,
            reason_code="bootstrap_reference_model",
            reason="Establish the deterministic accepted reference model.",
            evidence_ids=evidence_ids,
            created_at=timestamp,
            metadata={"score": initial_model_score},
        )
        transition = TransitionRecord(
            transition_id=_record_id(
                "transition-bootstrap-model",
                self.run_id,
                0,
                checkpoint_id,
            ),
            run_id=self.run_id,
            iteration=0,
            from_state=None,
            event=ControlEvent.START,
            to_state=ControlState.FREEZE_MODEL,
            occurred_at=timestamp,
            idempotency_key=_record_id(
                "idempotency-bootstrap-model",
                self.run_id,
                0,
                checkpoint_id,
            ),
            decision_id=decision.decision_id,
            evidence_ids=evidence_ids,
        )
        snapshot = StateSnapshot(
            snapshot_id=_record_id(
                "snapshot-bootstrap-model",
                self.run_id,
                0,
                checkpoint_id,
            ),
            run_id=self.run_id,
            iteration=0,
            cycle=cycle,
            state=ControlState.FREEZE_MODEL,
            entered_at=timestamp,
            active_checkpoint_id=checkpoint_id,
            peak_checkpoint_id=checkpoint_id,
            active_harness_id=initial_harness.harness_id,
            peak_score=initial_harness_score,
            total_cost_usd=0.0,
            evidence_ids=evidence_ids,
            metadata={
                "decision_id": decision.decision_id,
                "active_model_score": initial_model_score,
            },
        )
        committed_model = self._commit_records(
            (*evidence, decision, transition, snapshot),
            label="bootstrap-model",
        )
        model_transaction_id = committed_model.final_transaction_id
        lineage = LineageManifest(
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=None,
            dataset_commit_hash=model_sha256,
            dataset_path=model_artifact.as_posix(),
            teacher_api_version="reference-bootstrap-v1",
            teacher_model="reference-bootstrap",
            teacher_prompt_hash=initial_harness.content_sha256,
            filter_config_version=self.verifier.config_hash,
            rejected_data_count=0,
            training_loss_final=0.0,
            benchmark_score=initial_model_score,
            model_id=self.config.model_id,
            code_git_commit="reference-coevolution",
            iteration=0,
            status="PEAK",
            created_at=timestamp,
        )
        bundle = self.checkpoint_store.commit(
            checkpoint_id=checkpoint_id,
            run_id=self.run_id,
            iteration=0,
            checkpoint_payload={
                "checkpoint_id": checkpoint_id,
                "model_id": self.config.model_id,
                "artifact_sha256": model_sha256,
                "score": initial_model_score,
                "status": "PEAK",
            },
            lineage_manifest=lineage,
            artifact_path=model_artifact,
            artifact_uri=model_artifact.resolve().as_uri(),
            control_transaction_id=model_transaction_id,
            created_at=timestamp,
        )
        self.peak_store.compare_and_swap(
            PeakPointer(
                run_id=self.run_id,
                checkpoint_id=checkpoint_id,
                previous_checkpoint_id=None,
                model_id=self.config.model_id,
                iteration=0,
                score=initial_model_score,
                decision_id=decision.decision_id,
                control_transaction_id=model_transaction_id,
                checkpoint_bundle_sha256=bundle.manifest.manifest_sha256,
                updated_at=timestamp,
            ),
            expected_previous_checkpoint_id=None,
        )

        harness_timestamp = self.clock.at(cycle=cycle, ordinal=2)
        harness_path = self.workspace / "harness" / "bootstrap-harness.json"
        _write_immutable_or_equal(
            harness_path,
            _json_bytes(initial_harness.to_dict()),
        )
        harness_evidence = EvidenceRecord(
            evidence_id=_record_id(
                "ev-bootstrap-harness",
                self.run_id,
                0,
                initial_harness.harness_id,
            ),
            run_id=self.run_id,
            iteration=0,
            kind=EvidenceKind.HARNESS_SNAPSHOT,
            producer="coevolution-controller",
            uri=harness_path.resolve().as_uri(),
            created_at=harness_timestamp,
            sha256=initial_harness.content_sha256,
            metadata={
                "harness_id": initial_harness.harness_id,
                "score": initial_harness_score,
            },
        )
        harness_decision = DecisionRecord(
            decision_id=_record_id(
                "decision-bootstrap-harness",
                self.run_id,
                0,
                initial_harness.harness_id,
            ),
            run_id=self.run_id,
            iteration=0,
            subject_type=DecisionSubject.HARNESS,
            subject_id=initial_harness.harness_id,
            action=DecisionAction.ACCEPT,
            reason_code="bootstrap_reference_harness",
            reason="Establish the deterministic accepted reference Harness.",
            evidence_ids=(harness_evidence.evidence_id,),
            created_at=harness_timestamp,
            metadata={"score": initial_harness_score},
        )
        harness_transition = TransitionRecord(
            transition_id=_record_id(
                "transition-bootstrap-harness",
                self.run_id,
                0,
                initial_harness.harness_id,
            ),
            run_id=self.run_id,
            iteration=0,
            from_state=ControlState.FREEZE_MODEL,
            event=ControlEvent.HARNESS_APPROVED,
            to_state=ControlState.FREEZE_MODEL,
            occurred_at=harness_timestamp,
            idempotency_key=_record_id(
                "idempotency-bootstrap-harness",
                self.run_id,
                0,
                initial_harness.harness_id,
            ),
            decision_id=harness_decision.decision_id,
            evidence_ids=(harness_evidence.evidence_id,),
        )
        harness_snapshot = replace(
            snapshot,
            snapshot_id=_record_id(
                "snapshot-bootstrap-harness",
                self.run_id,
                0,
                initial_harness.harness_id,
            ),
            entered_at=harness_timestamp,
            evidence_ids=(harness_evidence.evidence_id,),
            metadata={
                "decision_id": harness_decision.decision_id,
                "active_model_score": initial_model_score,
            },
        )
        committed_harness = self._commit_records(
            (
                harness_evidence,
                harness_decision,
                harness_transition,
                harness_snapshot,
            ),
            label="bootstrap-harness",
        )
        harness_transaction_id = committed_harness.final_transaction_id
        harness_bundle = self.harness_snapshot_store.commit(
            initial_harness,
            run_id=self.run_id,
            cycle=cycle,
            score=initial_harness_score,
            status="ACTIVE",
            control_transaction_id=harness_transaction_id,
            created_at=harness_timestamp,
        )
        self.harness_pointer_store.compare_and_swap(
            HarnessPointer(
                run_id=self.run_id,
                harness_id=initial_harness.harness_id,
                previous_harness_id=None,
                cycle=cycle,
                score=initial_harness_score,
                decision_id=harness_decision.decision_id,
                control_transaction_id=harness_transaction_id,
                snapshot_manifest_sha256=(
                    harness_bundle.manifest.manifest_sha256
                ),
                updated_at=harness_timestamp,
            ),
            expected_previous_harness_id=None,
        )

        outer_policy = self._outer_policy()
        start_evidence = (
            EvidenceRecord(
                evidence_id=_record_id(
                    "ev-peak-pointer",
                    self.run_id,
                    0,
                    checkpoint_id,
                ),
                run_id=self.run_id,
                iteration=0,
                kind=EvidenceKind.PEAK_POINTER,
                producer="coevolution-controller",
                uri=(self.workspace / "peak_checkpoint.json").resolve().as_uri(),
                created_at=self.clock.at(cycle=cycle, ordinal=3),
                sha256=_sha256_file(self.workspace / "peak_checkpoint.json"),
                metadata={"checkpoint_id": checkpoint_id},
            ),
            EvidenceRecord(
                evidence_id=_record_id(
                    "ev-harness-pointer",
                    self.run_id,
                    0,
                    initial_harness.harness_id,
                ),
                run_id=self.run_id,
                iteration=0,
                kind=EvidenceKind.HARNESS_SNAPSHOT,
                producer="coevolution-controller",
                uri=(self.workspace / "active_harness.json").resolve().as_uri(),
                created_at=self.clock.at(cycle=cycle, ordinal=3),
                sha256=_sha256_file(self.workspace / "active_harness.json"),
                metadata={"harness_id": initial_harness.harness_id},
            ),
        )
        start_step = outer_policy.start(
            run_id=self.run_id,
            cycle=cycle,
            active_model_checkpoint_id=checkpoint_id,
            active_harness=initial_harness,
            active_score=initial_harness_score,
            started_at=self.clock.at(cycle=cycle, ordinal=4),
            evidence_ids=tuple(item.evidence_id for item in start_evidence),
            total_cost_usd=0.0,
        )
        committed_start = self._commit_step(
            start_step,
            evidence=start_evidence,
            label="outer-start",
        )
        final_snapshot = start_step.final_snapshot
        final_transaction = committed_start.snapshot_transactions[
            final_snapshot.snapshot_id
        ]
        self.run_store.create(
            CoEvolutionRunMetadata(
                run_id=self.run_id,
                config_sha256=self.config_sha256,
                revision=0,
                state=final_snapshot.state,
                current_cycle=cycle,
                completed_cycles=0,
                active_checkpoint_id=checkpoint_id,
                active_model_score=initial_model_score,
                active_harness_id=initial_harness.harness_id,
                active_harness_score=initial_harness_score,
                latest_snapshot_id=final_snapshot.snapshot_id,
                latest_transaction_id=final_transaction,
                pending_approval_request_id=None,
                pending_approval_request_sha256=None,
                pending_approval_subject=None,
                status="RUNNING",
                created_at=timestamp,
                updated_at=self.clock.at(cycle=cycle, ordinal=5),
            )
        )

    def _run_harness_attempt(
        self,
        metadata: CoEvolutionRunMetadata,
        current: StateSnapshot,
    ) -> None:
        pointer = self.harness_pointer_store.load()
        if pointer is None or pointer.harness_id != metadata.active_harness_id:
            raise RuntimeError("active Harness pointer does not match Run metadata")
        active_bundle = self.harness_snapshot_store.load(pointer.harness_id)
        attempt = current.iteration
        proposal = HarnessMutationProposal(
            mutation_id=f"mutation-c{current.cycle:02d}-a{attempt:02d}",
            parent_harness_id=active_bundle.spec.harness_id,
            prompt_appendix=(
                "After each tool call, verify the observable state and terminal result."
                if attempt == 1
                else (
                    f"Attempt {attempt}: preserve observable state checks and bounded "
                    "retries without adding hidden reasoning."
                )
            ),
            add_tools=("browser",) if attempt == 1 else (),
            max_attempts=min(4, active_bundle.spec.retry_policy.max_attempts + 1),
            timeout_seconds=active_bundle.spec.timeout_seconds,
            max_steps=active_bundle.spec.max_steps,
            metadata={"cycle": current.cycle, "attempt": attempt},
        )
        candidate = HarnessMutator().apply(active_bundle.spec, proposal)
        candidate_path = (
            self.workspace
            / "harness"
            / "candidates"
            / f"{candidate.harness_id}.json"
        )
        _write_immutable_or_equal(candidate_path, _json_bytes(candidate.to_dict()))
        mutation_evidence = EvidenceRecord(
            evidence_id=_record_id(
                "ev-harness-mutation",
                self.run_id,
                current.iteration,
                candidate.harness_id,
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            kind=EvidenceKind.HARNESS_SNAPSHOT,
            producer="coevolution-controller",
            uri=candidate_path.resolve().as_uri(),
            created_at=self.clock.at(cycle=current.cycle, ordinal=100 + attempt * 10),
            sha256=candidate.content_sha256,
            metadata={
                "candidate_harness_id": candidate.harness_id,
                "parent_harness_id": candidate.parent_harness_id,
                "mutation_id": proposal.mutation_id,
            },
        )
        outer_policy = self._outer_policy()
        created_step = outer_policy.candidate_created(
            current,
            candidate,
            created_at=mutation_evidence.created_at,
            evidence_ids=(mutation_evidence.evidence_id,),
        )
        self._commit_step(
            created_step,
            evidence=(mutation_evidence,),
            label="harness-mutated",
        )
        validation_evidence_id = _record_id(
            "ev-harness-validation",
            self.run_id,
            current.iteration,
            candidate.harness_id,
        )
        validation = HarnessValidator(
            allowed_tools=("search", "calculator", "browser"),
            max_attempts=8,
            max_timeout_seconds=300.0,
            max_steps=256,
        ).validate(
            candidate,
            evidence_ids=(validation_evidence_id,),
            validated_at=self.clock.at(
                cycle=current.cycle,
                ordinal=101 + attempt * 10,
            ),
        )
        validation_path = (
            self.workspace
            / "harness"
            / "validation"
            / f"{candidate.harness_id}.json"
        )
        validation_payload = {
            "candidate_harness_id": validation.candidate_harness_id,
            "valid": validation.valid,
            "reasons": list(validation.reasons),
            "metrics": dict(validation.metrics),
            "validated_at": validation.validated_at,
        }
        _write_immutable_or_equal(
            validation_path,
            _json_bytes(validation_payload),
        )
        validation_evidence = EvidenceRecord(
            evidence_id=validation_evidence_id,
            run_id=self.run_id,
            iteration=created_step.final_snapshot.iteration,
            kind=EvidenceKind.VERIFICATION_AUDIT,
            producer="coevolution-controller",
            uri=validation_path.resolve().as_uri(),
            created_at=validation.validated_at,
            sha256=_sha256_file(validation_path),
            metadata={
                "candidate_harness_id": candidate.harness_id,
                "valid": validation.valid,
                "reasons": list(validation.reasons),
            },
        )
        validated_step = outer_policy.validation_completed(
            created_step.final_snapshot,
            validation,
        )
        committed_validation = self._commit_step(
            validated_step,
            evidence=(validation_evidence,),
            label="harness-validated",
        )
        if validated_step.final_snapshot.state is not ControlState.EVALUATE_HARNESS:
            self._update_run_from_step(
                metadata,
                validated_step,
                committed_validation,
            )
            return

        target_score = reference_harness_candidate_score(
            cycle=current.cycle,
            attempt=attempt,
            active_score=metadata.active_harness_score,
            min_improvement=self.config.co_evolution.harness_min_improvement,
        )
        evaluation_evidence_id = _record_id(
            "ev-harness-evaluation",
            self.run_id,
            current.iteration,
            candidate.harness_id,
        )

        def runner(_: HarnessSpec, task: Any) -> HarnessTaskResult:
            family_offset = {
                "tool-use": 0.000,
                "state-verification": 0.002,
                "boundary-handling": -0.002,
                "safety": 0.001,
            }.get(task.task_family, 0.0)
            score = min(1.0, max(0.0, target_score + family_offset))
            return HarnessTaskResult(
                task_id=task.task_id,
                task_family=task.task_family,
                score=score,
                success=score >= 0.45,
                failure_code=None if score >= 0.45 else "REFERENCE_TASK_FAILED",
                observable_trace_uri=(
                    f"artifact://reference-harness-evaluations/"
                    f"{candidate.harness_id}/{task.task_id}.json"
                ),
                metadata={
                    "cycle": current.cycle,
                    "attempt": attempt,
                    "observable_only": True,
                },
            )

        evaluation = DeterministicHarnessEvaluator(
            benchmark_id=self.task_suite.benchmark_id,
            runner=runner,
        ).evaluate(
            candidate,
            self.task_suite.tasks,
            evaluated_at=self.clock.at(
                cycle=current.cycle,
                ordinal=102 + attempt * 10,
            ),
            evidence_ids=(evaluation_evidence_id,),
            cost_usd=0.10,
            metadata={"cycle": current.cycle, "attempt": attempt},
        )
        evaluation_path = (
            self.workspace
            / "harness"
            / "evaluations"
            / f"{candidate.harness_id}.json"
        )
        _write_immutable_or_equal(
            evaluation_path,
            _json_bytes(
                {
                    "harness_id": evaluation.harness_id,
                    "benchmark_id": evaluation.benchmark_id,
                    "score": evaluation.score,
                    "task_family_scores": evaluation.task_family_scores,
                    "task_results": [
                        {
                            "task_id": item.task_id,
                            "task_family": item.task_family,
                            "score": item.score,
                            "success": item.success,
                            "failure_code": item.failure_code,
                            "observable_trace_uri": item.observable_trace_uri,
                            "metadata": item.metadata,
                        }
                        for item in evaluation.task_results
                    ],
                    "cost_usd": evaluation.cost_usd,
                    "evaluated_at": evaluation.evaluated_at,
                }
            ),
        )
        evaluation_evidence = EvidenceRecord(
            evidence_id=evaluation_evidence_id,
            run_id=self.run_id,
            iteration=validated_step.final_snapshot.iteration,
            kind=EvidenceKind.EVALUATION_RESULT,
            producer="coevolution-controller",
            uri=evaluation_path.resolve().as_uri(),
            created_at=evaluation.evaluated_at,
            sha256=_sha256_file(evaluation_path),
            metadata={
                "harness_id": candidate.harness_id,
                "score": evaluation.score,
                "task_family_scores": dict(evaluation.task_family_scores),
                "cost_usd": evaluation.cost_usd,
            },
        )
        evaluated_step = outer_policy.evaluation_completed(
            validated_step.final_snapshot,
            evaluation,
        )
        committed_evaluation = self._commit_step(
            evaluated_step,
            evidence=(evaluation_evidence,),
            label="harness-evaluated",
        )

        if evaluated_step.snapshots[0].state is ControlState.ACCEPT_HARNESS:
            accept_decision = evaluated_step.decisions[0]
            transaction_id = committed_evaluation.decision_transactions[
                accept_decision.decision_id
            ]
            bundle = self.harness_snapshot_store.commit(
                candidate,
                run_id=self.run_id,
                cycle=current.cycle,
                score=evaluation.score,
                status="ACTIVE",
                control_transaction_id=transaction_id,
                created_at=evaluation.evaluated_at,
            )
            self.harness_pointer_store.compare_and_swap(
                HarnessPointer(
                    run_id=self.run_id,
                    harness_id=candidate.harness_id,
                    previous_harness_id=metadata.active_harness_id,
                    cycle=current.cycle,
                    score=evaluation.score,
                    decision_id=accept_decision.decision_id,
                    control_transaction_id=transaction_id,
                    snapshot_manifest_sha256=bundle.manifest.manifest_sha256,
                    updated_at=evaluation.evaluated_at,
                ),
                expected_previous_harness_id=metadata.active_harness_id,
            )
            metadata = replace(
                metadata,
                active_harness_id=candidate.harness_id,
                active_harness_score=evaluation.score,
            )

        if evaluated_step.final_snapshot.state is ControlState.HARNESS_REVIEW_PENDING:
            self._update_run_from_step(
                metadata,
                evaluated_step,
                committed_evaluation,
            )
            refreshed = self.run_store.load()
            self._create_approval_request(
                refreshed,
                evaluated_step.final_snapshot,
                subject_type=DecisionSubject.HARNESS,
                subject_id=candidate.harness_id,
                subject_sha256=candidate.content_sha256,
                requested_action=DecisionAction.ACCEPT,
                source_evidence_ids=(evaluation_evidence.evidence_id,),
                candidate_metadata={"score": evaluation.score},
            )
            return
        self._update_run_from_step(
            metadata,
            evaluated_step,
            committed_evaluation,
        )

    def _run_trace_batch(
        self,
        metadata: CoEvolutionRunMetadata,
        current: StateSnapshot,
    ) -> None:
        count = max(1, self.config.co_evolution.target_traces)
        trajectories = build_reference_trajectories(
            run_id=self.run_id,
            cycle=current.cycle,
            model_checkpoint_id=metadata.active_checkpoint_id,
            harness_id=metadata.active_harness_id,
            count=count,
            score=max(0.55, metadata.active_model_score),
            clock=self.clock,
        )
        raw_path = (
            self.workspace
            / "reference-traces"
            / f"cycle-{current.cycle:03d}.jsonl"
        )
        _write_immutable_or_equal(
            raw_path,
            b"".join(
                (trace.to_json() + "\n").encode("utf-8")
                for trace in trajectories
            ),
        )
        harvest_evidence = EvidenceRecord(
            evidence_id=_record_id(
                "ev-reference-traces",
                self.run_id,
                current.iteration,
                f"cycle-{current.cycle}",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            kind=EvidenceKind.TRACE_DATASET,
            producer="coevolution-controller",
            uri=raw_path.resolve().as_uri(),
            created_at=self.clock.at(cycle=current.cycle, ordinal=400),
            sha256=_sha256_file(raw_path),
            metadata={
                "cycle": current.cycle,
                "trace_count": len(trajectories),
                "model_checkpoint_id": metadata.active_checkpoint_id,
                "harness_id": metadata.active_harness_id,
                "observable_only": True,
            },
        )
        harvester = TraceHarvester(
            TraceHarvestConfig(
                target_count=count,
                min_score=0.50,
                max_per_task_family=max(1, count),
            )
        )
        batch = harvester.harvest(
            trajectories,
            expected_run_id=self.run_id,
            expected_cycle=current.cycle,
            expected_model_checkpoint_id=metadata.active_checkpoint_id,
            expected_harness_id=metadata.active_harness_id,
            selection_seed=f"trace-seed-c{current.cycle:03d}",
            created_at=self.clock.at(cycle=current.cycle, ordinal=401),
            evidence_ids=(harvest_evidence.evidence_id,),
            cost_usd=0.10,
            metadata={"source": "reference-runtime"},
        )
        trace_policy = self._trace_policy()
        harvested_step = trace_policy.batch_harvested(current, batch)
        self._commit_step(
            harvested_step,
            evidence=(harvest_evidence,),
            label="trace-harvested",
        )
        verification_service = TraceVerificationService(
            verifier=VerificationPipeline(
                self.config.verification,
                benchmark_texts=self.config.benchmark_texts,
            ),
            output_root=self.workspace,
            harvester=harvester,
        )
        verification_bundle = verification_service.verify(
            batch,
            created_at=self.clock.at(cycle=current.cycle, ordinal=402),
        )
        adjusted_evidence = tuple(
            replace(
                evidence,
                iteration=harvested_step.final_snapshot.iteration,
            )
            for evidence in verification_bundle.evidence_records
        )
        verified_step = trace_policy.verification_completed(
            harvested_step.final_snapshot,
            verification_bundle.result,
        )
        committed_verify = self._commit_step(
            verified_step,
            evidence=adjusted_evidence,
            label="trace-verified",
        )
        self._update_run_from_step(
            metadata,
            verified_step,
            committed_verify,
        )
        if verified_step.final_snapshot.state is ControlState.TRAIN_MODEL and (
            self.config.approval.dataset_review_required
        ):
            refreshed = self.run_store.load()
            self._enter_dataset_review(
                refreshed,
                verified_step.final_snapshot,
                verification_bundle.result,
            )

    def _run_model_inner_loop(
        self,
        metadata: CoEvolutionRunMetadata,
        current: StateSnapshot,
    ) -> None:
        current_metadata = dict(current.metadata)
        for key, expected in (
            ("active_model_score", metadata.active_model_score),
            ("active_harness_score", metadata.active_harness_score),
        ):
            value = current_metadata.get(key)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or float(value) != expected
            ):
                raise RuntimeError(
                    f"Snapshot metadata.{key} does not match durable Run metadata"
                )
            current_metadata[key] = expected
        current = replace(current, metadata=current_metadata)
        dataset_id = _metadata_str(current, "trace_dataset_id")
        dataset_path = _metadata_str(current, "trace_dataset_path")
        dataset_sha256 = _metadata_str(current, "trace_dataset_sha256")
        accepted_count = _metadata_int(current, "accepted_trace_count", 0)
        if accepted_count < 1:
            accepted_count = _metadata_int(
                current,
                "verified_trace_count",
                self.config.co_evolution.target_traces,
            )
        request = ModelTrainingRequest.create(
            run_id=self.run_id,
            cycle=current.cycle,
            model_id=self.config.model_id,
            parent_checkpoint_id=metadata.active_checkpoint_id,
            dataset_id=dataset_id,
            dataset_path=dataset_path,
            dataset_sha256=dataset_sha256,
            accepted_example_count=accepted_count,
            algorithm=ModelTrainingAlgorithm.SFT,
            requested_at=self.clock.at(cycle=current.cycle, ordinal=600),
            evidence_ids=current.evidence_ids,
            metadata={
                "active_harness_id": metadata.active_harness_id,
                "reference_runtime": True,
            },
        )
        target_score = reference_model_score(
            cycle=current.cycle,
            active_score=metadata.active_model_score,
            min_improvement=self.config.co_evolution.model_min_improvement,
        )
        executor = ModelInnerExecutor(
            artifact_root=self.workspace / "model-artifacts",
            dataset_root=self.workspace / "trace-datasets",
        )
        execution = executor.run(
            request,
            trainer=ReferenceModelTrainer(
                artifact_root=self.workspace / "model-artifacts",
                expected_score=target_score,
                clock=self.clock,
            ),
            deployer=ReferenceModelDeployer(clock=self.clock),
            evaluator=ReferenceModelEvaluator(
                benchmark_id=self.task_suite.benchmark_id,
                clock=self.clock,
            ),
            teardown=_ReferenceTeardown(
                clock=self.clock,
                cycle=current.cycle,
            ),
        )
        candidate_path = (
            self.workspace
            / "model-candidates"
            / f"{execution.bundle.candidate.checkpoint_id}.json"
        )
        _write_immutable_or_equal(
            candidate_path,
            _json_bytes(execution.bundle.candidate.to_dict()),
        )
        policy = self._model_policy()
        candidate_evidence_ids = execution.bundle.candidate.evidence_ids
        training_templates = (
            execution.evidence.training,
            execution.evidence.checkpoint,
        )
        training_evidence_items: list[EvidenceRecord] = []
        for index, template in enumerate(training_templates):
            evidence_id = (
                candidate_evidence_ids[index]
                if index < len(candidate_evidence_ids)
                else template.evidence_id
            )
            training_evidence_items.append(
                replace(
                    template,
                    evidence_id=evidence_id,
                    iteration=current.iteration,
                )
            )
        for evidence_id in candidate_evidence_ids[len(training_templates) :]:
            training_evidence_items.append(
                replace(
                    execution.evidence.training,
                    evidence_id=evidence_id,
                    iteration=current.iteration,
                )
            )
        training_evidence = tuple(training_evidence_items)
        trained_step = policy.training_completed(
            current,
            execution.bundle.candidate,
        )
        self._commit_step(
            trained_step,
            evidence=training_evidence,
            label="model-trained",
        )
        evaluation_evidence_items: list[EvidenceRecord] = []
        for template, evidence_ids in (
            (execution.evidence.serving, execution.bundle.serving.evidence_ids),
            (
                execution.evidence.evaluation,
                execution.bundle.evaluation.evidence_ids,
            ),
            (
                execution.evidence.teardown,
                execution.bundle.teardown.evidence_ids,
            ),
        ):
            for evidence_id in evidence_ids:
                evaluation_evidence_items.append(
                    replace(
                        template,
                        evidence_id=evidence_id,
                        iteration=trained_step.final_snapshot.iteration,
                    )
                )
        evaluation_evidence = tuple(evaluation_evidence_items)
        evaluated_step = policy.evaluation_completed(
            trained_step.final_snapshot,
            execution.bundle.evaluation,
        )
        committed_evaluation = self._commit_step(
            evaluated_step,
            evidence=evaluation_evidence,
            label="model-evaluated",
        )
        self._update_run_from_step(
            metadata,
            evaluated_step,
            committed_evaluation,
        )
        if evaluated_step.final_snapshot.state is ControlState.MODEL_REVIEW_PENDING:
            refreshed = self.run_store.load()
            self._create_approval_request(
                refreshed,
                evaluated_step.final_snapshot,
                subject_type=DecisionSubject.CHECKPOINT,
                subject_id=execution.bundle.candidate.checkpoint_id,
                subject_sha256=execution.bundle.candidate.artifact_sha256,
                requested_action=DecisionAction.PROMOTE,
                source_evidence_ids=tuple(
                    evidence.evidence_id for evidence in evaluation_evidence
                ),
                candidate_metadata={
                    "score": execution.bundle.evaluation.score,
                    "artifact_path": execution.bundle.candidate.artifact_path,
                },
            )
            return
        if evaluated_step.final_snapshot.state is ControlState.PROMOTE_MODEL:
            self._commit_model_promotion(
                self.run_store.load(),
                evaluated_step.final_snapshot,
                execution.bundle.candidate,
                execution.bundle.evaluation.score,
                committed_evaluation.decision_transactions[
                    evaluated_step.decisions[-1].decision_id
                ],
                evaluated_step.decisions[-1],
            )
            return
        if evaluated_step.final_snapshot.state is ControlState.ROLLBACK_MODEL:
            self._commit_model_rollback(
                self.run_store.load(),
                evaluated_step.final_snapshot,
                evaluated_step.decisions[-1],
                committed_evaluation.decision_transactions[
                    evaluated_step.decisions[-1].decision_id
                ],
            )

    def _commit_model_promotion(
        self,
        metadata: CoEvolutionRunMetadata,
        current: StateSnapshot,
        candidate: Any,
        score: float,
        promotion_transaction_id: str,
        promotion_decision: DecisionRecord,
    ) -> None:
        iteration = promotion_decision.iteration
        active_harness = self.harness_snapshot_store.load(
            metadata.active_harness_id
        ).spec
        lineage = LineageManifest(
            checkpoint_id=candidate.checkpoint_id,
            parent_checkpoint_id=candidate.parent_checkpoint_id,
            dataset_commit_hash=candidate.dataset_sha256,
            dataset_path=current.metadata.get(
                "trace_dataset_path",
                candidate.dataset_id,
            ),
            teacher_api_version="observable-trace-harvesting/v1",
            teacher_model=metadata.active_checkpoint_id,
            teacher_prompt_hash=active_harness.content_sha256,
            filter_config_version=self.verifier.config_hash,
            rejected_data_count=int(
                current.metadata.get("rejected_trace_count", 0)
            ),
            training_loss_final=candidate.training_loss,
            benchmark_score=score,
            model_id=candidate.model_id,
            code_git_commit="reference-coevolution",
            iteration=iteration,
            status="PEAK",
            created_at=self.clock.at(cycle=current.cycle, ordinal=800),
        )
        bundle = self.checkpoint_store.commit(
            checkpoint_id=candidate.checkpoint_id,
            run_id=self.run_id,
            iteration=iteration,
            checkpoint_payload={
                **candidate.to_dict(),
                "score": score,
                "status": "PEAK",
            },
            lineage_manifest=lineage,
            artifact_path=candidate.artifact_path,
            artifact_uri=Path(candidate.artifact_path).resolve().as_uri(),
            control_transaction_id=promotion_transaction_id,
            created_at=lineage.created_at,
        )
        self.peak_store.compare_and_swap(
            PeakPointer(
                run_id=self.run_id,
                checkpoint_id=candidate.checkpoint_id,
                previous_checkpoint_id=metadata.active_checkpoint_id,
                model_id=candidate.model_id,
                iteration=iteration,
                score=score,
                decision_id=promotion_decision.decision_id,
                control_transaction_id=promotion_transaction_id,
                checkpoint_bundle_sha256=bundle.manifest.manifest_sha256,
                updated_at=lineage.created_at,
            ),
            expected_previous_checkpoint_id=metadata.active_checkpoint_id,
        )
        peak_evidence = EvidenceRecord(
            evidence_id=_record_id(
                "ev-model-promotion-commit",
                self.run_id,
                current.iteration,
                candidate.checkpoint_id,
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            kind=EvidenceKind.PEAK_POINTER,
            producer="coevolution-controller",
            uri=(self.workspace / "peak_checkpoint.json").resolve().as_uri(),
            created_at=lineage.created_at,
            sha256=_sha256_file(self.workspace / "peak_checkpoint.json"),
            metadata={
                "checkpoint_id": candidate.checkpoint_id,
                "previous_checkpoint_id": metadata.active_checkpoint_id,
                "score": score,
                "checkpoint_bundle_sha256": bundle.manifest.manifest_sha256,
            },
        )
        committed_step = self._model_policy().promotion_committed(
            current,
            ModelPromotionCommitObservation(
                checkpoint_id=candidate.checkpoint_id,
                previous_checkpoint_id=metadata.active_checkpoint_id,
                score=score,
                checkpoint_bundle_sha256=bundle.manifest.manifest_sha256,
                committed_at=lineage.created_at,
                evidence_ids=(peak_evidence.evidence_id,),
            ),
        )
        committed = self._commit_step(
            committed_step,
            evidence=(peak_evidence,),
            label="model-promotion-committed",
        )
        updated = replace(
            metadata,
            active_checkpoint_id=candidate.checkpoint_id,
            active_model_score=score,
        )
        self._update_run_from_step(updated, committed_step, committed)

    def _commit_model_rollback(
        self,
        metadata: CoEvolutionRunMetadata,
        current: StateSnapshot,
        decision: DecisionRecord,
        transaction_id: str,
    ) -> None:
        marker = self.quarantine_store.commit(
            QuarantineMarker(
                run_id=self.run_id,
                iteration=decision.iteration,
                subject_type=DecisionSubject.CHECKPOINT,
                subject_id=decision.subject_id,
                decision_id=decision.decision_id,
                control_transaction_id=transaction_id,
                reason_code=decision.reason_code,
                reason=decision.reason,
                evidence_ids=decision.evidence_ids,
                created_at=decision.created_at,
            )
        )
        marker_path = (
            self.workspace
            / "quarantine"
            / (
                f"iter-{marker.iteration:06d}-"
                f"{marker.subject_type.value.lower()}-{marker.subject_id}.json"
            )
        )
        handoff_iteration = 0
        rollback_evidence = EvidenceRecord(
            evidence_id=_record_id(
                "ev-model-rollback-commit",
                self.run_id,
                handoff_iteration,
                decision.subject_id,
            ),
            run_id=self.run_id,
            iteration=handoff_iteration,
            kind=EvidenceKind.REGRESSION_AUDIT,
            producer="coevolution-controller",
            uri=marker_path.resolve().as_uri(),
            created_at=self.clock.at(cycle=current.cycle, ordinal=810),
            sha256=_sha256_file(marker_path),
            metadata={
                "rejected_checkpoint_id": decision.subject_id,
                "active_checkpoint_id": metadata.active_checkpoint_id,
                "reason_code": decision.reason_code,
            },
        )
        rollback_step = self._model_policy().rollback_committed(
            current,
            ModelRollbackCommitObservation(
                rejected_checkpoint_id=decision.subject_id,
                active_checkpoint_id=metadata.active_checkpoint_id,
                completed_at=rollback_evidence.created_at,
                evidence_ids=(rollback_evidence.evidence_id,),
            ),
        )
        committed = self._commit_step(
            rollback_step,
            evidence=(rollback_evidence,),
            label="model-rollback-committed",
        )
        updated = replace(
            metadata,
            completed_cycles=metadata.completed_cycles + 1,
            current_cycle=rollback_step.final_snapshot.cycle,
        )
        self._update_run_from_step(updated, rollback_step, committed)

    def _slim_harness_after_model_promotion(
        self,
        metadata: CoEvolutionRunMetadata,
        current: StateSnapshot,
    ) -> None:
        active_bundle = self.harness_snapshot_store.load(
            metadata.active_harness_id
        )
        next_cycle = current.cycle + 1
        handoff_iteration = 0
        base_prompt = active_bundle.spec.system_prompt.split("\n\n", 1)[0]
        payload: dict[str, JSONValue] = {
            "parent_harness_id": active_bundle.spec.harness_id,
            "cycle": current.cycle,
            "system_prompt": base_prompt,
            "tools": list(active_bundle.spec.tools),
            "model_checkpoint_id": metadata.active_checkpoint_id,
        }
        slim_id = content_addressed_harness_id(
            prefix="harness-slim",
            payload=payload,
        )
        slim = HarnessSpec(
            harness_id=slim_id,
            version=active_bundle.spec.version + 1,
            parent_harness_id=active_bundle.spec.harness_id,
            system_prompt=base_prompt,
            tools=active_bundle.spec.tools,
            retry_policy=RetryPolicy(max_attempts=2),
            timeout_seconds=active_bundle.spec.timeout_seconds,
            max_steps=active_bundle.spec.max_steps,
            metadata={
                "slimmed_after_checkpoint": metadata.active_checkpoint_id,
                "cycle": current.cycle,
                "prompt_chars_before": len(active_bundle.spec.system_prompt),
                "prompt_chars_after": len(base_prompt),
            },
        )
        slim_score = min(
            1.0,
            metadata.active_harness_score
            + self.config.co_evolution.harness_min_improvement
            + 0.01,
        )
        slim_path = self.workspace / "harness" / "slim" / f"{slim.harness_id}.json"
        _write_immutable_or_equal(slim_path, _json_bytes(slim.to_dict()))
        evidence = (
            EvidenceRecord(
                evidence_id=_record_id(
                    "ev-harness-slim",
                    self.run_id,
                    handoff_iteration,
                    slim.harness_id,
                ),
                run_id=self.run_id,
                iteration=handoff_iteration,
                kind=EvidenceKind.HARNESS_SNAPSHOT,
                producer="coevolution-controller",
                uri=slim_path.resolve().as_uri(),
                created_at=self.clock.at(cycle=current.cycle, ordinal=850),
                sha256=slim.content_sha256,
                metadata={
                    "harness_id": slim.harness_id,
                    "parent_harness_id": slim.parent_harness_id,
                    "score": slim_score,
                },
            ),
            EvidenceRecord(
                evidence_id=_record_id(
                    "ev-harness-slim-evaluation",
                    self.run_id,
                    handoff_iteration,
                    slim.harness_id,
                ),
                run_id=self.run_id,
                iteration=handoff_iteration,
                kind=EvidenceKind.EVALUATION_RESULT,
                producer="coevolution-controller",
                uri=f"artifact://harness-slim-evaluations/{slim.harness_id}.json",
                created_at=self.clock.at(cycle=current.cycle, ordinal=851),
                metadata={
                    "harness_id": slim.harness_id,
                    "score": slim_score,
                    "model_checkpoint_id": metadata.active_checkpoint_id,
                },
            ),
        )
        evidence_ids = tuple(item.evidence_id for item in evidence)
        decision = DecisionRecord(
            decision_id=_record_id(
                "decision-harness-slim",
                self.run_id,
                handoff_iteration,
                slim.harness_id,
            ),
            run_id=self.run_id,
            iteration=handoff_iteration,
            subject_type=DecisionSubject.HARNESS,
            subject_id=slim.harness_id,
            action=DecisionAction.ACCEPT,
            reason_code="slim_harness_under_promoted_model",
            reason=(
                "The promoted model preserves task performance with a smaller Harness."
            ),
            evidence_ids=evidence_ids,
            created_at=self.clock.at(cycle=current.cycle, ordinal=852),
            metadata={
                "score": slim_score,
                "previous_harness_id": metadata.active_harness_id,
                "prompt_chars_before": len(active_bundle.spec.system_prompt),
                "prompt_chars_after": len(base_prompt),
                "cycle": next_cycle,
                "source_cycle": current.cycle,
            },
        )
        transition = TransitionRecord(
            transition_id=_record_id(
                "transition-harness-slim",
                self.run_id,
                handoff_iteration,
                slim.harness_id,
            ),
            run_id=self.run_id,
            iteration=handoff_iteration,
            from_state=ControlState.SLIM_HARNESS,
            event=ControlEvent.HARNESS_SLIMMED,
            to_state=ControlState.FREEZE_MODEL,
            occurred_at=decision.created_at,
            idempotency_key=_record_id(
                "idempotency-harness-slim",
                self.run_id,
                handoff_iteration,
                slim.harness_id,
            ),
            decision_id=decision.decision_id,
            evidence_ids=evidence_ids,
        )
        snapshot = StateSnapshot(
            snapshot_id=_record_id(
                "snapshot-harness-slim",
                self.run_id,
                handoff_iteration,
                slim.harness_id,
            ),
            run_id=self.run_id,
            iteration=handoff_iteration,
            cycle=next_cycle,
            state=ControlState.FREEZE_MODEL,
            entered_at=decision.created_at,
            active_checkpoint_id=metadata.active_checkpoint_id,
            peak_checkpoint_id=metadata.active_checkpoint_id,
            active_harness_id=slim.harness_id,
            peak_score=slim_score,
            plateau_count=0,
            total_cost_usd=current.total_cost_usd,
            evidence_ids=evidence_ids,
            metadata={
                "decision_id": decision.decision_id,
                "active_model_score": metadata.active_model_score,
                "previous_harness_id": metadata.active_harness_id,
            },
        )
        committed = self._commit_records(
            (*evidence, decision, transition, snapshot),
            label="harness-slimmed",
        )
        transaction_id = committed.decision_transactions[decision.decision_id]
        bundle = self.harness_snapshot_store.commit(
            slim,
            run_id=self.run_id,
            cycle=next_cycle,
            score=slim_score,
            status="ACTIVE",
            control_transaction_id=transaction_id,
            created_at=decision.created_at,
        )
        self.harness_pointer_store.compare_and_swap(
            HarnessPointer(
                run_id=self.run_id,
                harness_id=slim.harness_id,
                previous_harness_id=metadata.active_harness_id,
                cycle=next_cycle,
                score=slim_score,
                decision_id=decision.decision_id,
                control_transaction_id=transaction_id,
                snapshot_manifest_sha256=bundle.manifest.manifest_sha256,
                updated_at=decision.created_at,
            ),
            expected_previous_harness_id=metadata.active_harness_id,
        )
        updated = replace(
            metadata,
            active_harness_id=slim.harness_id,
            active_harness_score=slim_score,
            completed_cycles=metadata.completed_cycles + 1,
            current_cycle=next_cycle,
        )
        self._update_run(
            updated,
            snapshot=snapshot,
            transaction_id=transaction_id,
            status="RUNNING",
        )

    def _begin_outer_cycle(
        self,
        metadata: CoEvolutionRunMetadata,
        current: StateSnapshot,
    ) -> None:
        timestamp = self.clock.at(cycle=current.cycle, ordinal=20)
        outer_iteration = 1
        evidence = EvidenceRecord(
            evidence_id=_record_id(
                "ev-cycle-freeze",
                self.run_id,
                outer_iteration,
                f"cycle-{current.cycle}",
            ),
            run_id=self.run_id,
            iteration=outer_iteration,
            kind=EvidenceKind.PEAK_POINTER,
            producer="coevolution-controller",
            uri=(self.workspace / "peak_checkpoint.json").resolve().as_uri(),
            created_at=timestamp,
            sha256=_sha256_file(self.workspace / "peak_checkpoint.json"),
            metadata={
                "cycle": current.cycle,
                "checkpoint_id": metadata.active_checkpoint_id,
                "harness_id": metadata.active_harness_id,
            },
        )
        decision = DecisionRecord(
            decision_id=_record_id(
                "decision-begin-cycle",
                self.run_id,
                outer_iteration,
                f"cycle-{current.cycle}",
            ),
            run_id=self.run_id,
            iteration=outer_iteration,
            subject_type=DecisionSubject.RUN,
            subject_id=self.run_id,
            action=DecisionAction.CONTINUE,
            reason_code="begin_harness_outer_cycle",
            reason="Begin Harness mutation under the accepted frozen model.",
            evidence_ids=(evidence.evidence_id,),
            created_at=timestamp,
            metadata={"cycle": current.cycle},
        )
        transition = TransitionRecord(
            transition_id=_record_id(
                "transition-begin-cycle",
                self.run_id,
                outer_iteration,
                f"cycle-{current.cycle}",
            ),
            run_id=self.run_id,
            iteration=outer_iteration,
            from_state=ControlState.FREEZE_MODEL,
            event=ControlEvent.NEXT_ITERATION_REQUESTED,
            to_state=ControlState.MUTATE_HARNESS,
            occurred_at=timestamp,
            idempotency_key=_record_id(
                "idempotency-begin-cycle",
                self.run_id,
                outer_iteration,
                f"cycle-{current.cycle}",
            ),
            decision_id=decision.decision_id,
            evidence_ids=(evidence.evidence_id,),
        )
        snapshot = StateSnapshot(
            snapshot_id=_record_id(
                "snapshot-begin-cycle",
                self.run_id,
                outer_iteration,
                f"cycle-{current.cycle}",
            ),
            run_id=self.run_id,
            iteration=outer_iteration,
            cycle=current.cycle,
            state=ControlState.MUTATE_HARNESS,
            entered_at=timestamp,
            active_checkpoint_id=metadata.active_checkpoint_id,
            peak_checkpoint_id=metadata.active_checkpoint_id,
            active_harness_id=metadata.active_harness_id,
            peak_score=metadata.active_harness_score,
            plateau_count=0,
            total_cost_usd=current.total_cost_usd,
            evidence_ids=(evidence.evidence_id,),
            metadata={
                "decision_id": decision.decision_id,
                "active_model_score": metadata.active_model_score,
            },
        )
        committed = self._commit_records(
            (evidence, decision, transition, snapshot),
            label="begin-cycle",
        )
        self._update_run(
            metadata,
            snapshot=snapshot,
            transaction_id=committed.final_transaction_id,
            status="RUNNING",
        )

    def _stop_cycle_limit(
        self,
        metadata: CoEvolutionRunMetadata,
        current: StateSnapshot,
    ) -> None:
        timestamp = self.clock.at(cycle=current.cycle, ordinal=999)
        evidence = EvidenceRecord(
            evidence_id=_record_id(
                "ev-cycle-limit",
                self.run_id,
                current.iteration,
                f"cycle-{current.cycle}",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            kind=EvidenceKind.DECISION,
            producer="coevolution-controller",
            uri=f"artifact://coevolution/{self.run_id}/cycle-limit",
            created_at=timestamp,
            metadata={
                "completed_cycles": metadata.completed_cycles,
                "max_cycles": self.config.co_evolution.max_cycles,
            },
        )
        decision = DecisionRecord(
            decision_id=_record_id(
                "decision-cycle-limit",
                self.run_id,
                current.iteration,
                f"cycle-{current.cycle}",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            subject_type=DecisionSubject.RUN,
            subject_id=self.run_id,
            action=DecisionAction.STOP,
            reason_code="coevolution_cycle_limit_reached",
            reason="The configured Co-Evolution cycle limit was reached.",
            evidence_ids=(evidence.evidence_id,),
            created_at=timestamp,
            stop_reason=StopReason.CYCLE_LIMIT,
        )
        transition = TransitionRecord(
            transition_id=_record_id(
                "transition-cycle-limit",
                self.run_id,
                current.iteration,
                f"cycle-{current.cycle}",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            from_state=ControlState.FREEZE_MODEL,
            event=ControlEvent.CYCLE_LIMIT_REACHED,
            to_state=ControlState.STOPPED,
            occurred_at=timestamp,
            idempotency_key=_record_id(
                "idempotency-cycle-limit",
                self.run_id,
                current.iteration,
                f"cycle-{current.cycle}",
            ),
            decision_id=decision.decision_id,
            evidence_ids=(evidence.evidence_id,),
        )
        snapshot = StateSnapshot(
            snapshot_id=_record_id(
                "snapshot-cycle-limit",
                self.run_id,
                current.iteration,
                f"cycle-{current.cycle}",
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            cycle=current.cycle,
            state=ControlState.STOPPED,
            entered_at=timestamp,
            active_checkpoint_id=metadata.active_checkpoint_id,
            peak_checkpoint_id=metadata.active_checkpoint_id,
            active_harness_id=metadata.active_harness_id,
            peak_score=metadata.active_harness_score,
            plateau_count=current.plateau_count,
            total_cost_usd=current.total_cost_usd,
            stop_reason=StopReason.CYCLE_LIMIT,
            evidence_ids=(evidence.evidence_id,),
            metadata={
                "decision_id": decision.decision_id,
                "active_model_score": metadata.active_model_score,
                "completed_cycles": metadata.completed_cycles,
            },
        )
        committed = self._commit_records(
            (evidence, decision, transition, snapshot),
            label="cycle-limit",
        )
        self._update_run(
            metadata,
            snapshot=snapshot,
            transaction_id=committed.final_transaction_id,
            status="STOPPED",
        )

    def _enter_dataset_review(
        self,
        metadata: CoEvolutionRunMetadata,
        current: StateSnapshot,
        result: Any,
    ) -> None:
        request_bundle = self.approval_service.create_request(
            run_id=self.run_id,
            iteration=current.iteration,
            subject_type=DecisionSubject.DATASET,
            subject_id=result.dataset_id,
            candidates=(
                ApprovalCandidate(
                    item_id=result.dataset_id,
                    content_sha256=result.dataset_sha256,
                    metadata={
                        "accepted_count": result.accepted_count,
                        "cycle": result.cycle,
                    },
                ),
            ),
            source_evidence_ids=result.evidence_ids,
            selection_seed=f"dataset-review-c{result.cycle:03d}",
            requested_at=self.clock.at(cycle=result.cycle, ordinal=500),
            metadata={"dataset_sha256": result.dataset_sha256},
        )
        request = request_bundle.request
        request_evidence = replace(
            request_bundle.request_evidence,
            iteration=current.iteration,
        )
        decision = DecisionRecord(
            decision_id=_record_id(
                "decision-dataset-review",
                self.run_id,
                current.iteration,
                result.dataset_id,
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            subject_type=DecisionSubject.DATASET,
            subject_id=result.dataset_id,
            action=DecisionAction.REQUEST_APPROVAL,
            reason_code="trace_dataset_review_required",
            reason="Human review is required before model training.",
            evidence_ids=(request_evidence.evidence_id,),
            created_at=request.requested_at,
            metadata={
                "approval_request_id": request.request_id,
                "approval_request_sha256": record_sha256(request.to_dict()),
                "trace_dataset_sha256": result.dataset_sha256,
            },
        )
        transition = TransitionRecord(
            transition_id=_record_id(
                "transition-dataset-review",
                self.run_id,
                current.iteration,
                result.dataset_id,
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            from_state=ControlState.TRAIN_MODEL,
            event=ControlEvent.DATA_REVIEW_REQUESTED,
            to_state=ControlState.DATA_REVIEW_PENDING,
            occurred_at=request.requested_at,
            idempotency_key=_record_id(
                "idempotency-dataset-review",
                self.run_id,
                current.iteration,
                result.dataset_id,
            ),
            decision_id=decision.decision_id,
            evidence_ids=(request_evidence.evidence_id,),
        )
        snapshot = replace(
            current,
            snapshot_id=_record_id(
                "snapshot-dataset-review",
                self.run_id,
                current.iteration,
                result.dataset_id,
            ),
            state=ControlState.DATA_REVIEW_PENDING,
            entered_at=request.requested_at,
            evidence_ids=(request_evidence.evidence_id,),
            metadata={
                **dict(current.metadata),
                "decision_id": decision.decision_id,
                "approval_request_id": request.request_id,
                "approval_request_sha256": record_sha256(request.to_dict()),
            },
        )
        committed = self._commit_records(
            (request_evidence, decision, transition, snapshot),
            label="dataset-review-requested",
        )
        self._update_run(
            metadata,
            snapshot=snapshot,
            transaction_id=committed.final_transaction_id,
            status="AWAITING_DATASET_APPROVAL",
            pending_request_id=request.request_id,
            pending_request_sha256=record_sha256(request.to_dict()),
            pending_subject="dataset",
        )

    def _create_approval_request(
        self,
        metadata: CoEvolutionRunMetadata,
        current: StateSnapshot,
        *,
        subject_type: DecisionSubject,
        subject_id: str,
        subject_sha256: str,
        requested_action: DecisionAction,
        source_evidence_ids: tuple[str, ...],
        candidate_metadata: dict[str, JSONValue],
    ) -> None:
        request_bundle = self.approval_service.create_request(
            run_id=self.run_id,
            iteration=current.iteration,
            subject_type=subject_type,
            subject_id=subject_id,
            candidates=(
                ApprovalCandidate(
                    item_id=subject_id,
                    content_sha256=subject_sha256,
                    metadata=candidate_metadata,
                ),
            ),
            source_evidence_ids=source_evidence_ids,
            selection_seed=f"approval-{subject_type.value.lower()}-{current.cycle}",
            requested_at=self.clock.at(cycle=current.cycle, ordinal=900),
            metadata={"subject_sha256": subject_sha256},
        )
        request = request_bundle.request
        if request.requested_action is not requested_action:
            raise RuntimeError("approval policy returned an unexpected action")
        request_evidence = replace(
            request_bundle.request_evidence,
            iteration=current.iteration,
        )
        committed = self._commit_records(
            (request_evidence,),
            label=f"approval-{subject_type.value.lower()}-requested",
        )
        status = {
            DecisionSubject.HARNESS: "AWAITING_HARNESS_APPROVAL",
            DecisionSubject.DATASET: "AWAITING_DATASET_APPROVAL",
            DecisionSubject.CHECKPOINT: "AWAITING_MODEL_APPROVAL",
        }[subject_type]
        self._update_run(
            metadata,
            snapshot=current,
            transaction_id=committed.final_transaction_id,
            status=status,
            pending_request_id=request.request_id,
            pending_request_sha256=record_sha256(request.to_dict()),
            pending_subject=subject_type.value.lower(),
        )

    def _resume_pending_approval(
        self,
        metadata: CoEvolutionRunMetadata,
        current: StateSnapshot,
    ) -> bool:
        request_id = metadata.pending_approval_request_id
        request_sha256 = metadata.pending_approval_request_sha256
        subject = metadata.pending_approval_subject
        if request_id is None or request_sha256 is None or subject is None:
            raise RuntimeError("pending approval metadata is incomplete")
        as_of = self.clock.at(cycle=current.cycle, ordinal=9_500)
        if self.approval_service.store.has_decision(request_id):
            recorded_decision = self.approval_service.store.load_decision(request_id)
            as_of = max(as_of, recorded_decision.decided_at)
        status = self.approval_service.status(request_id, as_of=as_of)
        if status.state in {ApprovalState.PENDING, ApprovalState.EXPIRED}:
            return False
        request = self.approval_service.store.load_request(request_id)
        if record_sha256(request.to_dict()) != request_sha256:
            raise RuntimeError("pending approval Request SHA-256 changed")
        decision = self.approval_service.store.load_decision(request_id)
        if decision.request_sha256 != request_sha256:
            raise RuntimeError("approval Decision Request SHA-256 mismatch")
        if decision.run_id != self.run_id or decision.iteration != current.iteration:
            raise RuntimeError("approval Decision Run/iteration mismatch")
        decision_path = self.approval_service.store.decision_path(request_id)
        approval_evidence = EvidenceRecord(
            evidence_id=_record_id(
                "ev-approval-decision",
                self.run_id,
                current.iteration,
                request_id,
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            kind=EvidenceKind.APPROVAL_DECISION,
            producer="coevolution-controller",
            uri=decision_path.resolve().as_uri(),
            created_at=decision.decided_at,
            sha256=_sha256_file(decision_path),
            metadata={
                "request_id": request_id,
                "subject_type": decision.subject_type.value,
                "subject_id": decision.subject_id,
                "approved": decision.approved,
                "reviewer_id": decision.reviewer_id,
                "reviewer_role": decision.reviewer_role,
            },
        )
        cleared = replace(
            metadata,
            pending_approval_request_id=None,
            pending_approval_request_sha256=None,
            pending_approval_subject=None,
        )

        if subject == "harness":
            step = self._outer_policy().review_completed(
                current,
                HarnessReviewObservation(
                    request_id=request_id,
                    candidate_harness_id=decision.subject_id,
                    approved=decision.approved,
                    reviewer_id=decision.reviewer_id,
                    reviewer_role=decision.reviewer_role,
                    evidence_ids=(approval_evidence.evidence_id,),
                    decided_at=decision.decided_at,
                ),
            )
            committed = self._commit_step(
                step,
                evidence=(approval_evidence,),
                label="harness-review-completed",
            )
            if step.snapshots[0].state is ControlState.ACCEPT_HARNESS:
                candidate_path = (
                    self.workspace
                    / "harness"
                    / "candidates"
                    / f"{decision.subject_id}.json"
                )
                candidate = HarnessSpec.from_dict(
                    json.loads(candidate_path.read_text(encoding="utf-8"))
                )
                score = float(step.snapshots[0].peak_score or 0.0)
                accept_decision = step.decisions[0]
                transaction_id = committed.decision_transactions[
                    accept_decision.decision_id
                ]
                bundle = self.harness_snapshot_store.commit(
                    candidate,
                    run_id=self.run_id,
                    cycle=current.cycle,
                    score=score,
                    status="ACTIVE",
                    control_transaction_id=transaction_id,
                    created_at=decision.decided_at,
                )
                self.harness_pointer_store.compare_and_swap(
                    HarnessPointer(
                        run_id=self.run_id,
                        harness_id=candidate.harness_id,
                        previous_harness_id=metadata.active_harness_id,
                        cycle=current.cycle,
                        score=score,
                        decision_id=accept_decision.decision_id,
                        control_transaction_id=transaction_id,
                        snapshot_manifest_sha256=(
                            bundle.manifest.manifest_sha256
                        ),
                        updated_at=decision.decided_at,
                    ),
                    expected_previous_harness_id=metadata.active_harness_id,
                )
                cleared = replace(
                    cleared,
                    active_harness_id=candidate.harness_id,
                    active_harness_score=score,
                )
            self._update_run_from_step(cleared, step, committed)
            return True

        if subject == "dataset":
            if current.state is not ControlState.DATA_REVIEW_PENDING:
                raise RuntimeError("Dataset approval resumed from wrong State")
            if decision.subject_type is not DecisionSubject.DATASET:
                raise RuntimeError("Dataset approval Decision subject mismatch")
            target_state = (
                ControlState.TRAIN_MODEL
                if decision.approved
                else ControlState.STOPPED
            )
            action = (
                DecisionAction.ACCEPT
                if decision.approved
                else DecisionAction.REJECT
            )
            stop_reason = (
                None if decision.approved else StopReason.APPROVAL_NOT_GRANTED
            )
            step = self._controller_edge(
                current,
                to_state=target_state,
                event=(
                    ControlEvent.DATA_APPROVED
                    if decision.approved
                    else ControlEvent.DATA_DENIED
                ),
                action=action,
                subject_type=DecisionSubject.DATASET,
                subject_id=decision.subject_id,
                reason_code=(
                    "trace_dataset_approval_granted"
                    if decision.approved
                    else "trace_dataset_approval_not_granted"
                ),
                reason=(
                    "Authorized review granted Trace Dataset training authority."
                    if decision.approved
                    else "Trace Dataset training authority was not granted."
                ),
                timestamp=decision.decided_at,
                evidence_ids=(approval_evidence.evidence_id,),
                decision_stop_reason=stop_reason,
                snapshot_stop_reason=stop_reason,
                metadata={
                    **dict(current.metadata),
                    "approval_request_id": request_id,
                    "reviewer_id": decision.reviewer_id,
                    "reviewer_role": decision.reviewer_role,
                },
            )
            committed = self._commit_step(
                step,
                evidence=(approval_evidence,),
                label="dataset-review-completed",
            )
            self._update_run_from_step(cleared, step, committed)
            return True

        if subject == "checkpoint":
            step = self._model_policy().review_completed(
                current,
                ModelReviewObservation(
                    request_id=request_id,
                    checkpoint_id=decision.subject_id,
                    approved=decision.approved,
                    reviewer_id=decision.reviewer_id,
                    reviewer_role=decision.reviewer_role,
                    decided_at=decision.decided_at,
                    evidence_ids=(approval_evidence.evidence_id,),
                ),
            )
            committed = self._commit_step(
                step,
                evidence=(approval_evidence,),
                label="model-review-completed",
            )
            self._update_run_from_step(cleared, step, committed)
            if step.final_snapshot.state is ControlState.PROMOTE_MODEL:
                candidate = self._candidate_from_snapshot(current)
                self._commit_model_promotion(
                    self.run_store.load(),
                    step.final_snapshot,
                    candidate,
                    float(step.final_snapshot.candidate_score or 0.0),
                    committed.decision_transactions[
                        step.decisions[-1].decision_id
                    ],
                    step.decisions[-1],
                )
            else:
                self._commit_model_rollback(
                    self.run_store.load(),
                    step.final_snapshot,
                    step.decisions[-1],
                    committed.decision_transactions[
                        step.decisions[-1].decision_id
                    ],
                )
            return True
        raise RuntimeError(f"unsupported pending approval subject: {subject}")

    def _candidate_from_snapshot(
        self,
        snapshot: StateSnapshot,
    ) -> Any:
        from ..harness.model_inner_loop import ModelCandidateArtifact

        checkpoint_id = snapshot.candidate_checkpoint_id
        if checkpoint_id is None:
            raise RuntimeError("model review Snapshot has no Candidate checkpoint")
        candidate_path = (
            self.workspace / "model-candidates" / f"{checkpoint_id}.json"
        )
        try:
            payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"cannot load immutable model Candidate {checkpoint_id}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise RuntimeError("model Candidate record must be a JSON object")
        candidate = ModelCandidateArtifact.from_dict(payload)
        if candidate.checkpoint_id != checkpoint_id:
            raise RuntimeError("model Candidate record identity mismatch")
        if candidate.run_id != snapshot.run_id or candidate.cycle != snapshot.cycle:
            raise RuntimeError("model Candidate record Run/cycle mismatch")
        if candidate.parent_checkpoint_id != snapshot.active_checkpoint_id:
            raise RuntimeError("model Candidate parent changed during approval pause")
        expected_artifact_sha = snapshot.metadata.get("artifact_sha256")
        if expected_artifact_sha != candidate.artifact_sha256:
            raise RuntimeError("model Candidate artifact SHA-256 changed during approval")
        expected_dataset_sha = snapshot.metadata.get("trace_dataset_sha256")
        if expected_dataset_sha != candidate.dataset_sha256:
            raise RuntimeError("model Candidate Dataset SHA-256 changed during approval")
        return candidate

    def _outer_policy(self) -> HarnessOuterPolicy:
        return HarnessOuterPolicy(
            HarnessOuterLimits(
                max_iterations=self.config.co_evolution.max_outer_iterations,
                plateau_patience=self.config.co_evolution.plateau_patience,
                min_improvement=(
                    self.config.co_evolution.harness_min_improvement
                ),
                per_iteration_budget_usd=min(
                    self.config.budget.per_iteration_limit_usd,
                    10.0,
                ),
                total_budget_usd=self.config.budget.total_limit_usd,
                approval_required=(
                    self.config.approval.harness_review_required
                ),
            )
        )

    def _trace_policy(self) -> TraceHarvestPolicy:
        return TraceHarvestPolicy(
            TraceHarvestLimits(
                target_traces=self.config.co_evolution.target_traces,
                max_batches=max(1, self.config.co_evolution.max_outer_iterations),
                min_acceptance_rate=self.config.verification.min_acceptance_rate,
                per_batch_budget_usd=min(
                    self.config.budget.per_iteration_limit_usd,
                    5.0,
                ),
                total_budget_usd=self.config.budget.total_limit_usd,
            )
        )

    def _model_policy(self) -> ModelInnerPolicy:
        return ModelInnerPolicy(
            ModelInnerLimits(
                min_improvement=self.config.co_evolution.model_min_improvement,
                regression_tolerance=self.config.rsi.regression_tolerance,
                per_stage_budget_usd=self.config.budget.per_iteration_limit_usd,
                total_budget_usd=self.config.budget.total_limit_usd,
                approval_required=(
                    self.config.approval.checkpoint_review_required
                ),
            )
        )

    def _bootstrap_harness_spec(self) -> HarnessSpec:
        payload: dict[str, JSONValue] = {
            "version": 1,
            "system_prompt": (
                "Use only observable evidence. Call tools when required, verify state "
                "boundaries, and report a concise final result."
            ),
            "tools": ["search", "calculator"],
            "retry_policy": RetryPolicy(max_attempts=2).to_dict(),
            "timeout_seconds": 60.0,
            "max_steps": 24,
            "metadata": {"source": "reference-bootstrap"},
        }
        harness_id = content_addressed_harness_id(
            prefix="harness-bootstrap",
            payload=payload,
        )
        return HarnessSpec(
            harness_id=harness_id,
            version=1,
            parent_harness_id=None,
            system_prompt=str(payload["system_prompt"]),
            tools=("search", "calculator"),
            retry_policy=RetryPolicy(max_attempts=2),
            timeout_seconds=60.0,
            max_steps=24,
            metadata={"source": "reference-bootstrap"},
        )

    def _commit_step(
        self,
        step: Any,
        *,
        evidence: tuple[EvidenceRecord, ...],
        label: str,
    ) -> _CommittedRecords:
        return self._commit_records(
            (
                *evidence,
                *step.decisions,
                *step.transitions,
                *step.snapshots,
            ),
            label=label,
        )

    def _commit_records(
        self,
        records: tuple[ControlRecord, ...],
        *,
        label: str,
    ) -> _CommittedRecords:
        grouped: dict[int, list[ControlRecord]] = {}
        for record in records:
            grouped.setdefault(record.iteration, []).append(record)
        snapshot_transactions: dict[str, str] = {}
        decision_transactions: dict[str, str] = {}
        final_transaction_id = ""
        for index, iteration in enumerate(sorted(grouped)):
            items = tuple(
                sorted(
                    grouped[iteration],
                    key=lambda item: (
                        item.RECORD_TYPE,
                        _control_record_id(item),
                    ),
                )
            )
            digest = hashlib.sha256(
                canonical_json(
                    [item.to_dict() for item in items]
                ).encode("utf-8")
            ).hexdigest()[:20]
            transaction_id = (
                f"tx-{label}-{iteration:06d}-{index:02d}-{digest}"
            )
            timestamp = max(_record_timestamp(item) for item in items)
            self.control_store.commit(
                transaction_id=transaction_id,
                run_id=self.run_id,
                iteration=iteration,
                created_at=timestamp,
                records=items,
            )
            for item in items:
                if isinstance(item, StateSnapshot):
                    snapshot_transactions[item.snapshot_id] = transaction_id
                elif isinstance(item, DecisionRecord):
                    decision_transactions[item.decision_id] = transaction_id
            final_transaction_id = transaction_id
        if not final_transaction_id:
            raise RuntimeError("cannot commit an empty control record set")
        return _CommittedRecords(
            final_transaction_id=final_transaction_id,
            snapshot_transactions=snapshot_transactions,
            decision_transactions=decision_transactions,
        )

    def _update_run_from_step(
        self,
        metadata: CoEvolutionRunMetadata,
        step: Any,
        committed: _CommittedRecords,
    ) -> None:
        snapshot = step.final_snapshot
        transaction_id = committed.snapshot_transactions[snapshot.snapshot_id]
        status = (
            "ABORTED"
            if snapshot.state is ControlState.ABORTED
            else (
                "STOPPED"
                if snapshot.state is ControlState.STOPPED
                else "RUNNING"
            )
        )
        self._update_run(
            metadata,
            snapshot=snapshot,
            transaction_id=transaction_id,
            status=status,
        )

    def _update_run(
        self,
        metadata: CoEvolutionRunMetadata,
        *,
        snapshot: StateSnapshot,
        transaction_id: str,
        status: str,
        pending_request_id: str | None = None,
        pending_request_sha256: str | None = None,
        pending_subject: str | None = None,
    ) -> None:
        model_score = metadata.active_model_score
        score_value = snapshot.metadata.get("active_model_score")
        if isinstance(score_value, (int, float)) and not isinstance(
            score_value,
            bool,
        ):
            model_score = float(score_value)
        harness_score = metadata.active_harness_score
        if snapshot.peak_score is not None:
            harness_score = float(snapshot.peak_score)
        next_metadata = CoEvolutionRunMetadata(
            run_id=metadata.run_id,
            config_sha256=metadata.config_sha256,
            revision=metadata.revision + 1,
            state=snapshot.state,
            current_cycle=max(metadata.current_cycle, snapshot.cycle),
            completed_cycles=metadata.completed_cycles,
            active_checkpoint_id=(
                snapshot.active_checkpoint_id or metadata.active_checkpoint_id
            ),
            active_model_score=model_score,
            active_harness_id=(
                snapshot.active_harness_id or metadata.active_harness_id
            ),
            active_harness_score=harness_score,
            latest_snapshot_id=snapshot.snapshot_id,
            latest_transaction_id=transaction_id,
            pending_approval_request_id=pending_request_id,
            pending_approval_request_sha256=pending_request_sha256,
            pending_approval_subject=pending_subject,
            status=status,
            created_at=metadata.created_at,
            updated_at=self.clock.at(
                cycle=max(1, snapshot.cycle),
                ordinal=9_900 + metadata.revision,
            ),
        )
        if metadata.completed_cycles != next_metadata.completed_cycles:
            next_metadata = replace(
                next_metadata,
                completed_cycles=metadata.completed_cycles,
            )
        self.run_store.compare_and_swap(
            next_metadata,
            expected_revision=metadata.revision,
        )

    def _controller_edge(
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
    ) -> Any:
        from ..harness.trace_harvesting.policy import TraceHarvestPolicyStep

        phase = to_state.value.lower()
        decision_id = _record_id(
            f"decision-{phase}",
            self.run_id,
            current.iteration,
            subject_id,
        )
        decision = DecisionRecord(
            decision_id=decision_id,
            run_id=self.run_id,
            iteration=current.iteration,
            subject_type=subject_type,
            subject_id=subject_id,
            action=action,
            reason_code=reason_code,
            reason=reason,
            evidence_ids=evidence_ids,
            created_at=timestamp,
            stop_reason=decision_stop_reason,
            metadata=metadata,
        )
        transition = TransitionRecord(
            transition_id=_record_id(
                f"transition-{phase}",
                self.run_id,
                current.iteration,
                subject_id,
            ),
            run_id=self.run_id,
            iteration=current.iteration,
            from_state=current.state,
            event=event,
            to_state=to_state,
            occurred_at=timestamp,
            idempotency_key=_record_id(
                f"idempotency-{phase}",
                self.run_id,
                current.iteration,
                subject_id,
            ),
            decision_id=decision_id,
            evidence_ids=evidence_ids,
        )
        snapshot = replace(
            current,
            snapshot_id=_record_id(
                f"snapshot-{phase}",
                self.run_id,
                current.iteration,
                subject_id,
            ),
            state=to_state,
            entered_at=timestamp,
            stop_reason=snapshot_stop_reason,
            evidence_ids=evidence_ids,
            metadata={**metadata, "decision_id": decision_id},
        )
        return TraceHarvestPolicyStep(
            decisions=(decision,),
            transitions=(transition,),
            snapshots=(snapshot,),
        )

    def _result(
        self,
        metadata: CoEvolutionRunMetadata,
        snapshot: StateSnapshot,
    ) -> CoEvolutionRunResult:
        report = {
            "run_id": self.run_id,
            "status": metadata.status,
            "state": snapshot.state.value,
            "current_cycle": metadata.current_cycle,
            "completed_cycles": metadata.completed_cycles,
            "active_checkpoint_id": metadata.active_checkpoint_id,
            "active_model_score": metadata.active_model_score,
            "active_harness_id": metadata.active_harness_id,
            "active_harness_score": metadata.active_harness_score,
            "total_cost_usd": snapshot.total_cost_usd,
            "pending_approval_request_id": (
                metadata.pending_approval_request_id
            ),
            "latest_snapshot_id": metadata.latest_snapshot_id,
            "latest_transaction_id": metadata.latest_transaction_id,
            "config_sha256": metadata.config_sha256,
        }
        path = self.artifacts.write_report(
            "coevolution-run-summary.json",
            report,
        )
        return CoEvolutionRunResult(
            run_id=self.run_id,
            status=metadata.status,
            state=snapshot.state.value,
            current_cycle=metadata.current_cycle,
            completed_cycles=metadata.completed_cycles,
            active_checkpoint_id=metadata.active_checkpoint_id,
            active_model_score=metadata.active_model_score,
            active_harness_id=metadata.active_harness_id,
            active_harness_score=metadata.active_harness_score,
            total_cost_usd=snapshot.total_cost_usd,
            pending_approval_request_id=(
                metadata.pending_approval_request_id
            ),
            report_path=path.as_posix(),
        )


def build_reference_coevolution_controller(
    config: PipelineConfig,
    *,
    workspace: str | Path,
    run_id: str,
) -> CoEvolutionController:
    return CoEvolutionController(
        config,
        workspace=workspace,
        run_id=run_id,
    )


def _record_id(prefix: str, run_id: str, iteration: int, subject: str) -> str:
    digest = hashlib.sha256(
        f"{prefix}|{run_id}|{iteration}|{subject}".encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest[:24]}"


def _control_record_id(record: ControlRecord) -> str:
    if isinstance(record, EvidenceRecord):
        return record.evidence_id
    if isinstance(record, DecisionRecord):
        return record.decision_id
    if isinstance(record, TransitionRecord):
        return record.transition_id
    return record.snapshot_id


def _record_timestamp(record: ControlRecord) -> str:
    if isinstance(record, EvidenceRecord):
        return record.created_at
    if isinstance(record, DecisionRecord):
        return record.created_at
    if isinstance(record, TransitionRecord):
        return record.occurred_at
    return record.entered_at


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_immutable_or_equal(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f"immutable path is a symlink: {path}")
    if path.exists():
        if path.read_bytes() != content:
            raise RuntimeError(
                f"immutable path already has different bytes: {path}"
            )
        return
    path.write_bytes(content)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata_str(snapshot: StateSnapshot, key: str) -> str:
    value = snapshot.metadata.get(key)
    if not isinstance(value, str):
        raise RuntimeError(f"Snapshot metadata.{key} must be a string")
    return value


def _metadata_int(snapshot: StateSnapshot, key: str, default: int) -> int:
    value = snapshot.metadata.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"Snapshot metadata.{key} must be an integer")
    return value
