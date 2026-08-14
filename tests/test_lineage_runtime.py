from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from post_training_rsi.control_plane import (
    ControlContractError,
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
from post_training_rsi.lineage import (
    CheckpointBundle,
    CheckpointBundleStore,
    ControlRecordStore,
    LineageConflictError,
    LineageIntegrityError,
    LineageLockTimeout,
    LineageManifest,
    PeakPointer,
    PeakPointerStore,
    QuarantineMarker,
    QuarantineStore,
)

NOW = "2026-08-14T03:00:00Z"
SHA256_A = "a" * 64


def promotion_records(
    *,
    run_id: str = "run-001",
    iteration: int = 1,
    checkpoint_id: str = "ckpt-001",
    action: DecisionAction = DecisionAction.PROMOTE,
) -> tuple[EvidenceRecord, DecisionRecord, TransitionRecord, StateSnapshot]:
    evidence = EvidenceRecord(
        evidence_id=f"ev-eval-{checkpoint_id}",
        run_id=run_id,
        iteration=iteration,
        kind=EvidenceKind.EVALUATION_RESULT,
        producer="evaluation.fixture",
        uri=f"artifacts/{checkpoint_id}/evaluation.json",
        created_at=NOW,
        sha256=SHA256_A,
        metadata={"score": 0.72},
    )
    decision = DecisionRecord(
        decision_id=f"decision-{checkpoint_id}",
        run_id=run_id,
        iteration=iteration,
        subject_type=DecisionSubject.CHECKPOINT,
        subject_id=checkpoint_id,
        action=action,
        reason_code=(
            "candidate_above_peak"
            if action is DecisionAction.PROMOTE
            else "candidate_not_above_peak"
        ),
        reason="Fixture Candidate decision.",
        evidence_ids=(evidence.evidence_id,),
        created_at=NOW,
    )
    target_state = (
        ControlState.PROMOTED
        if action is DecisionAction.PROMOTE
        else ControlState.REJECTED
    )
    event = (
        ControlEvent.CANDIDATE_IMPROVED
        if action is DecisionAction.PROMOTE
        else ControlEvent.CANDIDATE_NOT_IMPROVED
    )
    transition = TransitionRecord(
        transition_id=f"transition-{checkpoint_id}",
        run_id=run_id,
        iteration=iteration,
        from_state=ControlState.EVALUATE,
        event=event,
        to_state=target_state,
        occurred_at=NOW,
        idempotency_key=f"idem-{checkpoint_id}",
        decision_id=decision.decision_id,
        evidence_ids=(evidence.evidence_id,),
    )
    snapshot = StateSnapshot(
        snapshot_id=f"snapshot-{checkpoint_id}",
        run_id=run_id,
        iteration=iteration,
        cycle=0,
        state=target_state,
        entered_at=NOW,
        active_checkpoint_id=(
            checkpoint_id if action is DecisionAction.PROMOTE else "ckpt-old"
        ),
        candidate_checkpoint_id=checkpoint_id,
        peak_checkpoint_id=(
            checkpoint_id if action is DecisionAction.PROMOTE else "ckpt-old"
        ),
        candidate_score=0.72,
        peak_score=(0.72 if action is DecisionAction.PROMOTE else 0.70),
        plateau_count=(0 if action is DecisionAction.PROMOTE else 1),
        total_cost_usd=5.0,
        evidence_ids=(evidence.evidence_id,),
        metadata={"decision_id": decision.decision_id},
    )
    return evidence, decision, transition, snapshot


def quarantine_records(
    *,
    run_id: str = "run-001",
    iteration: int = 2,
    dataset_id: str = "dataset-002",
) -> tuple[EvidenceRecord, DecisionRecord, TransitionRecord, StateSnapshot]:
    evidence = EvidenceRecord(
        evidence_id=f"ev-filter-{dataset_id}",
        run_id=run_id,
        iteration=iteration,
        kind=EvidenceKind.VERIFICATION_AUDIT,
        producer="verification.fixture",
        uri=f"artifacts/{dataset_id}/filter_audit.jsonl",
        created_at=NOW,
        sha256=SHA256_A,
    )
    decision = DecisionRecord(
        decision_id=f"decision-{dataset_id}",
        run_id=run_id,
        iteration=iteration,
        subject_type=DecisionSubject.DATASET,
        subject_id=dataset_id,
        action=DecisionAction.QUARANTINE,
        reason_code="low_diversity",
        reason="Dataset diversity fell below the required floor.",
        evidence_ids=(evidence.evidence_id,),
        created_at=NOW,
        stop_reason=StopReason.LOW_DIVERSITY,
    )
    transition = TransitionRecord(
        transition_id=f"transition-{dataset_id}",
        run_id=run_id,
        iteration=iteration,
        from_state=ControlState.VERIFY,
        event=ControlEvent.DATASET_QUARANTINED,
        to_state=ControlState.QUARANTINED,
        occurred_at=NOW,
        idempotency_key=f"idem-{dataset_id}",
        decision_id=decision.decision_id,
        evidence_ids=(evidence.evidence_id,),
    )
    snapshot = StateSnapshot(
        snapshot_id=f"snapshot-{dataset_id}",
        run_id=run_id,
        iteration=iteration,
        cycle=0,
        state=ControlState.QUARANTINED,
        entered_at=NOW,
        active_checkpoint_id="ckpt-old",
        peak_checkpoint_id="ckpt-old",
        peak_score=0.70,
        evidence_ids=(evidence.evidence_id,),
        metadata={"decision_id": decision.decision_id},
    )
    return evidence, decision, transition, snapshot


def commit_promotion_transaction(
    root: Path,
    *,
    checkpoint_id: str = "ckpt-001",
    action: DecisionAction = DecisionAction.PROMOTE,
) -> tuple[
    ControlRecordStore,
    str,
    tuple[EvidenceRecord, DecisionRecord, TransitionRecord, StateSnapshot],
]:
    store = ControlRecordStore(root)
    records = promotion_records(checkpoint_id=checkpoint_id, action=action)
    transaction_id = f"tx-{checkpoint_id}"
    store.commit(
        transaction_id=transaction_id,
        run_id="run-001",
        iteration=1,
        created_at=NOW,
        records=records,
    )
    return store, transaction_id, records


def make_lineage_manifest(
    *,
    checkpoint_id: str = "ckpt-001",
    status: str = "PROMOTED",
    score: float = 0.72,
) -> LineageManifest:
    return LineageManifest(
        checkpoint_id=checkpoint_id,
        parent_checkpoint_id="ckpt-old",
        dataset_commit_hash="d" * 64,
        dataset_path="artifacts/iterations/iter-001/accepted.jsonl",
        teacher_api_version="teacher-api-v1",
        teacher_model="teacher-model",
        teacher_prompt_hash="prompt-hash-v1",
        filter_config_version="filter-v1",
        rejected_data_count=3,
        training_loss_final=0.18,
        benchmark_score=score,
        model_id="student-model",
        code_git_commit="git-commit-fixture",
        iteration=1,
        status=status,
        created_at=NOW,
    )


def commit_checkpoint_bundle(
    root: Path,
    *,
    checkpoint_id: str = "ckpt-001",
    action: DecisionAction = DecisionAction.PROMOTE,
    status: str = "PROMOTED",
) -> tuple[
    ControlRecordStore,
    CheckpointBundleStore,
    Path,
    CheckpointBundle,
    DecisionRecord,
]:
    control_store, transaction_id, records = commit_promotion_transaction(
        root,
        checkpoint_id=checkpoint_id,
        action=action,
    )
    artifact = root / "model-artifacts" / f"{checkpoint_id}.bin"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(f"weights:{checkpoint_id}".encode())
    checkpoint_store = CheckpointBundleStore(root, control_store)
    bundle = checkpoint_store.commit(
        checkpoint_id=checkpoint_id,
        run_id="run-001",
        iteration=1,
        checkpoint_payload={
            "checkpoint_id": checkpoint_id,
            "model_id": "student-model",
            "final_loss": 0.18,
        },
        lineage_manifest=make_lineage_manifest(
            checkpoint_id=checkpoint_id,
            status=status,
        ),
        artifact_path=artifact,
        artifact_uri=f"artifact://{checkpoint_id}",
        control_transaction_id=transaction_id,
        created_at=NOW,
    )
    return control_store, checkpoint_store, artifact, bundle, records[1]


def test_control_transaction_round_trip_and_idempotent_retry(tmp_path: Path) -> None:
    store, transaction_id, records = commit_promotion_transaction(tmp_path)

    first = store.load_transaction(transaction_id)
    second = store.commit(
        transaction_id=transaction_id,
        run_id="run-001",
        iteration=1,
        created_at=NOW,
        records=records,
    )

    assert second == first
    assert [ref.record_type for ref in first.records] == sorted(
        ref.record_type for ref in first.records
    )
    assert store.load_evidence(records[0].evidence_id) == records[0]
    assert store.load_decision(records[1].decision_id) == records[1]
    assert store.load_transition(records[2].transition_id) == records[2]
    assert store.load_snapshot(records[3].snapshot_id) == records[3]
    assert store.is_committed(EvidenceRecord.RECORD_TYPE, records[0].evidence_id)


def test_control_transaction_rejects_uncommitted_dependencies(tmp_path: Path) -> None:
    store = ControlRecordStore(tmp_path)
    _, decision, transition, snapshot = promotion_records()

    with pytest.raises(LineageIntegrityError, match="uncommitted evidence"):
        store.commit(
            transaction_id="tx-missing-evidence",
            run_id="run-001",
            iteration=1,
            created_at=NOW,
            records=(decision,),
        )
    assert not (tmp_path / "control/transactions/tx-missing-evidence.json").exists()

    with pytest.raises(LineageIntegrityError, match="uncommitted decision"):
        store.commit(
            transaction_id="tx-missing-decision",
            run_id="run-001",
            iteration=1,
            created_at=NOW,
            records=(promotion_records()[0], transition),
        )

    bad_snapshot = replace(snapshot, metadata={"decision_id": 17})
    with pytest.raises(LineageIntegrityError, match="must be a string"):
        store.commit(
            transaction_id="tx-bad-snapshot-reference",
            run_id="run-001",
            iteration=1,
            created_at=NOW,
            records=(promotion_records()[0], bad_snapshot),
        )


def test_control_transaction_conflict_and_tamper_detection(tmp_path: Path) -> None:
    store, _, records = commit_promotion_transaction(tmp_path)
    original = records[0]
    conflicting = replace(original, uri="artifact://different")

    with pytest.raises(LineageConflictError, match="immutable content conflict"):
        store.commit(
            transaction_id="tx-conflict",
            run_id="run-001",
            iteration=1,
            created_at=NOW,
            records=(conflicting,),
        )
    assert not (tmp_path / "control/transactions/tx-conflict.json").exists()

    evidence_path = tmp_path / "control/evidence" / f"{original.evidence_id}.json"
    evidence_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(LineageIntegrityError, match="hash mismatch"):
        store.load_transaction("tx-ckpt-001")


def test_orphan_record_is_not_committed(tmp_path: Path) -> None:
    store = ControlRecordStore(tmp_path)
    evidence = promotion_records()[0]
    path = tmp_path / "control/evidence" / f"{evidence.evidence_id}.json"
    path.write_text(evidence.to_json() + "\n", encoding="utf-8")

    with pytest.raises(LineageIntegrityError, match="not part of a committed"):
        store.load_evidence(evidence.evidence_id)
    assert (
        store.load_record(
            EvidenceRecord.RECORD_TYPE,
            evidence.evidence_id,
            require_committed=False,
        )
        == evidence
    )


def test_control_store_lock_times_out_fail_closed(tmp_path: Path) -> None:
    store = ControlRecordStore(
        tmp_path,
        lock_timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )
    store.lock_path.write_text("held\n", encoding="utf-8")
    with pytest.raises(LineageLockTimeout, match="timed out"):
        store.commit(
            transaction_id="tx-locked",
            run_id="run-001",
            iteration=1,
            created_at=NOW,
            records=(promotion_records()[0],),
        )
    assert not (tmp_path / "control/transactions/tx-locked.json").exists()


def test_checkpoint_bundle_round_trip_and_idempotent_retry(tmp_path: Path) -> None:
    control_store, checkpoint_store, artifact, bundle, _ = commit_checkpoint_bundle(
        tmp_path
    )
    transaction_id = bundle.manifest.control_transaction_id
    manifest = bundle.lineage_manifest
    payload = bundle.checkpoint_payload

    second = checkpoint_store.commit(
        checkpoint_id="ckpt-001",
        run_id="run-001",
        iteration=1,
        checkpoint_payload=payload,
        lineage_manifest=manifest,
        artifact_path=artifact,
        artifact_uri="artifact://ckpt-001",
        control_transaction_id=transaction_id,
        created_at=NOW,
    )

    assert second == bundle
    assert second.manifest.artifact_sha256
    assert second.control_transaction == control_store.load_transaction(transaction_id)
    assert (tmp_path / "checkpoints/ckpt-001/checkpoint.json").exists()
    assert (tmp_path / "checkpoints/ckpt-001/lineage_manifest.json").exists()
    assert (tmp_path / "checkpoints/ckpt-001/bundle_manifest.json").exists()


def test_checkpoint_bundle_rejects_conflicts_and_bad_links(tmp_path: Path) -> None:
    _, checkpoint_store, artifact, bundle, _ = commit_checkpoint_bundle(tmp_path)

    with pytest.raises(LineageConflictError, match="different content"):
        checkpoint_store.commit(
            checkpoint_id="ckpt-001",
            run_id="run-001",
            iteration=1,
            checkpoint_payload={
                "checkpoint_id": "ckpt-001",
                "model_id": "student-model",
                "final_loss": 9.99,
            },
            lineage_manifest=bundle.lineage_manifest,
            artifact_path=artifact,
            artifact_uri="artifact://ckpt-001",
            control_transaction_id=bundle.manifest.control_transaction_id,
            created_at=NOW,
        )

    with pytest.raises(ControlContractError, match="checkpoint_id"):
        checkpoint_store.commit(
            checkpoint_id="ckpt-new",
            run_id="run-001",
            iteration=1,
            checkpoint_payload={"checkpoint_id": "ckpt-wrong"},
            lineage_manifest=replace(
                bundle.lineage_manifest,
                checkpoint_id="ckpt-new",
            ),
            artifact_path=artifact,
            artifact_uri="artifact://ckpt-new",
            control_transaction_id=bundle.manifest.control_transaction_id,
            created_at=NOW,
        )

    with pytest.raises(LineageIntegrityError, match="artifact hash"):
        checkpoint_store.commit(
            checkpoint_id="ckpt-hash-mismatch",
            run_id="run-001",
            iteration=1,
            checkpoint_payload={
                "checkpoint_id": "ckpt-hash-mismatch",
                "artifact_sha256": "0" * 64,
            },
            lineage_manifest=replace(
                bundle.lineage_manifest,
                checkpoint_id="ckpt-hash-mismatch",
            ),
            artifact_path=artifact,
            artifact_uri="artifact://ckpt-hash-mismatch",
            control_transaction_id=bundle.manifest.control_transaction_id,
            created_at=NOW,
        )


def test_checkpoint_bundle_detects_artifact_and_metadata_tampering(
    tmp_path: Path,
) -> None:
    _, checkpoint_store, artifact, _, _ = commit_checkpoint_bundle(tmp_path)

    artifact.write_bytes(b"tampered weights")
    with pytest.raises(LineageIntegrityError, match="artifact bytes"):
        checkpoint_store.load("ckpt-001", artifact_path=artifact)

    checkpoint_path = tmp_path / "checkpoints/ckpt-001/checkpoint.json"
    checkpoint_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(LineageIntegrityError, match="hash mismatch"):
        checkpoint_store.load("ckpt-001")


def test_checkpoint_bundle_rejects_self_parent_and_unknown_transaction(
    tmp_path: Path,
) -> None:
    control_store, transaction_id, _ = commit_promotion_transaction(tmp_path)
    checkpoint_store = CheckpointBundleStore(tmp_path, control_store)
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"weights")

    with pytest.raises(LineageIntegrityError, match="own lineage parent"):
        checkpoint_store.commit(
            checkpoint_id="ckpt-001",
            run_id="run-001",
            iteration=1,
            checkpoint_payload={"checkpoint_id": "ckpt-001"},
            lineage_manifest=replace(
                make_lineage_manifest(),
                parent_checkpoint_id="ckpt-001",
            ),
            artifact_path=artifact,
            artifact_uri="artifact://ckpt-001",
            control_transaction_id=transaction_id,
            created_at=NOW,
        )

    with pytest.raises(LineageIntegrityError, match="unknown control transaction"):
        checkpoint_store.commit(
            checkpoint_id="ckpt-unknown-tx",
            run_id="run-001",
            iteration=1,
            checkpoint_payload={"checkpoint_id": "ckpt-unknown-tx"},
            lineage_manifest=replace(
                make_lineage_manifest(),
                checkpoint_id="ckpt-unknown-tx",
            ),
            artifact_path=artifact,
            artifact_uri="artifact://ckpt-unknown-tx",
            control_transaction_id="tx-does-not-exist",
            created_at=NOW,
        )


