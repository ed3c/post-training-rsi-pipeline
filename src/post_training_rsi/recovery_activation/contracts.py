from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

PLAN_SCHEMA_VERSION = "post-training-rsi.recovery-activation-plan/v1"
REQUEST_SCHEMA_VERSION = "post-training-rsi.recovery-activation-request/v1"
RECEIPT_SCHEMA_VERSION = "post-training-rsi.recovery-authority-receipt/v1"
OBSERVATION_SCHEMA_VERSION = "post-training-rsi.recovery-preflight/v1"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SECRET_KEY_FRAGMENTS = {
    "api_key",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
}


class RecoveryActivationError(RuntimeError):
    """Base class for fail-closed recovery activation planning failures."""


class RecoveryActivationContractError(RecoveryActivationError):
    """Raised when a plan, receipt, request, or observation is malformed."""


class RecoveryActivationPreflightError(RecoveryActivationError):
    """Raised when observed recovery evidence does not match the plan."""


@dataclass(frozen=True, slots=True)
class RecoveryPointer:
    generation_id: str
    pointer_sha256: str
    workspace_uri: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "generation_id",
            _validate_id(self.generation_id, "generation_id"),
        )
        _validate_sha256(self.pointer_sha256, "pointer_sha256")
        object.__setattr__(
            self,
            "workspace_uri",
            _validate_uri(self.workspace_uri, "workspace_uri"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "generation_id": self.generation_id,
            "pointer_sha256": self.pointer_sha256,
            "workspace_uri": self.workspace_uri,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RecoveryPointer:
        data = _exact_mapping(
            value,
            {"generation_id", "pointer_sha256", "workspace_uri"},
            "recovery pointer",
        )
        return cls(
            generation_id=_required_string(data, "generation_id"),
            pointer_sha256=_required_string(data, "pointer_sha256"),
            workspace_uri=_required_string(data, "workspace_uri"),
        )


@dataclass(frozen=True, slots=True)
class StagedRecoveryEvidence:
    bundle_id: str
    bundle_verification_sha256: str
    staged_root_uri: str
    staged_audit_status: str
    staged_audit_report_sha256: str

    def __post_init__(self) -> None:
        _validate_sha256(self.bundle_id, "bundle_id")
        _validate_sha256(
            self.bundle_verification_sha256,
            "bundle_verification_sha256",
        )
        object.__setattr__(
            self,
            "staged_root_uri",
            _validate_uri(self.staged_root_uri, "staged_root_uri"),
        )
        if self.staged_audit_status != "PASS":
            raise RecoveryActivationContractError(
                "staged_audit_status must be exactly 'PASS'"
            )
        _validate_sha256(
            self.staged_audit_report_sha256,
            "staged_audit_report_sha256",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_verification_sha256": self.bundle_verification_sha256,
            "staged_root_uri": self.staged_root_uri,
            "staged_audit_status": self.staged_audit_status,
            "staged_audit_report_sha256": self.staged_audit_report_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> StagedRecoveryEvidence:
        data = _exact_mapping(
            value,
            {
                "bundle_id",
                "bundle_verification_sha256",
                "staged_root_uri",
                "staged_audit_status",
                "staged_audit_report_sha256",
            },
            "staged recovery evidence",
        )
        return cls(
            bundle_id=_required_string(data, "bundle_id"),
            bundle_verification_sha256=_required_string(
                data,
                "bundle_verification_sha256",
            ),
            staged_root_uri=_required_string(data, "staged_root_uri"),
            staged_audit_status=_required_string(data, "staged_audit_status"),
            staged_audit_report_sha256=_required_string(
                data,
                "staged_audit_report_sha256",
            ),
        )


@dataclass(frozen=True, slots=True)
class RecoveryAuthorityReceipt:
    receipt_id: str
    request_id: str
    decision_id: str
    decision_sha256: str
    recovery_ticket_id: str
    requester_id: str
    reviewer_id: str
    reviewer_role: str
    approved_bundle_id: str
    expected_live_generation_id: str
    target_generation_id: str
    approved_at: str
    expires_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _validate_id(self.request_id, "request_id"))
        object.__setattr__(
            self,
            "decision_id",
            _validate_id(self.decision_id, "decision_id"),
        )
        _validate_sha256(self.decision_sha256, "decision_sha256")
        object.__setattr__(
            self,
            "recovery_ticket_id",
            _validate_id(self.recovery_ticket_id, "recovery_ticket_id"),
        )
        object.__setattr__(
            self,
            "requester_id",
            _validate_id(self.requester_id, "requester_id"),
        )
        object.__setattr__(
            self,
            "reviewer_id",
            _validate_id(self.reviewer_id, "reviewer_id"),
        )
        object.__setattr__(
            self,
            "reviewer_role",
            _validate_id(self.reviewer_role, "reviewer_role"),
        )
        if self.requester_id == self.reviewer_id:
            raise RecoveryActivationContractError(
                "recovery authority receipt must not be self-approved"
            )
        _validate_sha256(self.approved_bundle_id, "approved_bundle_id")
        object.__setattr__(
            self,
            "expected_live_generation_id",
            _validate_id(
                self.expected_live_generation_id,
                "expected_live_generation_id",
            ),
        )
        object.__setattr__(
            self,
            "target_generation_id",
            _validate_id(self.target_generation_id, "target_generation_id"),
        )
        approved_at = _normalize_timestamp(self.approved_at, "approved_at")
        expires_at = _normalize_timestamp(self.expires_at, "expires_at")
        if _timestamp(expires_at) <= _timestamp(approved_at):
            raise RecoveryActivationContractError(
                "authority receipt expires_at must be later than approved_at"
            )
        object.__setattr__(self, "approved_at", approved_at)
        object.__setattr__(self, "expires_at", expires_at)
        expected_receipt_id = _receipt_identity(self.identity_payload())
        if self.receipt_id != expected_receipt_id:
            raise RecoveryActivationContractError(
                "authority receipt_id does not match canonical content"
            )

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "record_type": "recovery_authority_receipt",
            "request_id": self.request_id,
            "decision_id": self.decision_id,
            "decision_sha256": self.decision_sha256,
            "recovery_ticket_id": self.recovery_ticket_id,
            "requester_id": self.requester_id,
            "reviewer_id": self.reviewer_id,
            "reviewer_role": self.reviewer_role,
            "approved_bundle_id": self.approved_bundle_id,
            "expected_live_generation_id": self.expected_live_generation_id,
            "target_generation_id": self.target_generation_id,
            "approved_at": self.approved_at,
            "expires_at": self.expires_at,
        }

    def to_dict(self) -> dict[str, object]:
        value = self.identity_payload()
        value["receipt_id"] = self.receipt_id
        return value

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        decision_id: str,
        decision_sha256: str,
        recovery_ticket_id: str,
        requester_id: str,
        reviewer_id: str,
        reviewer_role: str,
        approved_bundle_id: str,
        expected_live_generation_id: str,
        target_generation_id: str,
        approved_at: str,
        expires_at: str,
    ) -> RecoveryAuthorityReceipt:
        normalized_approved = _normalize_timestamp(approved_at, "approved_at")
        normalized_expires = _normalize_timestamp(expires_at, "expires_at")
        payload: dict[str, object] = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "record_type": "recovery_authority_receipt",
            "request_id": request_id,
            "decision_id": decision_id,
            "decision_sha256": decision_sha256,
            "recovery_ticket_id": recovery_ticket_id,
            "requester_id": requester_id,
            "reviewer_id": reviewer_id,
            "reviewer_role": reviewer_role,
            "approved_bundle_id": approved_bundle_id,
            "expected_live_generation_id": expected_live_generation_id,
            "target_generation_id": target_generation_id,
            "approved_at": normalized_approved,
            "expires_at": normalized_expires,
        }
        return cls(
            receipt_id=_receipt_identity(payload),
            request_id=request_id,
            decision_id=decision_id,
            decision_sha256=decision_sha256,
            recovery_ticket_id=recovery_ticket_id,
            requester_id=requester_id,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            approved_bundle_id=approved_bundle_id,
            expected_live_generation_id=expected_live_generation_id,
            target_generation_id=target_generation_id,
            approved_at=normalized_approved,
            expires_at=normalized_expires,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RecoveryAuthorityReceipt:
        data = _exact_mapping(
            value,
            {
                "schema_version",
                "record_type",
                "receipt_id",
                "request_id",
                "decision_id",
                "decision_sha256",
                "recovery_ticket_id",
                "requester_id",
                "reviewer_id",
                "reviewer_role",
                "approved_bundle_id",
                "expected_live_generation_id",
                "target_generation_id",
                "approved_at",
                "expires_at",
            },
            "recovery authority receipt",
        )
        if data["schema_version"] != RECEIPT_SCHEMA_VERSION:
            raise RecoveryActivationContractError("unsupported authority receipt schema")
        if data["record_type"] != "recovery_authority_receipt":
            raise RecoveryActivationContractError(
                "unexpected authority receipt record_type"
            )
        return cls(
            receipt_id=_required_string(data, "receipt_id"),
            request_id=_required_string(data, "request_id"),
            decision_id=_required_string(data, "decision_id"),
            decision_sha256=_required_string(data, "decision_sha256"),
            recovery_ticket_id=_required_string(data, "recovery_ticket_id"),
            requester_id=_required_string(data, "requester_id"),
            reviewer_id=_required_string(data, "reviewer_id"),
            reviewer_role=_required_string(data, "reviewer_role"),
            approved_bundle_id=_required_string(data, "approved_bundle_id"),
            expected_live_generation_id=_required_string(
                data,
                "expected_live_generation_id",
            ),
            target_generation_id=_required_string(data, "target_generation_id"),
            approved_at=_required_string(data, "approved_at"),
            expires_at=_required_string(data, "expires_at"),
        )


@dataclass(frozen=True, slots=True)
class RecoveryActivationPlan:
    plan_id: str
    run_id: str
    expected_live: RecoveryPointer
    target: RecoveryPointer
    rollback: RecoveryPointer
    staged_evidence: StagedRecoveryEvidence
    authority: RecoveryAuthorityReceipt
    created_at: str
    valid_until: str
    reason: str
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _validate_id(self.run_id, "run_id"))
        if self.target.generation_id == self.expected_live.generation_id:
            raise RecoveryActivationContractError(
                "target generation must differ from the expected live generation"
            )
        if self.rollback != self.expected_live:
            raise RecoveryActivationContractError(
                "rollback pointer must exactly equal the expected live pointer"
            )
        if self.authority.approved_bundle_id != self.staged_evidence.bundle_id:
            raise RecoveryActivationContractError(
                "authority receipt is bound to a different recovery bundle"
            )
        if (
            self.authority.expected_live_generation_id
            != self.expected_live.generation_id
        ):
            raise RecoveryActivationContractError(
                "authority receipt expected generation does not match the plan"
            )
        if self.authority.target_generation_id != self.target.generation_id:
            raise RecoveryActivationContractError(
                "authority receipt target generation does not match the plan"
            )
        created_at = _normalize_timestamp(self.created_at, "created_at")
        valid_until = _normalize_timestamp(self.valid_until, "valid_until")
        if _timestamp(valid_until) <= _timestamp(created_at):
            raise RecoveryActivationContractError(
                "valid_until must be later than created_at"
            )
        if _timestamp(self.authority.approved_at) > _timestamp(created_at):
            raise RecoveryActivationContractError(
                "activation plan cannot predate its authority receipt"
            )
        if _timestamp(valid_until) > _timestamp(self.authority.expires_at):
            raise RecoveryActivationContractError(
                "activation plan cannot outlive its authority receipt"
            )
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "valid_until", valid_until)
        object.__setattr__(self, "reason", _validate_text(self.reason, "reason", 4096))
        object.__setattr__(self, "metadata", _validate_metadata(self.metadata))
        expected_plan_id = _plan_identity(self.identity_payload())
        if self.plan_id != expected_plan_id:
            raise RecoveryActivationContractError(
                "activation plan_id does not match canonical content"
            )

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "record_type": "recovery_activation_plan",
            "run_id": self.run_id,
            "expected_live": self.expected_live.to_dict(),
            "target": self.target.to_dict(),
            "rollback": self.rollback.to_dict(),
            "staged_evidence": self.staged_evidence.to_dict(),
            "authority": self.authority.to_dict(),
            "created_at": self.created_at,
            "valid_until": self.valid_until,
            "reason": self.reason,
            "metadata": dict(sorted(self.metadata.items())),
        }

    def to_dict(self) -> dict[str, object]:
        value = self.identity_payload()
        value["plan_id"] = self.plan_id
        return value

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        expected_live: RecoveryPointer,
        target: RecoveryPointer,
        staged_evidence: StagedRecoveryEvidence,
        authority: RecoveryAuthorityReceipt,
        created_at: str,
        valid_until: str,
        reason: str,
        metadata: Mapping[str, str] | None = None,
    ) -> RecoveryActivationPlan:
        normalized_created = _normalize_timestamp(created_at, "created_at")
        normalized_valid = _normalize_timestamp(valid_until, "valid_until")
        normalized_metadata = _validate_metadata(metadata or {})
        payload: dict[str, object] = {
            "schema_version": PLAN_SCHEMA_VERSION,
            "record_type": "recovery_activation_plan",
            "run_id": run_id,
            "expected_live": expected_live.to_dict(),
            "target": target.to_dict(),
            "rollback": expected_live.to_dict(),
            "staged_evidence": staged_evidence.to_dict(),
            "authority": authority.to_dict(),
            "created_at": normalized_created,
            "valid_until": normalized_valid,
            "reason": reason,
            "metadata": dict(sorted(normalized_metadata.items())),
        }
        return cls(
            plan_id=_plan_identity(payload),
            run_id=run_id,
            expected_live=expected_live,
            target=target,
            rollback=expected_live,
            staged_evidence=staged_evidence,
            authority=authority,
            created_at=normalized_created,
            valid_until=normalized_valid,
            reason=reason,
            metadata=normalized_metadata,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RecoveryActivationPlan:
        data = _exact_mapping(
            value,
            {
                "schema_version",
                "record_type",
                "plan_id",
                "run_id",
                "expected_live",
                "target",
                "rollback",
                "staged_evidence",
                "authority",
                "created_at",
                "valid_until",
                "reason",
                "metadata",
            },
            "recovery activation plan",
        )
        if data["schema_version"] != PLAN_SCHEMA_VERSION:
            raise RecoveryActivationContractError("unsupported activation plan schema")
        if data["record_type"] != "recovery_activation_plan":
            raise RecoveryActivationContractError(
                "unexpected activation plan record_type"
            )
        return cls(
            plan_id=_required_string(data, "plan_id"),
            run_id=_required_string(data, "run_id"),
            expected_live=RecoveryPointer.from_dict(
                _required_mapping(data, "expected_live")
            ),
            target=RecoveryPointer.from_dict(_required_mapping(data, "target")),
            rollback=RecoveryPointer.from_dict(_required_mapping(data, "rollback")),
            staged_evidence=StagedRecoveryEvidence.from_dict(
                _required_mapping(data, "staged_evidence")
            ),
            authority=RecoveryAuthorityReceipt.from_dict(
                _required_mapping(data, "authority")
            ),
            created_at=_required_string(data, "created_at"),
            valid_until=_required_string(data, "valid_until"),
            reason=_required_string(data, "reason"),
            metadata=_required_string_mapping(data, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class RecoveryActivationRequest:
    run_id: str
    expected_live: RecoveryPointer
    target: RecoveryPointer
    staged_evidence: StagedRecoveryEvidence
    authority: RecoveryAuthorityReceipt
    created_at: str
    valid_until: str
    reason: str
    metadata: dict[str, str] = field(default_factory=dict)

    def to_plan(self) -> RecoveryActivationPlan:
        return RecoveryActivationPlan.create(
            run_id=self.run_id,
            expected_live=self.expected_live,
            target=self.target,
            staged_evidence=self.staged_evidence,
            authority=self.authority,
            created_at=self.created_at,
            valid_until=self.valid_until,
            reason=self.reason,
            metadata=self.metadata,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RecoveryActivationRequest:
        data = _exact_mapping(
            value,
            {
                "schema_version",
                "record_type",
                "run_id",
                "expected_live",
                "target",
                "staged_evidence",
                "authority",
                "created_at",
                "valid_until",
                "reason",
                "metadata",
            },
            "recovery activation request",
        )
        if data["schema_version"] != REQUEST_SCHEMA_VERSION:
            raise RecoveryActivationContractError("unsupported activation request schema")
        if data["record_type"] != "recovery_activation_request":
            raise RecoveryActivationContractError(
                "unexpected activation request record_type"
            )
        return cls(
            run_id=_required_string(data, "run_id"),
            expected_live=RecoveryPointer.from_dict(
                _required_mapping(data, "expected_live")
            ),
            target=RecoveryPointer.from_dict(_required_mapping(data, "target")),
            staged_evidence=StagedRecoveryEvidence.from_dict(
                _required_mapping(data, "staged_evidence")
            ),
            authority=RecoveryAuthorityReceipt.from_dict(
                _required_mapping(data, "authority")
            ),
            created_at=_required_string(data, "created_at"),
            valid_until=_required_string(data, "valid_until"),
            reason=_required_string(data, "reason"),
            metadata=_required_string_mapping(data, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class RecoveryPreflightObservation:
    plan_id: str
    as_of: str
    current_live: RecoveryPointer
    observed_bundle_id: str
    observed_bundle_verification_sha256: str
    observed_staged_root_uri: str
    observed_staged_audit_report_sha256: str
    observed_approval_decision_sha256: str
    observed_target_pointer_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _validate_id(self.plan_id, "plan_id"))
        object.__setattr__(self, "as_of", _normalize_timestamp(self.as_of, "as_of"))
        _validate_sha256(self.observed_bundle_id, "observed_bundle_id")
        _validate_sha256(
            self.observed_bundle_verification_sha256,
            "observed_bundle_verification_sha256",
        )
        object.__setattr__(
            self,
            "observed_staged_root_uri",
            _validate_uri(
                self.observed_staged_root_uri,
                "observed_staged_root_uri",
            ),
        )
        _validate_sha256(
            self.observed_staged_audit_report_sha256,
            "observed_staged_audit_report_sha256",
        )
        _validate_sha256(
            self.observed_approval_decision_sha256,
            "observed_approval_decision_sha256",
        )
        _validate_sha256(
            self.observed_target_pointer_sha256,
            "observed_target_pointer_sha256",
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RecoveryPreflightObservation:
        data = _exact_mapping(
            value,
            {
                "schema_version",
                "record_type",
                "plan_id",
                "as_of",
                "current_live",
                "observed_bundle_id",
                "observed_bundle_verification_sha256",
                "observed_staged_root_uri",
                "observed_staged_audit_report_sha256",
                "observed_approval_decision_sha256",
                "observed_target_pointer_sha256",
            },
            "recovery preflight observation",
        )
        if data["schema_version"] != OBSERVATION_SCHEMA_VERSION:
            raise RecoveryActivationContractError("unsupported preflight schema")
        if data["record_type"] != "recovery_preflight_observation":
            raise RecoveryActivationContractError(
                "unexpected preflight observation record_type"
            )
        return cls(
            plan_id=_required_string(data, "plan_id"),
            as_of=_required_string(data, "as_of"),
            current_live=RecoveryPointer.from_dict(
                _required_mapping(data, "current_live")
            ),
            observed_bundle_id=_required_string(data, "observed_bundle_id"),
            observed_bundle_verification_sha256=_required_string(
                data,
                "observed_bundle_verification_sha256",
            ),
            observed_staged_root_uri=_required_string(
                data,
                "observed_staged_root_uri",
            ),
            observed_staged_audit_report_sha256=_required_string(
                data,
                "observed_staged_audit_report_sha256",
            ),
            observed_approval_decision_sha256=_required_string(
                data,
                "observed_approval_decision_sha256",
            ),
            observed_target_pointer_sha256=_required_string(
                data,
                "observed_target_pointer_sha256",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "record_type": "recovery_preflight_observation",
            "plan_id": self.plan_id,
            "as_of": self.as_of,
            "current_live": self.current_live.to_dict(),
            "observed_bundle_id": self.observed_bundle_id,
            "observed_bundle_verification_sha256": (
                self.observed_bundle_verification_sha256
            ),
            "observed_staged_root_uri": self.observed_staged_root_uri,
            "observed_staged_audit_report_sha256": (
                self.observed_staged_audit_report_sha256
            ),
            "observed_approval_decision_sha256": (
                self.observed_approval_decision_sha256
            ),
            "observed_target_pointer_sha256": self.observed_target_pointer_sha256,
        }


@dataclass(frozen=True, slots=True)
class RecoveryPreflightReport:
    plan_id: str
    run_id: str
    status: str
    checked_at: str
    expected_live_generation_id: str
    target_generation_id: str
    rollback_generation_id: str
    bundle_id: str
    approval_decision_id: str
    executed: bool = False

    def __post_init__(self) -> None:
        if self.status != "READY_FOR_HUMAN_EXECUTION":
            raise RecoveryActivationContractError(
                "preflight report status must be READY_FOR_HUMAN_EXECUTION"
            )
        if self.executed:
            raise RecoveryActivationContractError(
                "preflight report cannot claim activation execution"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "post-training-rsi.recovery-preflight-report/v1",
            "record_type": "recovery_preflight_report",
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "status": self.status,
            "checked_at": self.checked_at,
            "expected_live_generation_id": self.expected_live_generation_id,
            "target_generation_id": self.target_generation_id,
            "rollback_generation_id": self.rollback_generation_id,
            "bundle_id": self.bundle_id,
            "approval_decision_id": self.approval_decision_id,
            "executed": self.executed,
        }


def canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _receipt_identity(value: Mapping[str, object]) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"recovery-receipt-{digest[:32]}"


def _plan_identity(value: Mapping[str, object]) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"recovery-plan-{digest[:32]}"


def _validate_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise RecoveryActivationContractError(f"{field_name} is not a safe identifier")
    return value


def _validate_sha256(value: str, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RecoveryActivationContractError(
            f"{field_name} must contain 64 lowercase hexadecimal characters"
        )


def _validate_uri(value: str, field_name: str) -> str:
    text = _validate_text(value, field_name, 4096)
    if "://" not in text:
        raise RecoveryActivationContractError(
            f"{field_name} must be an explicit URI"
        )
    if text.startswith(("http://", "https://")):
        raise RecoveryActivationContractError(
            f"{field_name} must not select a network destination"
        )
    return text


def _validate_text(value: str, field_name: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecoveryActivationContractError(f"{field_name} must be non-empty")
    if len(value) > max_length or any(ord(character) < 32 for character in value):
        raise RecoveryActivationContractError(f"{field_name} contains invalid text")
    return value


def _validate_metadata(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise RecoveryActivationContractError("metadata must be an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        safe_key = _validate_id(key, "metadata key")
        lowered = safe_key.lower()
        if any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS):
            raise RecoveryActivationContractError(
                f"metadata key is secret-like and forbidden: {safe_key}"
            )
        if not isinstance(item, str):
            raise RecoveryActivationContractError("metadata values must be strings")
        result[safe_key] = _validate_text(item, f"metadata.{safe_key}", 2048)
    return dict(sorted(result.items()))


def _normalize_timestamp(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecoveryActivationContractError(f"{field_name} must be a timestamp")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise RecoveryActivationContractError(
            f"{field_name} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RecoveryActivationContractError(
            f"{field_name} must include a UTC offset"
        )
    normalized = parsed.astimezone(timezone.utc).isoformat()
    return normalized.replace("+00:00", "Z")


def _timestamp(value: str) -> datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(candidate)


def _exact_mapping(
    value: Mapping[str, object],
    expected: set[str],
    label: str,
) -> dict[str, object]:
    if any(not isinstance(key, str) for key in value):
        raise RecoveryActivationContractError(f"{label} keys must be strings")
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise RecoveryActivationContractError(
            f"{label} fields mismatch: missing={missing}, unknown={unknown}"
        )
    return dict(value)


def _required_string(value: Mapping[str, object], field_name: str) -> str:
    item = value[field_name]
    if not isinstance(item, str):
        raise RecoveryActivationContractError(f"{field_name} must be a string")
    return item


def _required_mapping(
    value: Mapping[str, object],
    field_name: str,
) -> Mapping[str, object]:
    item = value[field_name]
    if not isinstance(item, Mapping):
        raise RecoveryActivationContractError(f"{field_name} must be an object")
    if any(not isinstance(key, str) for key in item):
        raise RecoveryActivationContractError(f"{field_name} keys must be strings")
    return item


def _required_string_mapping(
    value: Mapping[str, object],
    field_name: str,
) -> dict[str, str]:
    item = _required_mapping(value, field_name)
    result: dict[str, str] = {}
    for key, nested in item.items():
        if not isinstance(nested, str):
            raise RecoveryActivationContractError(
                f"{field_name} values must be strings"
            )
        result[key] = nested
    return result
