from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from post_training_rsi.control_plane import (
    ControlEvent,
    ControlState,
    DecisionAction,
    DecisionRecord,
    DecisionSubject,
    EvidenceKind,
    EvidenceRecord,
    StateSnapshot,
    TransitionRecord,
)
from post_training_rsi.lineage import (
    CheckpointBundleStore,
    ControlRecordStore,
    LineageConflictError,
    LineageManifest,
    PeakPointer,
    PeakPointerStore,
)

NOW = "2026-08-14T04:00:00Z"


def committed_peak(tmp_path: Path) -> tuple[PeakPointerStore, PeakPointer]:
    evidence = EvidenceRecord(
        evidence_id="ev-eval-peak",
        run_id="run-peak",
        iteration=1,
        kind=EvidenceKind.EVALUATION_RESULT,
        producer="evaluation.fixture",
        uri="artifact://evaluation",
        created_at=NOW,
        sha256="a" * 64,
    )
    decision = DecisionRecord(
        decision_id="decision-promote-peak",
        run_id="run-peak",
        iteration=1,
        subject_type=DecisionSubject.CHECKPOINT,
        subject_id="ckpt-peak-1",
        action=DecisionAction.PROMOTE,
        reason_code="candidate_above_peak",
        reason="Fixture promotion.",
        evidence_ids=(evidence.evidence_id,),
        created_at=NOW,
    )
    transition = TransitionRecord(
        transition_id="transition-promote-peak",
        run_id="run-peak",
        iteration=1,
        from_state=ControlState.EVALUATE,
        event=ControlEvent.CANDIDATE_IMPROVED,
        to_state=ControlState.PROMOTED,
        occurred_at=NOW,
        idempotency_key="idem-promote-peak",
        decision_id=decision.decision_id,
        evidence_ids=(evidence.evidence_id,),
    )
    snapshot = StateSnapshot(
        snapshot_id="snapshot-promote-peak",
        run_id="run-peak",
        iteration=1,
        cycle=0,
        state=ControlState.PROMOTED,
        entered_at=NOW,
        active_checkpoint_id="ckpt-peak-1",
        candidate_checkpoint_id="ckpt-peak-1",
        peak_checkpoint_id="ckpt-peak-1",
        candidate_score=0.72,
        peak_score=0.72,
        evidence_ids=(evidence.evidence_id,),
        metadata={"decision_id": decision.decision_id},
    )

    control_store = ControlRecordStore(tmp_path)
    transaction = control_store.commit(
        transaction_id="tx-promote-peak",
        run_id="run-peak",
        iteration=1,
        created_at=NOW,
        records=(evidence, decision, transition, snapshot),
    )
    artifact = tmp_path / "artifacts/ckpt-peak-1.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"fixture weights")
    checkpoint_store = CheckpointBundleStore(tmp_path, control_store)
    bundle = checkpoint_store.commit(
        checkpoint_id="ckpt-peak-1",
        run_id="run-peak",
        iteration=1,
        checkpoint_payload={
            "checkpoint_id": "ckpt-peak-1",
            "model_id": "student-model",
        },
        lineage_manifest=LineageManifest(
            checkpoint_id="ckpt-peak-1",
            parent_checkpoint_id="ckpt-base",
            dataset_commit_hash="d" * 64,
            dataset_path="artifacts/accepted.jsonl",
            teacher_api_version="teacher-api-v1",
            teacher_model="teacher-model",
            teacher_prompt_hash="prompt-v1",
            filter_config_version="filter-v1",
            rejected_data_count=0,
            training_loss_final=0.2,
            benchmark_score=0.72,
            model_id="student-model",
            code_git_commit="git-fixture",
            iteration=1,
            status="PROMOTED",
            created_at=NOW,
        ),
        artifact_path=artifact,
        artifact_uri="artifact://ckpt-peak-1",
        control_transaction_id=transaction.transaction_id,
        created_at=NOW,
    )
    peak_store = PeakPointerStore(tmp_path, control_store, checkpoint_store)
    pointer = PeakPointer(
        run_id="run-peak",
        checkpoint_id="ckpt-peak-1",
        previous_checkpoint_id=None,
        model_id="student-model",
        iteration=1,
        score=0.72,
        decision_id=decision.decision_id,
        control_transaction_id=transaction.transaction_id,
        checkpoint_bundle_sha256=bundle.manifest.manifest_sha256,
        updated_at=NOW,
    )
    peak_store.compare_and_swap(pointer, expected_previous_checkpoint_id=None)
    return peak_store, pointer


def test_peak_score_must_increase_strictly(tmp_path: Path) -> None:
    store, current = committed_peak(tmp_path)
    candidate = replace(
        current,
        checkpoint_id="ckpt-lower-score",
        previous_checkpoint_id=current.checkpoint_id,
        iteration=2,
        score=current.score,
        updated_at="2026-08-14T04:01:00Z",
    )

    with pytest.raises(LineageConflictError, match="score must increase strictly"):
        store.compare_and_swap(
            candidate,
            expected_previous_checkpoint_id=current.checkpoint_id,
        )


def test_peak_iteration_cannot_move_backwards(tmp_path: Path) -> None:
    store, current = committed_peak(tmp_path)
    candidate = replace(
        current,
        checkpoint_id="ckpt-old-iteration",
        previous_checkpoint_id=current.checkpoint_id,
        iteration=0,
        score=current.score + 0.1,
        updated_at="2026-08-14T04:01:00Z",
    )

    with pytest.raises(LineageConflictError, match="iteration cannot move backwards"):
        store.compare_and_swap(
            candidate,
            expected_previous_checkpoint_id=current.checkpoint_id,
        )