def test_peak_pointer_compare_and_swap_and_idempotent_retry(tmp_path: Path) -> None:
    control_store, checkpoint_store, _, bundle, decision = commit_checkpoint_bundle(
        tmp_path
    )
    peak_store = PeakPointerStore(tmp_path, control_store, checkpoint_store)
    pointer = PeakPointer(
        run_id="run-001",
        checkpoint_id="ckpt-001",
        previous_checkpoint_id=None,
        model_id="student-model",
        iteration=1,
        score=0.72,
        decision_id=decision.decision_id,
        control_transaction_id=bundle.manifest.control_transaction_id,
        checkpoint_bundle_sha256=bundle.manifest.manifest_sha256,
        updated_at=NOW,
    )

    first = peak_store.compare_and_swap(
        pointer,
        expected_previous_checkpoint_id=None,
    )
    retry = peak_store.compare_and_swap(
        pointer,
        expected_previous_checkpoint_id=None,
    )

    assert first == pointer
    assert retry == pointer
    assert peak_store.load() == pointer
    assert (tmp_path / "peak_history/iter-000001-ckpt-001.json").exists()

    stale = replace(
        pointer,
        checkpoint_id="ckpt-next",
        previous_checkpoint_id="ckpt-stale",
        updated_at="2026-08-14T03:01:00Z",
    )
    with pytest.raises(LineageConflictError, match="compare-and-swap failed"):
        peak_store.compare_and_swap(
            stale,
            expected_previous_checkpoint_id="ckpt-stale",
        )


