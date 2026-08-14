from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from post_training_rsi.approval import (
    ApprovalCandidate,
    ApprovalConflictError,
    ApprovalContractError,
    ApprovalIntegrityError,
    ApprovalNotGranted,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalService,
    ApprovalState,
    ApprovalStore,
    record_sha256,
)
from post_training_rsi.control_plane import (
    DecisionAction,
    DecisionSubject,
    EvidenceKind,
    StopReason,
)

REQUESTED_AT = "2026-08-14T01:00:00Z"
DECIDED_AT = "2026-08-14T01:05:00Z"
EXPIRED_AT = "2026-08-14T01:11:00Z"


def _candidate(index: int) -> ApprovalCandidate:
    content = f"candidate-content-{index}".encode()
    return ApprovalCandidate(
        item_id=f"item-{index:03d}",
        content_sha256=hashlib.sha256(content).hexdigest(),
        metadata={"task_family": "tool-use", "index": index},
    )


def _policy() -> ApprovalPolicy:
    return ApprovalPolicy(
        policy_id="hitl-test-v1",
        dataset_review_required=True,
        checkpoint_review_required=True,
        harness_review_required=True,
        sample_rate=0.01,
        min_sample_items=1,
        max_sample_items=5,
        decision_ttl_seconds=600,
        allowed_reviewer_roles=("researcher", "release-manager"),
    )


def _service(tmp_path: Path) -> ApprovalService:
    return ApprovalService(
        store=ApprovalStore(tmp_path),
        policy=_policy(),
        clock=lambda: REQUESTED_AT,
    )


def _create_dataset_request(
    service: ApprovalService,
    *,
    candidate_order: tuple[int, ...] = tuple(range(200)),
):
    return service.create_request(
        run_id="run-001",
        iteration=1,
        subject_type=DecisionSubject.DATASET,
        subject_id="dataset-001",
        candidates=tuple(_candidate(index) for index in candidate_order),
        source_evidence_ids=("ev.dataset.001", "ev.verify.001"),
        selection_seed="sample-seed-001",
        requested_at=REQUESTED_AT,
        metadata={"reason": "random quality review"},
    )


