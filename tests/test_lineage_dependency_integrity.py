from __future__ import annotations

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
    TransitionRecord,
)
from post_training_rsi.lineage import (
    ControlRecordStore,
    LineageIntegrityError,
    StoredRecordRef,
)

NOW = "2026-08-14T05:00:00Z"


def evidence(*, run_id: str, iteration: int, evidence_id: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        run_id=run_id,
        iteration=iteration,
        kind=EvidenceKind.EVALUATION_RESULT,
        producer="evaluation.fixture",
        uri=f"artifact://{evidence_id}",
        created_at=NOW,
        sha256="a" * 64,
    )


def decision(
    *,
    run_id: str,
    iteration: int,
    decision_id: str,
    evidence_id: str,
) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        run_id=run_id,
        iteration=iteration,
        subject_type=DecisionSubject.CHECKPOINT,
        subject_id=f"ckpt-{run_id}-{iteration}",
        action=DecisionAction.REJECT,
        reason_code="candidate_not_above_peak",
        reason="Fixture rejection.",
        evidence_ids=(evidence_id,),
        created_at=NOW,
    )


def test_dependency_cannot_cross_runs(tmp_path: Path) -> None:
    store = ControlRecordStore(tmp_path)
    source = evidence(run_id="run-a", iteration=1, evidence_id="ev-cross-run")
    store.commit(
        transaction_id="tx-source-run-a",
        run_id="run-a",
        iteration=1,
        created_at=NOW,
        records=(source,),
    )
    cross_run_decision = decision(
        run_id="run-b",
        iteration=1,
        decision_id="decision-run-b",
        evidence_id=source.evidence_id,
    )

    with pytest.raises(LineageIntegrityError, match="another Run"):
        store.commit(
            transaction_id="tx-cross-run",
            run_id="run-b",
            iteration=1,
            created_at=NOW,
            records=(cross_run_decision,),
        )


def test_dependency_cannot_reference_future_evidence(tmp_path: Path) -> None:
    store = ControlRecordStore(tmp_path)
    future = evidence(run_id="run-a", iteration=2, evidence_id="ev-future")
    store.commit(
        transaction_id="tx-future-evidence",
        run_id="run-a",
        iteration=2,
        created_at=NOW,
        records=(future,),
    )
    earlier_decision = decision(
        run_id="run-a",
        iteration=1,
        decision_id="decision-earlier",
        evidence_id=future.evidence_id,
    )

    with pytest.raises(LineageIntegrityError, match="future Evidence"):
        store.commit(
            transaction_id="tx-earlier-decision",
            run_id="run-a",
            iteration=1,
            created_at=NOW,
            records=(earlier_decision,),
        )


def test_transition_and_decision_iterations_must_match(tmp_path: Path) -> None:
    store = ControlRecordStore(tmp_path)
    source = evidence(run_id="run-a", iteration=0, evidence_id="ev-decision-zero")
    source_decision = decision(
        run_id="run-a",
        iteration=0,
        decision_id="decision-zero",
        evidence_id=source.evidence_id,
    )
    store.commit(
        transaction_id="tx-decision-zero",
        run_id="run-a",
        iteration=0,
        created_at=NOW,
        records=(source, source_decision),
    )
    transition = TransitionRecord(
        transition_id="transition-one",
        run_id="run-a",
        iteration=1,
        from_state=ControlState.EVALUATE,
        event=ControlEvent.CANDIDATE_NOT_IMPROVED,
        to_state=ControlState.REJECTED,
        occurred_at=NOW,
        idempotency_key="idem-transition-one",
        decision_id=source_decision.decision_id,
        evidence_ids=(source.evidence_id,),
    )

    with pytest.raises(LineageIntegrityError, match="lineage differ"):
        store.commit(
            transaction_id="tx-transition-one",
            run_id="run-a",
            iteration=1,
            created_at=NOW,
            records=(transition,),
        )


def test_next_iteration_snapshot_can_reference_prior_decision(tmp_path: Path) -> None:
    store = ControlRecordStore(tmp_path)
    source = evidence(run_id="run-a", iteration=1, evidence_id="ev-continue")
    source_decision = decision(
        run_id="run-a",
        iteration=1,
        decision_id="decision-continue",
        evidence_id=source.evidence_id,
    )
    store.commit(
        transaction_id="tx-continue-decision",
        run_id="run-a",
        iteration=1,
        created_at=NOW,
        records=(source, source_decision),
    )
    snapshot = StateSnapshot(
        snapshot_id="snapshot-next-iteration",
        run_id="run-a",
        iteration=2,
        cycle=0,
        state=ControlState.DIAGNOSE,
        entered_at=NOW,
        active_checkpoint_id="ckpt-peak",
        peak_checkpoint_id="ckpt-peak",
        peak_score=0.7,
        evidence_ids=(source.evidence_id,),
        metadata={"decision_id": source_decision.decision_id},
    )

    manifest = store.commit(
        transaction_id="tx-next-iteration-snapshot",
        run_id="run-a",
        iteration=1,
        created_at=NOW,
        records=(snapshot,),
    )

    assert manifest.iteration == 1
    assert store.load_snapshot(snapshot.snapshot_id) == snapshot


def test_non_snapshot_iteration_must_match_transaction(tmp_path: Path) -> None:
    store = ControlRecordStore(tmp_path)
    mismatched = evidence(run_id="run-a", iteration=2, evidence_id="ev-mismatch")

    with pytest.raises(ControlContractError, match="does not match"):
        store.commit(
            transaction_id="tx-mismatched-iteration",
            run_id="run-a",
            iteration=1,
            created_at=NOW,
            records=(mismatched,),
        )


def test_stored_record_reference_rejects_non_string_keys() -> None:
    with pytest.raises(ControlContractError, match="keys must be strings"):
        StoredRecordRef.from_dict(
            {
                1: "evidence",
                "record_id": "ev-001",
                "relative_path": "evidence/ev-001.json",
                "sha256": "a" * 64,
            }
        )