def test_peak_pointer_requires_promote_decision_and_valid_bundle_hash(
    tmp_path: Path,
) -> None:
    control_store, checkpoint_store, _, bundle, decision = commit_checkpoint_bundle(
        tmp_path,
        checkpoint_id="ckpt-rejected",
        action=DecisionAction.REJECT,
        status="REJECTED",
    )
    peak_store = PeakPointerStore(tmp_path, control_store, checkpoint_store)
    rejected_pointer = PeakPointer(
        run_id="run-001",
        checkpoint_id="ckpt-rejected",
        previous_checkpoint_id=None,
        model_id="student-model",
        iteration=1,
        score=0.72,
        decision_id=decision.decision_id,
        control_transaction_id=bundle.manifest.control_transaction_id,
        checkpoint_bundle_sha256=bundle.manifest.manifest_sha256,
        updated_at=NOW,
    )
    with pytest.raises(LineageIntegrityError, match="PROMOTE"):
        peak_store.compare_and_swap(
            rejected_pointer,
            expected_previous_checkpoint_id=None,
        )

    other_root = tmp_path / "other"
    other_control, other_checkpoints, _, good_bundle, good_decision = (
        commit_checkpoint_bundle(other_root)
    )
    other_peak = PeakPointerStore(other_root, other_control, other_checkpoints)
    invalid_hash_pointer = PeakPointer(
        run_id="run-001",
        checkpoint_id="ckpt-001",
        previous_checkpoint_id=None,
        model_id="student-model",
        iteration=1,
        score=0.72,
        decision_id=good_decision.decision_id,
        control_transaction_id=good_bundle.manifest.control_transaction_id,
        checkpoint_bundle_sha256="0" * 64,
        updated_at=NOW,
    )
    with pytest.raises(LineageIntegrityError, match="bundle hash"):
        other_peak.compare_and_swap(
            invalid_hash_pointer,
            expected_previous_checkpoint_id=None,
        )


