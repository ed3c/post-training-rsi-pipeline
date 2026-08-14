from __future__ import annotations

from dataclasses import replace

import pytest

from post_training_rsi.recovery_activation import (
    RecoveryActivationContractError,
    RecoveryActivationPlan,
    RecoveryActivationPolicy,
    RecoveryActivationPreflightError,
    RecoveryAuthorityReceipt,
    RecoveryPointer,
    RecoveryPreflightObservation,
    StagedRecoveryEvidence,
    plan_sha256,
    run_preflight,
    verify_plan,
)

EXPECTED = RecoveryPointer(
    generation_id="generation-001",
    pointer_sha256="a" * 64,
    workspace_uri="file:///srv/rsi/live-generation-001",
)
TARGET = RecoveryPointer(
    generation_id="generation-002",
    pointer_sha256="b" * 64,
    workspace_uri="file:///srv/rsi/staged-generation-002",
)
STAGED = StagedRecoveryEvidence(
    bundle_id="c" * 64,
    bundle_verification_sha256="d" * 64,
    staged_root_uri=TARGET.workspace_uri,
    staged_audit_status="PASS",
    staged_audit_report_sha256="e" * 64,
)


def authority(
    *,
    reviewer_id: str = "reviewer-001",
    reviewer_role: str = "recovery-admin",
    bundle_id: str = STAGED.bundle_id,
    expected_generation: str = EXPECTED.generation_id,
    target_generation: str = TARGET.generation_id,
    approved_at: str = "2026-08-14T00:00:00Z",
    expires_at: str = "2026-08-14T02:00:00Z",
) -> RecoveryAuthorityReceipt:
    return RecoveryAuthorityReceipt.create(
        request_id="request-001",
        decision_id="decision-001",
        decision_sha256="f" * 64,
        recovery_ticket_id="recovery-ticket-001",
        requester_id="requester-001",
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        approved_bundle_id=bundle_id,
        expected_live_generation_id=expected_generation,
        target_generation_id=target_generation,
        approved_at=approved_at,
        expires_at=expires_at,
    )


def plan(
    *,
    target: RecoveryPointer = TARGET,
    staged: StagedRecoveryEvidence = STAGED,
    receipt: RecoveryAuthorityReceipt | None = None,
    created_at: str = "2026-08-14T00:30:00Z",
    valid_until: str = "2026-08-14T01:30:00Z",
    metadata: dict[str, str] | None = None,
) -> RecoveryActivationPlan:
    return RecoveryActivationPlan.create(
        run_id="run-001",
        expected_live=EXPECTED,
        target=target,
        staged_evidence=staged,
        authority=receipt or authority(),
        created_at=created_at,
        valid_until=valid_until,
        reason="Restore the last strictly audited generation after corruption.",
        metadata=metadata or {"incident_id": "incident-001"},
    )


def observation(
    value: RecoveryActivationPlan,
    *,
    as_of: str = "2026-08-14T01:00:00Z",
) -> RecoveryPreflightObservation:
    return RecoveryPreflightObservation(
        plan_id=value.plan_id,
        as_of=as_of,
        current_live=value.expected_live,
        observed_bundle_id=value.staged_evidence.bundle_id,
        observed_bundle_verification_sha256=(
            value.staged_evidence.bundle_verification_sha256
        ),
        observed_staged_root_uri=value.staged_evidence.staged_root_uri,
        observed_staged_audit_report_sha256=(
            value.staged_evidence.staged_audit_report_sha256
        ),
        observed_approval_decision_sha256=value.authority.decision_sha256,
        observed_target_pointer_sha256=value.target.pointer_sha256,
    )


def test_plan_round_trip_is_content_addressed_and_preflight_is_non_executing() -> None:
    value = plan()
    parsed = RecoveryActivationPlan.from_dict(value.to_dict())

    assert parsed == value
    assert parsed.plan_id.startswith("recovery-plan-")
    assert len(plan_sha256(parsed)) == 64
    verify_plan(parsed)

    report = run_preflight(parsed, observation(parsed))
    assert report.status == "READY_FOR_HUMAN_EXECUTION"
    assert report.executed is False
    assert report.rollback_generation_id == EXPECTED.generation_id
    assert report.target_generation_id == TARGET.generation_id


def test_plan_identity_changes_with_any_bound_pointer() -> None:
    first = plan()
    second_target = RecoveryPointer(
        generation_id="generation-003",
        pointer_sha256="9" * 64,
        workspace_uri="file:///srv/rsi/staged-generation-003",
    )
    second_staged = replace(STAGED, staged_root_uri=second_target.workspace_uri)
    second_receipt = authority(target_generation=second_target.generation_id)
    second = plan(target=second_target, staged=second_staged, receipt=second_receipt)

    assert first.plan_id != second.plan_id
    assert plan_sha256(first) != plan_sha256(second)


