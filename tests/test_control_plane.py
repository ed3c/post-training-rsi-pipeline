from __future__ import annotations

import json

import pytest

from post_training_rsi.control_plane import (
    CONTROL_SCHEMA_VERSION,
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

FIXED_TIME = "2026-08-14T01:02:03+08:00"
UTC_TIME = "2026-08-13T17:02:03Z"
SHA256 = "a" * 64


def make_evidence() -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="ev.dataset.iter-1",
        run_id="run-001",
        iteration=1,
        kind=EvidenceKind.ACCEPTED_DATASET,
        producer="verification.pipeline",
        uri="artifacts/iterations/iter-001/accepted.jsonl",
        created_at=FIXED_TIME,
        sha256=SHA256,
        metadata={"accepted": 12, "metrics": {"rate": 0.75}},
    )


def test_evidence_round_trip_is_canonical_and_detached() -> None:
    metadata = {"z": [2, 1], "a": {"ok": True}}
    record = EvidenceRecord(
        evidence_id="ev-001",
        run_id="run-001",
        iteration=1,
        kind=EvidenceKind.VERIFICATION_AUDIT,
        producer="verification.pipeline",
        uri="artifacts/audit.jsonl",
        created_at=FIXED_TIME,
        sha256=SHA256,
        metadata=metadata,
    )
    metadata["z"].append(0)

    payload = record.to_dict()
    restored = EvidenceRecord.from_dict(payload)

    assert record.created_at == UTC_TIME
    assert restored == record
    assert payload["schema_version"] == CONTROL_SCHEMA_VERSION
    assert payload["metadata"] == {"a": {"ok": True}, "z": [2, 1]}
    assert record.to_json() == restored.to_json()
    assert list(json.loads(record.to_json())) == sorted(json.loads(record.to_json()))


def test_evidence_rejects_bad_hash_unknown_fields_and_non_json_metadata() -> None:
    with pytest.raises(ControlContractError, match="sha256"):
        EvidenceRecord(
            evidence_id="ev-001",
            run_id="run-001",
            iteration=1,
            kind=EvidenceKind.CHECKPOINT,
            producer="training.mock",
            uri="artifacts/checkpoint",
            created_at=FIXED_TIME,
            sha256="ABC",
        )

    payload = make_evidence().to_dict()
    payload["unexpected"] = True
    with pytest.raises(ControlContractError, match="unknown=\\['unexpected'\\]"):
        EvidenceRecord.from_dict(payload)

    with pytest.raises(ControlContractError, match="non-JSON"):
        EvidenceRecord(
            evidence_id="ev-001",
            run_id="run-001",
            iteration=1,
            kind=EvidenceKind.CONFIG,
            producer="config.loader",
            uri="memory:config",
            created_at=FIXED_TIME,
            metadata={"bad": object()},
        )


def test_decision_requires_evidence_and_terminal_reason_contract() -> None:
    record = DecisionRecord(
        decision_id="decision-001",
        run_id="run-001",
        iteration=1,
        subject_type=DecisionSubject.CHECKPOINT,
        subject_id="ckpt-001",
        action=DecisionAction.PROMOTE,
        reason_code="candidate_above_peak",
        reason="Candidate exceeded the historical peak by the configured delta.",
        evidence_ids=("ev.eval-001", "ev.lineage-001"),
        created_at=FIXED_TIME,
        metadata={"delta": 0.03},
    )
    assert DecisionRecord.from_dict(record.to_dict()) == record

    with pytest.raises(ControlContractError, match="require stop_reason"):
        DecisionRecord(
            decision_id="decision-002",
            run_id="run-001",
            iteration=2,
            subject_type=DecisionSubject.RUN,
            subject_id="run-001",
            action=DecisionAction.STOP,
            reason_code="plateau",
            reason="No improvement within patience.",
            evidence_ids=("ev.eval-002",),
            created_at=FIXED_TIME,
        )

    with pytest.raises(ControlContractError, match="at least one evidence_id"):
        DecisionRecord(
            decision_id="decision-no-evidence",
            run_id="run-001",
            iteration=2,
            subject_type=DecisionSubject.DATASET,
            subject_id="dataset-002",
            action=DecisionAction.REJECT,
            reason_code="empty_dataset",
            reason="No accepted records were produced.",
            evidence_ids=(),
            created_at=FIXED_TIME,
            stop_reason=StopReason.NO_ACCEPTED_DATA,
        )

    with pytest.raises(ControlContractError, match="duplicate IDs"):
        DecisionRecord(
            decision_id="decision-003",
            run_id="run-001",
            iteration=2,
            subject_type=DecisionSubject.DATASET,
            subject_id="dataset-002",
            action=DecisionAction.QUARANTINE,
            reason_code="low_diversity",
            reason="Retention rate fell below the configured floor.",
            evidence_ids=("ev.audit-002", "ev.audit-002"),
            created_at=FIXED_TIME,
            stop_reason=StopReason.LOW_DIVERSITY,
        )