def test_peak_pointer_detects_tampered_score(tmp_path: Path) -> None:
    control_store, checkpoint_store, _, bundle, decision = commit_checkpoint_bundle(
        tmp_path
    )
    peak_store = PeakPointerStore(tmp_path, control_store, checkpoint_store)
    pointer = PeakPointer(
        run_id="run-001",
        checkpoint_id="ckpt-001",
        previous_checkpoint_id=None,
        model_id="student-model",
        iteration=1,
        score=0.72,
        decision_id=decision.decision_id,
        control_transaction_id=bundle.manifest.control_transaction_id,
        checkpoint_bundle_sha256=bundle.manifest.manifest_sha256,
        updated_at=NOW,
    )
    peak_store.compare_and_swap(pointer, expected_previous_checkpoint_id=None)
    payload = pointer.to_dict()
    payload["score"] = 0.99
    (tmp_path / "peak_checkpoint.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    with pytest.raises(LineageIntegrityError, match="score differs"):
        peak_store.load()


def test_quarantine_marker_round_trip_idempotency_and_conflict(tmp_path: Path) -> None:
    control_store = ControlRecordStore(tmp_path)
    records = quarantine_records()
    control_store.commit(
        transaction_id="tx-quarantine-002",
        run_id="run-001",
        iteration=2,
        created_at=NOW,
        records=records,
    )
    store = QuarantineStore(tmp_path, control_store)
    marker = QuarantineMarker(
        run_id="run-001",
        iteration=2,
        subject_type=DecisionSubject.DATASET,
        subject_id="dataset-002",
        decision_id=records[1].decision_id,
        control_transaction_id="tx-quarantine-002",
        reason_code="low_diversity",
        reason="Dataset diversity fell below the required floor.",
        evidence_ids=(records[0].evidence_id,),
        created_at=NOW,
    )

    assert store.commit(marker) == marker
    assert store.commit(marker) == marker
    assert (
        store.load(
            iteration=2,
            subject_type=DecisionSubject.DATASET,
            subject_id="dataset-002",
        )
        == marker
    )

    with pytest.raises(LineageConflictError, match="immutable content conflict"):
        store.commit(replace(marker, reason="A conflicting explanation."))


def test_quarantine_marker_rejects_wrong_decision_action(tmp_path: Path) -> None:
    control_store, transaction_id, records = commit_promotion_transaction(tmp_path)
    store = QuarantineStore(tmp_path, control_store)
    marker = QuarantineMarker(
        run_id="run-001",
        iteration=1,
        subject_type=DecisionSubject.CHECKPOINT,
        subject_id="ckpt-001",
        decision_id=records[1].decision_id,
        control_transaction_id=transaction_id,
        reason_code=records[1].reason_code,
        reason="Should not be allowed.",
        evidence_ids=(records[0].evidence_id,),
        created_at=NOW,
    )
    with pytest.raises(LineageIntegrityError, match="requires QUARANTINE"):
        store.commit(marker)