def test_static_policy_rejects_unapproved_role_and_excessive_ttl() -> None:
    unapproved = plan(receipt=authority(reviewer_role="observer"))
    with pytest.raises(RecoveryActivationPreflightError, match="role is not allowed"):
        verify_plan(unapproved)

    with pytest.raises(RecoveryActivationPreflightError, match="TTL exceeds"):
        verify_plan(
            plan(valid_until="2026-08-14T01:45:00Z"),
            policy=RecoveryActivationPolicy(max_plan_ttl_seconds=3600),
        )


def test_static_policy_requires_target_to_equal_audited_stage() -> None:
    mismatched_target = replace(
        TARGET,
        workspace_uri="file:///srv/rsi/different-stage",
    )
    value = plan(target=mismatched_target)

    with pytest.raises(RecoveryActivationPreflightError, match="audited staged root"):
        verify_plan(value)


def test_receipt_must_be_separation_of_duties_and_content_bound() -> None:
    with pytest.raises(RecoveryActivationContractError, match="self-approved"):
        RecoveryAuthorityReceipt.create(
            request_id="request-001",
            decision_id="decision-001",
            decision_sha256="f" * 64,
            recovery_ticket_id="recovery-ticket-001",
            requester_id="same-user",
            reviewer_id="same-user",
            reviewer_role="recovery-admin",
            approved_bundle_id=STAGED.bundle_id,
            expected_live_generation_id=EXPECTED.generation_id,
            target_generation_id=TARGET.generation_id,
            approved_at="2026-08-14T00:00:00Z",
            expires_at="2026-08-14T02:00:00Z",
        )

    with pytest.raises(RecoveryActivationContractError, match="different recovery bundle"):
        plan(receipt=authority(bundle_id="0" * 64))


def test_plan_requires_exact_rollback_pointer() -> None:
    valid = plan()
    wrong_rollback = RecoveryPointer(
        generation_id="generation-rollback-other",
        pointer_sha256="8" * 64,
        workspace_uri="file:///srv/rsi/other-rollback",
    )

    with pytest.raises(RecoveryActivationContractError, match="rollback pointer"):
        RecoveryActivationPlan(
            plan_id=valid.plan_id,
            run_id=valid.run_id,
            expected_live=valid.expected_live,
            target=valid.target,
            rollback=wrong_rollback,
            staged_evidence=valid.staged_evidence,
            authority=valid.authority,
            created_at=valid.created_at,
            valid_until=valid.valid_until,
            reason=valid.reason,
            metadata=valid.metadata,
        )


def test_secret_like_metadata_and_network_uris_fail_closed() -> None:
    with pytest.raises(RecoveryActivationContractError, match="secret-like"):
        plan(metadata={"api_token": "must-not-enter-plan"})

    with pytest.raises(RecoveryActivationContractError, match="network destination"):
        RecoveryPointer(
            generation_id="generation-network",
            pointer_sha256="7" * 64,
            workspace_uri="https://example.invalid/recovery",
        )


def test_unknown_plan_fields_and_tampered_identity_fail_closed() -> None:
    value = plan().to_dict()
    value["unexpected"] = True
    with pytest.raises(RecoveryActivationContractError, match="fields mismatch"):
        RecoveryActivationPlan.from_dict(value)

    tampered = plan().to_dict()
    tampered["reason"] = "Different recovery reason."
    with pytest.raises(RecoveryActivationContractError, match="plan_id"):
        RecoveryActivationPlan.from_dict(tampered)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("plan_id", "recovery-plan-other", "different activation plan"),
        ("observed_bundle_id", "0" * 64, "bundle identity"),
        (
            "observed_bundle_verification_sha256",
            "1" * 64,
            "bundle verification evidence",
        ),
        (
            "observed_staged_root_uri",
            "file:///srv/rsi/other-stage",
            "staged workspace URI",
        ),
        (
            "observed_staged_audit_report_sha256",
            "2" * 64,
            "strict staged audit report",
        ),
        (
            "observed_approval_decision_sha256",
            "3" * 64,
            "approval decision",
        ),
        (
            "observed_target_pointer_sha256",
            "4" * 64,
            "target pointer content",
        ),
    ],
)
def test_preflight_rejects_evidence_substitution(
    field: str,
    replacement: str,
    message: str,
) -> None:
    value = plan()
    altered = replace(observation(value), **{field: replacement})

    with pytest.raises(RecoveryActivationPreflightError, match=message):
        run_preflight(value, altered)


def test_preflight_rejects_stale_live_pointer_and_expiry() -> None:
    value = plan()
    stale_pointer = RecoveryPointer(
        generation_id="generation-stale",
        pointer_sha256="5" * 64,
        workspace_uri="file:///srv/rsi/live-stale",
    )
    stale = replace(observation(value), current_live=stale_pointer)
    with pytest.raises(RecoveryActivationPreflightError, match="compare-and-swap"):
        run_preflight(value, stale)

    expired = observation(value, as_of=value.valid_until)
    with pytest.raises(RecoveryActivationPreflightError, match="plan is expired"):
        run_preflight(value, expired)
