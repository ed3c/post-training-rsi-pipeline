from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

from ..control_plane import (
    DecisionAction,
    DecisionRecord,
    DecisionSubject,
    EvidenceKind,
    EvidenceRecord,
    StopReason,
)
from ..control_plane.validation import (
    canonical_json,
    normalize_timestamp,
    validate_id,
    validate_id_tuple,
    validate_text,
)
from .contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalSampleManifest,
)
from .errors import (
    ApprovalContractError,
    ApprovalIntegrityError,
    ApprovalNotGranted,
    ApprovalState,
)
from .policy import ApprovalPolicy
from .sampling import ApprovalCandidate, build_sample_manifest
from .store import ApprovalStore, record_sha256


@dataclass(frozen=True, slots=True)
class ApprovalRequestBundle:
    request: ApprovalRequest
    sample: ApprovalSampleManifest
    request_evidence: EvidenceRecord


@dataclass(frozen=True, slots=True)
class ApprovalDecisionBundle:
    request: ApprovalRequest
    sample: ApprovalSampleManifest
    decision: ApprovalDecision
    request_evidence: EvidenceRecord
    decision_evidence: EvidenceRecord
    control_decision: DecisionRecord


@dataclass(frozen=True, slots=True)
class ApprovalStatusView:
    request_id: str
    state: ApprovalState
    request_sha256: str | None
    decided_at: str | None
    reviewer_id: str | None
    reviewer_role: str | None


