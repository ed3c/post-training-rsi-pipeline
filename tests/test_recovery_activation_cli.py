from __future__ import annotations

import json
from pathlib import Path

import pytest

from post_training_rsi.recovery_activation import (
    REQUEST_SCHEMA_VERSION,
    RecoveryActivationPlan,
    RecoveryAuthorityReceipt,
    RecoveryPointer,
    RecoveryPreflightObservation,
    StagedRecoveryEvidence,
)
from post_training_rsi.recovery_activation.__main__ import main


def _records() -> tuple[dict[str, object], RecoveryPreflightObservation]:
    expected = RecoveryPointer(
        generation_id="generation-001",
        pointer_sha256="a" * 64,
        workspace_uri="file:///srv/rsi/live-generation-001",
    )
    target = RecoveryPointer(
        generation_id="generation-002",
        pointer_sha256="b" * 64,
        workspace_uri="file:///srv/rsi/staged-generation-002",
    )
    staged = StagedRecoveryEvidence(
        bundle_id="c" * 64,
        bundle_verification_sha256="d" * 64,
        staged_root_uri=target.workspace_uri,
        staged_audit_status="PASS",
        staged_audit_report_sha256="e" * 64,
    )
    authority = RecoveryAuthorityReceipt.create(
        request_id="request-001",
        decision_id="decision-001",
        decision_sha256="f" * 64,
        recovery_ticket_id="recovery-ticket-001",
        requester_id="requester-001",
        reviewer_id="reviewer-001",
        reviewer_role="recovery-admin",
        approved_bundle_id=staged.bundle_id,
        expected_live_generation_id=expected.generation_id,
        target_generation_id=target.generation_id,
        approved_at="2026-08-14T00:00:00Z",
        expires_at="2026-08-14T02:00:00Z",
    )
    request: dict[str, object] = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "record_type": "recovery_activation_request",
        "run_id": "run-001",
        "expected_live": expected.to_dict(),
        "target": target.to_dict(),
        "staged_evidence": staged.to_dict(),
        "authority": authority.to_dict(),
        "created_at": "2026-08-14T00:30:00Z",
        "valid_until": "2026-08-14T01:30:00Z",
        "reason": "Restore the last strictly audited generation.",
        "metadata": {"incident_id": "incident-001"},
    }
    plan = RecoveryActivationPlan.create(
        run_id="run-001",
        expected_live=expected,
        target=target,
        staged_evidence=staged,
        authority=authority,
        created_at="2026-08-14T00:30:00Z",
        valid_until="2026-08-14T01:30:00Z",
        reason="Restore the last strictly audited generation.",
        metadata={"incident_id": "incident-001"},
    )
    observation = RecoveryPreflightObservation(
        plan_id=plan.plan_id,
        as_of="2026-08-14T01:00:00Z",
        current_live=expected,
        observed_bundle_id=staged.bundle_id,
        observed_bundle_verification_sha256=staged.bundle_verification_sha256,
        observed_staged_root_uri=staged.staged_root_uri,
        observed_staged_audit_report_sha256=staged.staged_audit_report_sha256,
        observed_approval_decision_sha256=authority.decision_sha256,
        observed_target_pointer_sha256=target.pointer_sha256,
    )
    return request, observation


def test_package_cli_build_verify_and_preflight_are_non_executing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request, observation = _records()
    request_path = tmp_path / "request.json"
    plan_path = tmp_path / "plan.json"
    observation_path = tmp_path / "observation.json"
    request_path.write_text(
        json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "build",
                "--request",
                str(request_path),
                "--output",
                str(plan_path),
            ]
        )
        == 0
    )
    built = json.loads(capsys.readouterr().out)
    assert built["status"] == "planned"
    assert built["executed"] is False
    assert plan_path.is_file()

    plan = RecoveryActivationPlan.from_dict(
        json.loads(plan_path.read_text(encoding="utf-8"))
    )
    assert plan.plan_id == built["plan_id"]

    assert main(["verify", "--plan", str(plan_path)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "verified"
    assert verified["executed"] is False

    exact_observation = RecoveryPreflightObservation(
        plan_id=plan.plan_id,
        as_of=observation.as_of,
        current_live=observation.current_live,
        observed_bundle_id=observation.observed_bundle_id,
        observed_bundle_verification_sha256=(
            observation.observed_bundle_verification_sha256
        ),
        observed_staged_root_uri=observation.observed_staged_root_uri,
        observed_staged_audit_report_sha256=(
            observation.observed_staged_audit_report_sha256
        ),
        observed_approval_decision_sha256=(
            observation.observed_approval_decision_sha256
        ),
        observed_target_pointer_sha256=observation.observed_target_pointer_sha256,
    )
    observation_path.write_text(
        json.dumps(exact_observation.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert (
        main(
            [
                "preflight",
                "--plan",
                str(plan_path),
                "--observation",
                str(observation_path),
            ]
        )
        == 0
    )
    preflight = json.loads(capsys.readouterr().out)
    assert preflight["status"] == "READY_FOR_HUMAN_EXECUTION"
    assert preflight["executed"] is False


def test_build_never_overwrites_existing_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request, _ = _records()
    request_path = tmp_path / "request.json"
    plan_path = tmp_path / "plan.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    plan_path.write_text("preserve me\n", encoding="utf-8")

    result = main(
        [
            "build",
            "--request",
            str(request_path),
            "--output",
            str(plan_path),
        ]
    )

    assert result == 2
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "error"
    assert value["executed"] is False
    assert "already exists" in value["message"]
    assert plan_path.read_text(encoding="utf-8") == "preserve me\n"


def test_preflight_substitution_returns_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request, observation = _records()
    request_path = tmp_path / "request.json"
    plan_path = tmp_path / "plan.json"
    observation_path = tmp_path / "observation.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    assert (
        main(
            [
                "build",
                "--request",
                str(request_path),
                "--output",
                str(plan_path),
            ]
        )
        == 0
    )
    capsys.readouterr()
    altered = observation.to_dict()
    altered["observed_bundle_id"] = "0" * 64
    observation_path.write_text(json.dumps(altered), encoding="utf-8")

    result = main(
        [
            "preflight",
            "--plan",
            str(plan_path),
            "--observation",
            str(observation_path),
        ]
    )

    assert result == 2
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "error"
    assert value["executed"] is False
    assert "bundle identity" in value["message"]


def test_cli_exposes_exactly_the_non_executing_commands() -> None:
    """The subcommand set is the real guard; help prose can deny a verb it names."""
    from post_training_rsi.recovery_activation.__main__ import _parser

    registered: set[str] = set()
    for action in _parser()._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            registered.update(choices)
    assert registered == {"build", "verify", "preflight"}

    for forbidden in ("activate", "apply", "switch", "resume", "rollback"):
        with pytest.raises(SystemExit) as raised:
            main([forbidden])
        assert raised.value.code == 2
