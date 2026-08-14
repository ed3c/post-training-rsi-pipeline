from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .contracts import (
    RecoveryActivationContractError,
    RecoveryActivationPlan,
    RecoveryActivationPreflightError,
    RecoveryPreflightObservation,
    RecoveryPreflightReport,
    canonical_json,
)

DEFAULT_ALLOWED_REVIEWER_ROLES = (
    "recovery-admin",
    "recovery-operator",
)
DEFAULT_MAX_PLAN_TTL_SECONDS = 60 * 60
MAX_POLICY_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class RecoveryActivationPolicy:
    """Local preflight policy; this object never grants production authority."""

    allowed_reviewer_roles: tuple[str, ...] = DEFAULT_ALLOWED_REVIEWER_ROLES
    max_plan_ttl_seconds: int = DEFAULT_MAX_PLAN_TTL_SECONDS

    def __post_init__(self) -> None:
        roles = tuple(self.allowed_reviewer_roles)
        if not roles:
            raise RecoveryActivationContractError(
                "allowed_reviewer_roles must not be empty"
            )
        if len(roles) != len(set(roles)):
            raise RecoveryActivationContractError(
                "allowed_reviewer_roles must be unique"
            )
        for role in roles:
            if not isinstance(role, str) or not role.strip():
                raise RecoveryActivationContractError(
                    "allowed reviewer roles must be non-empty strings"
                )
            if len(role) > 256 or any(ord(character) < 33 for character in role):
                raise RecoveryActivationContractError(
                    "allowed reviewer roles must be safe identifiers"
                )
        if (
            isinstance(self.max_plan_ttl_seconds, bool)
            or not isinstance(self.max_plan_ttl_seconds, int)
            or not 1 <= self.max_plan_ttl_seconds <= MAX_POLICY_TTL_SECONDS
        ):
            raise RecoveryActivationContractError(
                "max_plan_ttl_seconds must be an integer in [1, 86400]"
            )
        object.__setattr__(self, "allowed_reviewer_roles", roles)

    @classmethod
    def from_roles(
        cls,
        roles: Iterable[str],
        *,
        max_plan_ttl_seconds: int = DEFAULT_MAX_PLAN_TTL_SECONDS,
    ) -> RecoveryActivationPolicy:
        return cls(
            allowed_reviewer_roles=tuple(roles),
            max_plan_ttl_seconds=max_plan_ttl_seconds,
        )


def plan_sha256(plan: RecoveryActivationPlan) -> str:
    return hashlib.sha256(canonical_json(plan.to_dict()).encode("utf-8")).hexdigest()


def verify_plan(
    plan: RecoveryActivationPlan,
    *,
    policy: RecoveryActivationPolicy | None = None,
) -> RecoveryActivationPlan:
    """Verify static activation-plan invariants without observing live state."""

    active_policy = policy or RecoveryActivationPolicy()
    if plan.authority.reviewer_role not in active_policy.allowed_reviewer_roles:
        raise RecoveryActivationPreflightError(
            "authority reviewer role is not allowed by the local recovery policy"
        )
    ttl_seconds = (_timestamp(plan.valid_until) - _timestamp(plan.created_at)).total_seconds()
    if ttl_seconds > active_policy.max_plan_ttl_seconds:
        raise RecoveryActivationPreflightError(
            "activation plan TTL exceeds the local recovery policy"
        )
    if plan.target.workspace_uri != plan.staged_evidence.staged_root_uri:
        raise RecoveryActivationPreflightError(
            "target workspace URI does not match the strictly audited staged root"
        )
    if plan.target.workspace_uri == plan.expected_live.workspace_uri:
        raise RecoveryActivationPreflightError(
            "target workspace URI must differ from the current live workspace"
        )
    if plan.target.pointer_sha256 == plan.expected_live.pointer_sha256:
        raise RecoveryActivationPreflightError(
            "target pointer hash must differ from the current live pointer hash"
        )
    if plan.rollback != plan.expected_live:
        raise RecoveryActivationPreflightError(
            "rollback pointer must exactly preserve the expected live generation"
        )
    return plan


def run_preflight(
    plan: RecoveryActivationPlan,
    observation: RecoveryPreflightObservation,
    *,
    policy: RecoveryActivationPolicy | None = None,
) -> RecoveryPreflightReport:
    """Compare a plan with explicit observations; never mutate a live pointer."""

    verify_plan(plan, policy=policy)
    if observation.plan_id != plan.plan_id:
        raise RecoveryActivationPreflightError(
            "preflight observation belongs to a different activation plan"
        )
    as_of = _timestamp(observation.as_of)
    if as_of < _timestamp(plan.created_at):
        raise RecoveryActivationPreflightError(
            "preflight observation predates the activation plan"
        )
    if as_of >= _timestamp(plan.valid_until):
        raise RecoveryActivationPreflightError("activation plan is expired")
    if as_of >= _timestamp(plan.authority.expires_at):
        raise RecoveryActivationPreflightError("recovery authority receipt is expired")
    if observation.current_live != plan.expected_live:
        raise RecoveryActivationPreflightError(
            "current live pointer does not match the plan compare-and-swap expectation"
        )
    comparisons = (
        (
            observation.observed_bundle_id,
            plan.staged_evidence.bundle_id,
            "recovery bundle identity",
        ),
        (
            observation.observed_bundle_verification_sha256,
            plan.staged_evidence.bundle_verification_sha256,
            "recovery bundle verification evidence",
        ),
        (
            observation.observed_staged_root_uri,
            plan.staged_evidence.staged_root_uri,
            "staged workspace URI",
        ),
        (
            observation.observed_staged_audit_report_sha256,
            plan.staged_evidence.staged_audit_report_sha256,
            "strict staged audit report",
        ),
        (
            observation.observed_approval_decision_sha256,
            plan.authority.decision_sha256,
            "recovery approval decision",
        ),
        (
            observation.observed_target_pointer_sha256,
            plan.target.pointer_sha256,
            "target pointer content",
        ),
    )
    for observed, expected, label in comparisons:
        if observed != expected:
            raise RecoveryActivationPreflightError(f"{label} does not match the plan")
    return RecoveryPreflightReport(
        plan_id=plan.plan_id,
        run_id=plan.run_id,
        status="READY_FOR_HUMAN_EXECUTION",
        checked_at=observation.as_of,
        expected_live_generation_id=plan.expected_live.generation_id,
        target_generation_id=plan.target.generation_id,
        rollback_generation_id=plan.rollback.generation_id,
        bundle_id=plan.staged_evidence.bundle_id,
        approval_decision_id=plan.authority.decision_id,
        executed=False,
    )


def _timestamp(value: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(candidate)