class ApprovalService:
    """Create, review, and enforce immutable Dataset/Model/Harness approvals."""

    def __init__(
        self,
        *,
        store: ApprovalStore,
        policy: ApprovalPolicy,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.policy = policy
        self.clock = clock or _utc_now

    def create_request(
        self,
        *,
        run_id: str,
        iteration: int,
        subject_type: DecisionSubject,
        subject_id: str,
        candidates: tuple[ApprovalCandidate, ...],
        source_evidence_ids: tuple[str, ...],
        selection_seed: str,
        requested_at: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ApprovalRequestBundle:
        if not self.policy.requires_review(subject_type):
            raise ApprovalContractError(
                f"review is disabled for {subject_type.value}"
            )
        run_id = validate_id(run_id, "run_id")
        subject_id = validate_id(subject_id, "subject_id")
        source_evidence_ids = validate_id_tuple(
            source_evidence_ids,
            "source_evidence_ids",
        )
        if not source_evidence_ids:
            raise ApprovalContractError(
                "approval requests require source_evidence_ids"
            )
        timestamp = normalize_timestamp(requested_at or self.clock())
        expires_at = self.policy.expiration_for(timestamp)
        requested_action = self.policy.requested_action(subject_type)
        provisional_sample = build_sample_manifest(
            request_id="approval-pending",
            run_id=run_id,
            iteration=iteration,
            subject_type=subject_type,
            subject_id=subject_id,
            candidates=candidates,
            policy=self.policy,
            selection_seed=selection_seed,
            created_at=timestamp,
        )
        request_id = _request_id(
            sample=provisional_sample,
            requested_action=requested_action,
            policy_id=self.policy.policy_id,
            requested_at=timestamp,
            expires_at=expires_at,
            source_evidence_ids=source_evidence_ids,
        )
        sample = replace(provisional_sample, request_id=request_id)
        request = ApprovalRequest(
            request_id=request_id,
            run_id=run_id,
            iteration=iteration,
            subject_type=subject_type,
            subject_id=subject_id,
            requested_action=requested_action,
            policy_id=self.policy.policy_id,
            requested_at=timestamp,
            expires_at=expires_at,
            source_evidence_ids=source_evidence_ids,
            sample_uri=self.store.sample_path(request_id).as_uri(),
            sample_sha256=record_sha256(sample.to_dict()),
            sample_count=sample.selected_count,
            metadata=metadata or {},
        )
        committed = self.store.commit_request(sample=sample, request=request)
        committed_sample = self.store.load_sample(request_id)
        self._verify_request_identity(committed, committed_sample)
        return ApprovalRequestBundle(
            request=committed,
            sample=committed_sample,
            request_evidence=self._request_evidence(committed),
        )

    def review(
        self,
        *,
        request_id: str,
        expected_request_sha256: str,
        approved: bool,
        reviewer_id: str,
        reviewer_role: str,
        reason: str,
        decided_at: str | None = None,
        reviewer_evidence_ids: tuple[str, ...] = (),
        metadata: dict[str, object] | None = None,
    ) -> ApprovalDecisionBundle:
        request = self.store.load_request(request_id)
        sample = self.store.load_sample(request_id)
        self._verify_request_identity(request, sample)
        actual_request_sha256 = record_sha256(request.to_dict())
        if expected_request_sha256 != actual_request_sha256:
            raise ApprovalIntegrityError("expected request SHA-256 mismatch")
        reviewer_id = validate_id(reviewer_id, "reviewer_id")
        reviewer_role = validate_id(reviewer_role, "reviewer_role")
        if reviewer_role not in self.policy.allowed_reviewer_roles:
            raise ApprovalContractError(
                f"reviewer role {reviewer_role!r} is not authorized"
            )
        timestamp = normalize_timestamp(decided_at or self.clock())
        if _is_expired(request, timestamp):
            raise ApprovalNotGranted(
                request_id=request.request_id,
                state=ApprovalState.EXPIRED,
                reason="the review deadline passed before the decision",
            )
        reviewer_evidence_ids = validate_id_tuple(
            reviewer_evidence_ids,
            "reviewer_evidence_ids",
        )
        request_evidence = self._request_evidence(request)
        evidence_ids = tuple(
            dict.fromkeys(
                (request_evidence.evidence_id, *reviewer_evidence_ids)
            )
        )
        decision_id = _decision_id(
            request_sha256=actual_request_sha256,
            approved=approved,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            decided_at=timestamp,
        )
        decision = ApprovalDecision(
            decision_id=decision_id,
            request_id=request.request_id,
            request_sha256=actual_request_sha256,
            run_id=request.run_id,
            iteration=request.iteration,
            subject_type=request.subject_type,
            subject_id=request.subject_id,
            requested_action=request.requested_action,
            approved=approved,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            reason=validate_text(reason, "reason"),
            decided_at=timestamp,
            evidence_ids=evidence_ids,
            metadata=metadata or {},
        )
        committed = self.store.commit_decision(decision)
        decision_evidence = self._decision_evidence(committed)
        control_decision = self._control_decision(
            request=request,
            decision=committed,
            request_evidence=request_evidence,
            decision_evidence=decision_evidence,
        )
        return ApprovalDecisionBundle(
            request=request,
            sample=sample,
            decision=committed,
            request_evidence=request_evidence,
            decision_evidence=decision_evidence,
            control_decision=control_decision,
        )

    def status(
        self,
        request_id: str,
        *,
        as_of: str | None = None,
    ) -> ApprovalStatusView:
        request_id = validate_id(request_id, "request_id")
        if not self.store.has_request(request_id):
            return ApprovalStatusView(
                request_id=request_id,
                state=ApprovalState.MISSING,
                request_sha256=None,
                decided_at=None,
                reviewer_id=None,
                reviewer_role=None,
            )
        request = self.store.load_request(request_id)
        sample = self.store.load_sample(request_id)
        self._verify_request_identity(request, sample)
        request_sha256 = record_sha256(request.to_dict())
        timestamp = normalize_timestamp(as_of or self.clock())
        if self.store.has_decision(request_id):
            decision = self.store.load_decision(request_id)
            if decision.decided_at <= timestamp:
                return ApprovalStatusView(
                    request_id=request_id,
                    state=(
                        ApprovalState.APPROVED
                        if decision.approved
                        else ApprovalState.DENIED
                    ),
                    request_sha256=request_sha256,
                    decided_at=decision.decided_at,
                    reviewer_id=decision.reviewer_id,
                    reviewer_role=decision.reviewer_role,
                )
        state = (
            ApprovalState.EXPIRED
            if _is_expired(request, timestamp)
            else ApprovalState.PENDING
        )
        return ApprovalStatusView(
            request_id=request_id,
            state=state,
            request_sha256=request_sha256,
            decided_at=None,
            reviewer_id=None,
            reviewer_role=None,
        )

    def require_approved(
        self,
        *,
        request_id: str,
        expected_subject_type: DecisionSubject,
        expected_subject_id: str,
        expected_action: DecisionAction,
        expected_request_sha256: str,
        as_of: str | None = None,
    ) -> ApprovalDecisionBundle:
        status = self.status(request_id, as_of=as_of)
        if status.state is not ApprovalState.APPROVED:
            raise ApprovalNotGranted(
                request_id=request_id,
                state=status.state,
                reason="an immutable approved decision is required",
            )
        request = self.store.load_request(request_id)
        if request.subject_type is not expected_subject_type:
            raise ApprovalIntegrityError("approval subject_type mismatch")
        if request.subject_id != expected_subject_id:
            raise ApprovalIntegrityError("approval subject_id mismatch")
        if request.requested_action is not expected_action:
            raise ApprovalIntegrityError("approval requested_action mismatch")
        actual_request_sha256 = record_sha256(request.to_dict())
        if actual_request_sha256 != expected_request_sha256:
            raise ApprovalIntegrityError("approval request SHA-256 mismatch")
        sample = self.store.load_sample(request_id)
        decision = self.store.load_decision(request_id)
        request_evidence = self._request_evidence(request)
        decision_evidence = self._decision_evidence(decision)
        return ApprovalDecisionBundle(
            request=request,
            sample=sample,
            decision=decision,
            request_evidence=request_evidence,
            decision_evidence=decision_evidence,
            control_decision=self._control_decision(
                request=request,
                decision=decision,
                request_evidence=request_evidence,
                decision_evidence=decision_evidence,
            ),
        )

    def pending_request_ids(self, *, as_of: str | None = None) -> tuple[str, ...]:
        return tuple(
            request_id
            for request_id in self.store.list_request_ids()
            if self.status(request_id, as_of=as_of).state
            is ApprovalState.PENDING
        )

    def _verify_request_identity(
        self,
        request: ApprovalRequest,
        sample: ApprovalSampleManifest,
    ) -> None:
        expected = _request_id(
            sample=sample,
            requested_action=request.requested_action,
            policy_id=request.policy_id,
            requested_at=request.requested_at,
            expires_at=request.expires_at,
            source_evidence_ids=request.source_evidence_ids,
        )
        if request.request_id != expected:
            raise ApprovalIntegrityError(
                "request_id does not match the content-addressed request"
            )

    def _request_evidence(self, request: ApprovalRequest) -> EvidenceRecord:
        request_sha256 = record_sha256(request.to_dict())
        return EvidenceRecord(
            evidence_id=f"ev.approval.request.{request_sha256[:24]}",
            run_id=request.run_id,
            iteration=request.iteration,
            kind=EvidenceKind.APPROVAL_REQUEST,
            producer="approval.service",
            uri=self.store.request_path(request.request_id).as_uri(),
            created_at=request.requested_at,
            sha256=request_sha256,
            metadata={
                "request_id": request.request_id,
                "subject_type": request.subject_type.value,
                "subject_id": request.subject_id,
                "requested_action": request.requested_action.value,
                "policy_id": request.policy_id,
                "sample_sha256": request.sample_sha256,
                "sample_count": request.sample_count,
                "expires_at": request.expires_at,
            },
        )

    def _decision_evidence(self, decision: ApprovalDecision) -> EvidenceRecord:
        decision_sha256 = record_sha256(decision.to_dict())
        return EvidenceRecord(
            evidence_id=f"ev.approval.decision.{decision_sha256[:24]}",
            run_id=decision.run_id,
            iteration=decision.iteration,
            kind=EvidenceKind.APPROVAL_DECISION,
            producer="approval.service",
            uri=self.store.decision_path(decision.request_id).as_uri(),
            created_at=decision.decided_at,
            sha256=decision_sha256,
            metadata={
                "decision_id": decision.decision_id,
                "request_id": decision.request_id,
                "request_sha256": decision.request_sha256,
                "approved": decision.approved,
                "reviewer_id": decision.reviewer_id,
                "reviewer_role": decision.reviewer_role,
            },
        )

    @staticmethod
    def _control_decision(
        *,
        request: ApprovalRequest,
        decision: ApprovalDecision,
        request_evidence: EvidenceRecord,
        decision_evidence: EvidenceRecord,
    ) -> DecisionRecord:
        evidence_ids = tuple(
            dict.fromkeys(
                (
                    *request.source_evidence_ids,
                    request_evidence.evidence_id,
                    *decision.evidence_ids,
                    decision_evidence.evidence_id,
                )
            )
        )
        if decision.approved:
            action = request.requested_action
            stop_reason = None
            reason_code = "approval-granted"
        else:
            action = DecisionAction.REJECT
            stop_reason = StopReason.APPROVAL_NOT_GRANTED
            reason_code = "approval-denied"
        return DecisionRecord(
            decision_id=f"control-{decision.decision_id}",
            run_id=request.run_id,
            iteration=request.iteration,
            subject_type=request.subject_type,
            subject_id=request.subject_id,
            action=action,
            reason_code=reason_code,
            reason=decision.reason,
            evidence_ids=evidence_ids,
            created_at=decision.decided_at,
            stop_reason=stop_reason,
            metadata={
                "approval_request_id": request.request_id,
                "approval_decision_id": decision.decision_id,
                "reviewer_role": decision.reviewer_role,
            },
        )


def _request_id(
    *,
    sample: ApprovalSampleManifest,
    requested_action: DecisionAction,
    policy_id: str,
    requested_at: str,
    expires_at: str | None,
    source_evidence_ids: tuple[str, ...],
) -> str:
    payload = sample.to_dict()
    payload.pop("request_id")
    digest = hashlib.sha256(
        canonical_json(
            {
                "sample": payload,
                "requested_action": requested_action.value,
                "policy_id": policy_id,
                "requested_at": requested_at,
                "expires_at": expires_at,
                "source_evidence_ids": list(source_evidence_ids),
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"approval-{sample.subject_type.value.lower()}-{digest[:24]}"


def _decision_id(
    *,
    request_sha256: str,
    approved: bool,
    reviewer_id: str,
    reviewer_role: str,
    decided_at: str,
) -> str:
    digest = hashlib.sha256(
        canonical_json(
            {
                "request_sha256": request_sha256,
                "approved": approved,
                "reviewer_id": reviewer_id,
                "reviewer_role": reviewer_role,
                "decided_at": decided_at,
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"approval-decision-{digest[:24]}"


def _is_expired(request: ApprovalRequest, timestamp: str) -> bool:
    if request.expires_at is None:
        return False
    return _parse_timestamp(timestamp) > _parse_timestamp(request.expires_at)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