def test_terminal_snapshot_requires_stop_reason_and_nonterminal_forbids_it() -> None:
    completed = StateSnapshot(
        snapshot_id="state-001",
        run_id="run-001",
        iteration=3,
        cycle=0,
        state=ControlState.COMPLETED,
        entered_at=FIXED_TIME,
        peak_checkpoint_id="ckpt-002",
        peak_score=0.81,
        plateau_count=1,
        total_cost_usd=19.25,
        stop_reason=StopReason.COMPLETED,
        evidence_ids=("ev.summary-001",),
    )
    assert StateSnapshot.from_dict(completed.to_dict()) == completed

    with pytest.raises(ControlContractError, match="requires stop_reason"):
        StateSnapshot(
            snapshot_id="state-002",
            run_id="run-001",
            iteration=3,
            cycle=0,
            state=ControlState.STOPPED,
            entered_at=FIXED_TIME,
        )

    with pytest.raises(ControlContractError, match="cannot carry stop_reason"):
        StateSnapshot(
            snapshot_id="state-003",
            run_id="run-001",
            iteration=1,
            cycle=0,
            state=ControlState.EVALUATE,
            entered_at=FIXED_TIME,
            stop_reason=StopReason.PLATEAU,
        )


@pytest.mark.parametrize("bad_cost", [-0.01, float("nan"), float("inf")])
def test_snapshot_rejects_invalid_costs(bad_cost: float) -> None:
    with pytest.raises(ControlContractError, match="total_cost_usd"):
        StateSnapshot(
            snapshot_id="state-001",
            run_id="run-001",
            iteration=1,
            cycle=0,
            state=ControlState.TRAIN,
            entered_at=FIXED_TIME,
            total_cost_usd=bad_cost,
        )


def test_transition_round_trip_and_start_guard() -> None:
    transition = TransitionRecord(
        transition_id="transition-001",
        run_id="run-001",
        iteration=0,
        from_state=None,
        event=ControlEvent.START,
        to_state=ControlState.BOOT,
        occurred_at=FIXED_TIME,
        idempotency_key="run-001.start",
        evidence_ids=("ev.config-001",),
    )
    assert TransitionRecord.from_dict(transition.to_dict()) == transition

    with pytest.raises(ControlContractError, match="only for START"):
        TransitionRecord(
            transition_id="transition-002",
            run_id="run-001",
            iteration=1,
            from_state=None,
            event=ControlEvent.VERIFICATION_COMPLETED,
            to_state=ControlState.VERIFIED,
            occurred_at=FIXED_TIME,
            idempotency_key="run-001.verify-1",
            evidence_ids=("ev.verify-001",),
        )

    with pytest.raises(ControlContractError, match="must not declare from_state"):
        TransitionRecord(
            transition_id="transition-003",
            run_id="run-001",
            iteration=1,
            from_state=ControlState.BOOT,
            event=ControlEvent.START,
            to_state=ControlState.CONFIG_LOADED,
            occurred_at=FIXED_TIME,
            idempotency_key="run-001.start-duplicate",
            evidence_ids=("ev.config-002",),
        )


def test_transition_contract_does_not_embed_adjacency_policy() -> None:
    retry_fact = TransitionRecord(
        transition_id="transition-retry-001",
        run_id="run-001",
        iteration=2,
        from_state=ControlState.SYNTHESIZE,
        event=ControlEvent.SYNTHESIS_COMPLETED,
        to_state=ControlState.SYNTHESIZE,
        occurred_at=FIXED_TIME,
        idempotency_key="run-001.synthesis-attempt-2",
        evidence_ids=("ev.synthesis-attempt-2",),
        metadata={"attempt": 2},
    )
    assert retry_fact.from_state == retry_fact.to_state


def test_deserialization_rejects_wrong_schema_and_enum() -> None:
    payload = make_evidence().to_dict()
    payload["schema_version"] = "post-training-rsi.control/v2"
    with pytest.raises(ControlContractError, match="unsupported schema_version"):
        EvidenceRecord.from_dict(payload)

    payload = make_evidence().to_dict()
    payload["kind"] = "UNKNOWN_KIND"
    with pytest.raises(ControlContractError, match="unsupported value"):
        EvidenceRecord.from_dict(payload)


def test_timestamp_requires_timezone() -> None:
    with pytest.raises(ControlContractError, match="UTC offset"):
        EvidenceRecord(
            evidence_id="ev-001",
            run_id="run-001",
            iteration=1,
            kind=EvidenceKind.CONFIG,
            producer="config.loader",
            uri="memory:config",
            created_at="2026-08-14T01:02:03",
        )