def test_request_sampling_is_deterministic_content_addressed_and_idempotent(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    first = _create_dataset_request(service)
    replay = _create_dataset_request(service)

    assert replay.request == first.request
    assert replay.sample == first.sample
    assert first.sample.population_count == 200
    assert first.sample.selected_count == 2
    assert first.request.sample_count == 2
    assert first.request.request_id.startswith("approval-dataset-")
    assert service.pending_request_ids(as_of=DECIDED_AT) == (
        first.request.request_id,
    )
    assert "candidate-content" not in first.sample.to_json()
    assert first.request_evidence.kind is EvidenceKind.APPROVAL_REQUEST

    reversed_service = _service(tmp_path / "reversed")
    reversed_request = _create_dataset_request(
        reversed_service,
        candidate_order=tuple(reversed(range(200))),
    )
    assert [item.item_id for item in reversed_request.sample.items] == [
        item.item_id for item in first.sample.items
    ]
    assert reversed_request.request.request_id == first.request.request_id


def test_approved_dataset_produces_control_decision_and_gate_passes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    request_bundle = _create_dataset_request(service)
    request_sha256 = record_sha256(request_bundle.request.to_dict())

    reviewed = service.review(
        request_id=request_bundle.request.request_id,
        expected_request_sha256=request_sha256,
        approved=True,
        reviewer_id="reviewer-001",
        reviewer_role="researcher",
        reason="Sample and verification evidence are acceptable.",
        decided_at=DECIDED_AT,
    )
    gated = service.require_approved(
        request_id=request_bundle.request.request_id,
        expected_subject_type=DecisionSubject.DATASET,
        expected_subject_id="dataset-001",
        expected_action=DecisionAction.ACCEPT,
        expected_request_sha256=request_sha256,
        as_of=DECIDED_AT,
    )

    assert service.status(
        request_bundle.request.request_id,
        as_of=DECIDED_AT,
    ).state is ApprovalState.APPROVED
    assert gated.decision == reviewed.decision
    assert reviewed.control_decision.action is DecisionAction.ACCEPT
    assert reviewed.control_decision.stop_reason is None
    assert reviewed.request_evidence.kind is EvidenceKind.APPROVAL_REQUEST
    assert reviewed.decision_evidence.kind is EvidenceKind.APPROVAL_DECISION
    assert reviewed.decision_evidence.metadata["reviewer_role"] == "researcher"


def test_denied_checkpoint_fails_closed_and_emits_rejection(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    request = service.create_request(
        run_id="run-001",
        iteration=2,
        subject_type=DecisionSubject.CHECKPOINT,
        subject_id="checkpoint-002",
        candidates=(_candidate(1),),
        source_evidence_ids=("ev.checkpoint.002", "ev.eval.002"),
        selection_seed="checkpoint-seed",
        requested_at=REQUESTED_AT,
    )
    request_sha256 = record_sha256(request.request.to_dict())
    reviewed = service.review(
        request_id=request.request.request_id,
        expected_request_sha256=request_sha256,
        approved=False,
        reviewer_id="reviewer-002",
        reviewer_role="release-manager",
        reason="Regression evidence requires rejection.",
        decided_at=DECIDED_AT,
    )

    assert reviewed.control_decision.action is DecisionAction.REJECT
    assert reviewed.control_decision.stop_reason is StopReason.APPROVAL_NOT_GRANTED
    with pytest.raises(ApprovalNotGranted) as captured:
        service.require_approved(
            request_id=request.request.request_id,
            expected_subject_type=DecisionSubject.CHECKPOINT,
            expected_subject_id="checkpoint-002",
            expected_action=DecisionAction.PROMOTE,
            expected_request_sha256=request_sha256,
            as_of=DECIDED_AT,
        )
    assert captured.value.state is ApprovalState.DENIED


def test_missing_pending_and_expired_requests_do_not_grant_authority(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    with pytest.raises(ApprovalNotGranted) as missing:
        service.require_approved(
            request_id="approval-dataset-missing",
            expected_subject_type=DecisionSubject.DATASET,
            expected_subject_id="dataset-001",
            expected_action=DecisionAction.ACCEPT,
            expected_request_sha256="a" * 64,
            as_of=DECIDED_AT,
        )
    assert missing.value.state is ApprovalState.MISSING

    request = _create_dataset_request(service)
    request_sha256 = record_sha256(request.request.to_dict())
    with pytest.raises(ApprovalNotGranted) as pending:
        service.require_approved(
            request_id=request.request.request_id,
            expected_subject_type=DecisionSubject.DATASET,
            expected_subject_id="dataset-001",
            expected_action=DecisionAction.ACCEPT,
            expected_request_sha256=request_sha256,
            as_of=DECIDED_AT,
        )
    assert pending.value.state is ApprovalState.PENDING
    assert service.status(
        request.request.request_id,
        as_of=EXPIRED_AT,
    ).state is ApprovalState.EXPIRED

    with pytest.raises(ApprovalNotGranted) as expired:
        service.review(
            request_id=request.request.request_id,
            expected_request_sha256=request_sha256,
            approved=True,
            reviewer_id="reviewer-001",
            reviewer_role="researcher",
            reason="Late decision.",
            decided_at=EXPIRED_AT,
        )
    assert expired.value.state is ApprovalState.EXPIRED


def test_review_requires_expected_hash_and_authorized_role(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = _create_dataset_request(service)

    with pytest.raises(ApprovalIntegrityError, match="expected request"):
        service.review(
            request_id=request.request.request_id,
            expected_request_sha256="b" * 64,
            approved=True,
            reviewer_id="reviewer-001",
            reviewer_role="researcher",
            reason="Wrong request hash.",
            decided_at=DECIDED_AT,
        )
    with pytest.raises(ApprovalContractError, match="not authorized"):
        service.review(
            request_id=request.request.request_id,
            expected_request_sha256=record_sha256(request.request.to_dict()),
            approved=True,
            reviewer_id="reviewer-001",
            reviewer_role="observer",
            reason="Unauthorized role.",
            decided_at=DECIDED_AT,
        )


def test_decision_replay_is_exact_and_conflicting_review_is_rejected(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    request = _create_dataset_request(service)
    request_sha256 = record_sha256(request.request.to_dict())
    kwargs = {
        "request_id": request.request.request_id,
        "expected_request_sha256": request_sha256,
        "approved": True,
        "reviewer_id": "reviewer-001",
        "reviewer_role": "researcher",
        "reason": "Approved after review.",
        "decided_at": DECIDED_AT,
    }
    first = service.review(**kwargs)
    replay = service.review(**kwargs)

    assert replay.decision == first.decision
    with pytest.raises(ApprovalConflictError):
        service.review(
            request_id=request.request.request_id,
            expected_request_sha256=request_sha256,
            approved=False,
            reviewer_id="reviewer-001",
            reviewer_role="researcher",
            reason="Conflicting second decision.",
            decided_at=DECIDED_AT,
        )


def test_sample_tampering_and_subject_substitution_are_detected(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    request = _create_dataset_request(service)
    sample_path = service.store.sample_path(request.request.request_id)
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    payload["items"][0]["content_sha256"] = "f" * 64
    sample_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ApprovalIntegrityError, match="sample_sha256"):
        service.store.load_request(request.request.request_id)

    clean_service = _service(tmp_path / "clean")
    clean = _create_dataset_request(clean_service)
    clean_hash = record_sha256(clean.request.to_dict())
    clean_service.review(
        request_id=clean.request.request_id,
        expected_request_sha256=clean_hash,
        approved=True,
        reviewer_id="reviewer-001",
        reviewer_role="researcher",
        reason="Approved.",
        decided_at=DECIDED_AT,
    )
    with pytest.raises(ApprovalIntegrityError, match="subject_id"):
        clean_service.require_approved(
            request_id=clean.request.request_id,
            expected_subject_type=DecisionSubject.DATASET,
            expected_subject_id="dataset-substitution",
            expected_action=DecisionAction.ACCEPT,
            expected_request_sha256=clean_hash,
            as_of=DECIDED_AT,
        )


def test_records_reject_unknown_fields_and_disabled_policy(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = _create_dataset_request(service)
    payload = request.request.to_dict()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="unknown"):
        ApprovalRequest.from_dict(payload)

    disabled = ApprovalService(
        store=ApprovalStore(tmp_path / "disabled"),
        policy=ApprovalPolicy(),
        clock=lambda: REQUESTED_AT,
    )
    with pytest.raises(ApprovalContractError, match="review is disabled"):
        disabled.create_request(
            run_id="run-001",
            iteration=1,
            subject_type=DecisionSubject.DATASET,
            subject_id="dataset-001",
            candidates=(_candidate(1),),
            source_evidence_ids=("ev.dataset.001",),
            selection_seed="seed-001",
        )
