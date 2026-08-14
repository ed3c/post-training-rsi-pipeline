from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..control_plane import DecisionAction, DecisionSubject
from ..control_plane.validation import normalize_timestamp, validate_id
from .errors import ApprovalContractError


@dataclass(frozen=True, slots=True)
class ApprovalPolicy:
    policy_id: str = "hitl-default-v1"
    dataset_review_required: bool = False
    checkpoint_review_required: bool = False
    harness_review_required: bool = False
    sample_rate: float = 0.01
    min_sample_items: int = 1
    max_sample_items: int = 50
    decision_ttl_seconds: int | None = 86_400
    allowed_reviewer_roles: tuple[str, ...] = (
        "researcher",
        "release-manager",
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_id",
            validate_id(self.policy_id, "policy_id"),
        )
        for name in (
            "dataset_review_required",
            "checkpoint_review_required",
            "harness_review_required",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ApprovalContractError(f"{name} must be a boolean")
        if isinstance(self.sample_rate, bool) or not isinstance(
            self.sample_rate,
            (int, float),
        ):
            raise ApprovalContractError("sample_rate must be a number")
        if not math.isfinite(float(self.sample_rate)):
            raise ApprovalContractError("sample_rate must be finite")
        if not 0.0 < float(self.sample_rate) <= 1.0:
            raise ApprovalContractError("sample_rate must be in (0, 1]")
        _positive_int(self.min_sample_items, "min_sample_items")
        _positive_int(self.max_sample_items, "max_sample_items")
        if self.min_sample_items > self.max_sample_items:
            raise ApprovalContractError(
                "min_sample_items cannot exceed max_sample_items"
            )
        if self.decision_ttl_seconds is not None:
            _positive_int(
                self.decision_ttl_seconds,
                "decision_ttl_seconds",
            )
        if not self.allowed_reviewer_roles:
            raise ApprovalContractError(
                "allowed_reviewer_roles cannot be empty"
            )
        normalized_roles = tuple(
            validate_id(role, "allowed_reviewer_roles")
            for role in self.allowed_reviewer_roles
        )
        if len(normalized_roles) != len(set(normalized_roles)):
            raise ApprovalContractError(
                "allowed_reviewer_roles must be unique"
            )
        object.__setattr__(
            self,
            "allowed_reviewer_roles",
            normalized_roles,
        )

    def requires_review(self, subject_type: DecisionSubject) -> bool:
        if subject_type is DecisionSubject.DATASET:
            return self.dataset_review_required
        if subject_type is DecisionSubject.CHECKPOINT:
            return self.checkpoint_review_required
        if subject_type is DecisionSubject.HARNESS:
            return self.harness_review_required
        raise ApprovalContractError(
            f"approval subject {subject_type.value!r} is unsupported"
        )

    def requested_action(self, subject_type: DecisionSubject) -> DecisionAction:
        if subject_type is DecisionSubject.CHECKPOINT:
            return DecisionAction.PROMOTE
        if subject_type in {DecisionSubject.DATASET, DecisionSubject.HARNESS}:
            return DecisionAction.ACCEPT
        raise ApprovalContractError(
            f"approval subject {subject_type.value!r} is unsupported"
        )

    def expiration_for(self, requested_at: str) -> str | None:
        normalized = normalize_timestamp(requested_at)
        if self.decision_ttl_seconds is None:
            return None
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        expires = parsed + timedelta(seconds=self.decision_ttl_seconds)
        return expires.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _positive_int(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ApprovalContractError(f"{field_name} must be a positive integer")
