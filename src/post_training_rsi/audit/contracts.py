from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from ..control_plane import JSONValue
from ..control_plane.validation import (
    normalize_json_object,
    normalize_timestamp,
    validate_id,
    validate_nonnegative_int,
    validate_nonnegative_number,
    validate_text,
)

COEVOLUTION_AUDIT_SCHEMA_VERSION = "post-training-rsi.coevolution-audit/v1"
COEVOLUTION_STATUS_SCHEMA_VERSION = "post-training-rsi.coevolution-status/v1"


class AuditStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class AuditContractError(ValueError):
    """Raised when an audit record is malformed."""


@dataclass(frozen=True, slots=True)
class AuditCheck:
    check_id: str
    status: AuditStatus
    subject: str
    message: str
    details: dict[str, JSONValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_id", validate_id(self.check_id, "check_id"))
        if not isinstance(self.status, AuditStatus):
            object.__setattr__(self, "status", AuditStatus(self.status))
        object.__setattr__(self, "subject", validate_text(self.subject, "subject"))
        object.__setattr__(self, "message", validate_text(self.message, "message"))
        object.__setattr__(
            self,
            "details",
            normalize_json_object(self.details, "details"),
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "check_id": self.check_id,
            "status": self.status.value,
            "subject": self.subject,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class CoEvolutionStatusView:
    run_id: str
    runtime_status: str
    state: str
    revision: int
    current_cycle: int
    completed_cycles: int
    active_checkpoint_id: str
    active_model_score: float
    active_harness_id: str
    active_harness_score: float
    latest_snapshot_id: str
    latest_transaction_id: str
    total_cost_usd: float
    pending_approval_request_id: str | None
    pending_approval_subject: str | None

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "active_checkpoint_id",
            "active_harness_id",
            "latest_snapshot_id",
            "latest_transaction_id",
        ):
            object.__setattr__(self, name, validate_id(getattr(self, name), name))
        for name in ("runtime_status", "state"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))
        for name in ("revision", "current_cycle", "completed_cycles"):
            validate_nonnegative_int(getattr(self, name), name)
        for name in (
            "active_model_score",
            "active_harness_score",
            "total_cost_usd",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AuditContractError(f"{name} must be a number")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise AuditContractError(f"{name} must be finite")
            if name != "total_cost_usd" and not 0.0 <= numeric <= 1.0:
                raise AuditContractError(f"{name} must be in [0, 1]")
            if name == "total_cost_usd":
                validate_nonnegative_number(numeric, name)
            object.__setattr__(self, name, numeric)
        for name in (
            "pending_approval_request_id",
            "pending_approval_subject",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, validate_id(value, name))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": COEVOLUTION_STATUS_SCHEMA_VERSION,
            "run_id": self.run_id,
            "runtime_status": self.runtime_status,
            "state": self.state,
            "revision": self.revision,
            "current_cycle": self.current_cycle,
            "completed_cycles": self.completed_cycles,
            "active_checkpoint_id": self.active_checkpoint_id,
            "active_model_score": self.active_model_score,
            "active_harness_id": self.active_harness_id,
            "active_harness_score": self.active_harness_score,
            "latest_snapshot_id": self.latest_snapshot_id,
            "latest_transaction_id": self.latest_transaction_id,
            "total_cost_usd": self.total_cost_usd,
            "pending_approval_request_id": self.pending_approval_request_id,
            "pending_approval_subject": self.pending_approval_subject,
        }


@dataclass(frozen=True, slots=True)
class CoEvolutionAuditReport:
    generated_at: str
    strict: bool
    status: AuditStatus
    run_id: str | None
    checks: tuple[AuditCheck, ...]
    counts: dict[str, int]
    active: dict[str, JSONValue]
    report_path: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", normalize_timestamp(self.generated_at))
        if not isinstance(self.strict, bool):
            raise AuditContractError("strict must be a boolean")
        if not isinstance(self.status, AuditStatus):
            object.__setattr__(self, "status", AuditStatus(self.status))
        if self.run_id is not None:
            object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        if not self.checks:
            raise AuditContractError("audit report requires at least one check")
        normalized_counts: dict[str, int] = {}
        for key, value in self.counts.items():
            key = validate_id(key, "counts key")
            validate_nonnegative_int(value, f"counts[{key}]")
            normalized_counts[key] = value
        object.__setattr__(self, "counts", normalized_counts)
        object.__setattr__(
            self,
            "active",
            normalize_json_object(self.active, "active"),
        )
        if self.report_path is not None:
            object.__setattr__(
                self,
                "report_path",
                validate_text(self.report_path, "report_path"),
            )
        calculated = overall_status(self.checks, strict=self.strict)
        if calculated is not self.status:
            raise AuditContractError(
                f"audit status must be derived from checks as {calculated.value}"
            )

    @property
    def exit_code(self) -> int:
        return 0 if self.status is not AuditStatus.FAIL else 2

    def with_report_path(self, report_path: str) -> CoEvolutionAuditReport:
        return CoEvolutionAuditReport(
            generated_at=self.generated_at,
            strict=self.strict,
            status=self.status,
            run_id=self.run_id,
            checks=self.checks,
            counts=self.counts,
            active=self.active,
            report_path=report_path,
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": COEVOLUTION_AUDIT_SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "strict": self.strict,
            "status": self.status.value,
            "run_id": self.run_id,
            "checks": [check.to_dict() for check in self.checks],
            "counts": dict(self.counts),
            "active": dict(self.active),
            "report_path": self.report_path,
        }


def overall_status(
    checks: tuple[AuditCheck, ...] | list[AuditCheck],
    *,
    strict: bool,
) -> AuditStatus:
    if any(check.status is AuditStatus.FAIL for check in checks):
        return AuditStatus.FAIL
    if any(check.status is AuditStatus.WARN for check in checks):
        return AuditStatus.FAIL if strict else AuditStatus.WARN
    return AuditStatus.PASS
