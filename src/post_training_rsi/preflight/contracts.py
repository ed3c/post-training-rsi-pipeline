from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ..audit.contracts import AuditCheck, AuditStatus, overall_status
from ..control_plane import JSONValue
from ..control_plane.validation import (
    normalize_json_object,
    normalize_timestamp,
    validate_id,
    validate_id_tuple,
    validate_sha256,
    validate_text,
)

PROVIDER_PREFLIGHT_SCHEMA_VERSION = "post-training-rsi.provider-preflight/v1"
DESTINATION_AUTHORIZATION_SCHEMA_VERSION = (
    "post-training-rsi.destination-authorization/v1"
)


class PreflightContractError(ValueError):
    """Raised when a preflight record is malformed."""


class PreflightTarget(StrEnum):
    REFERENCE = "reference"
    TEACHER = "teacher"
    TRAINING = "training"
    END_TO_END_RSI = "end-to-end-rsi"
    END_TO_END_COEVOLUTION = "end-to-end-coevolution"

    @property
    def leaves_process(self) -> bool:
        """Whether the target transmits Dataset content to an external Teacher."""
        return self is not PreflightTarget.REFERENCE

    @property
    def is_end_to_end(self) -> bool:
        return self in {
            PreflightTarget.END_TO_END_RSI,
            PreflightTarget.END_TO_END_COEVOLUTION,
        }


@dataclass(frozen=True, slots=True)
class DestinationAuthorization:
    """A human-owned receipt admitting one origin as a transmission destination.

    The receipt binds an exact configuration hash, so re-pointing the Teacher or
    editing budgets after approval invalidates it instead of silently inheriting
    a decision a reviewer made about different bytes.
    """

    authorization_id: str
    approved_by: str
    approved_at: str
    expires_at: str
    stage: str
    origin: str
    data_classes: tuple[str, ...]
    config_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "authorization_id",
            validate_id(self.authorization_id, "authorization_id"),
        )
        object.__setattr__(
            self, "approved_by", validate_text(self.approved_by, "approved_by")
        )
        object.__setattr__(self, "approved_at", normalize_timestamp(self.approved_at))
        object.__setattr__(self, "expires_at", normalize_timestamp(self.expires_at))
        object.__setattr__(self, "stage", validate_id(self.stage, "stage"))
        object.__setattr__(self, "origin", validate_text(self.origin, "origin"))
        object.__setattr__(
            self,
            "data_classes",
            validate_id_tuple(self.data_classes, "data_classes"),
        )
        if not self.data_classes:
            raise PreflightContractError("data_classes cannot be empty")
        validate_sha256(self.config_sha256)
        if _instant(self.expires_at) <= _instant(self.approved_at):
            raise PreflightContractError("expires_at must be after approved_at")

    @classmethod
    def from_mapping(cls, value: object) -> DestinationAuthorization:
        if not isinstance(value, dict):
            raise PreflightContractError("authorization must be a JSON object")
        schema = value.get("schema_version")
        if schema != DESTINATION_AUTHORIZATION_SCHEMA_VERSION:
            raise PreflightContractError(
                "authorization schema_version must be "
                f"{DESTINATION_AUTHORIZATION_SCHEMA_VERSION}"
            )
        missing = sorted(
            {
                "authorization_id",
                "approved_by",
                "approved_at",
                "expires_at",
                "stage",
                "origin",
                "data_classes",
                "config_sha256",
            }
            - set(value)
        )
        if missing:
            raise PreflightContractError(
                f"authorization is missing required fields: {', '.join(missing)}"
            )
        data_classes = value["data_classes"]
        if not isinstance(data_classes, list):
            raise PreflightContractError("data_classes must be a list")
        return cls(
            authorization_id=_text(value, "authorization_id"),
            approved_by=_text(value, "approved_by"),
            approved_at=_text(value, "approved_at"),
            expires_at=_text(value, "expires_at"),
            stage=_text(value, "stage"),
            origin=_text(value, "origin"),
            data_classes=tuple(str(item) for item in data_classes),
            config_sha256=_text(value, "config_sha256"),
        )

    def is_expired_at(self, moment: str) -> bool:
        return _instant(moment) >= _instant(self.expires_at)

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": DESTINATION_AUTHORIZATION_SCHEMA_VERSION,
            "authorization_id": self.authorization_id,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "expires_at": self.expires_at,
            "stage": self.stage,
            "origin": self.origin,
            "data_classes": list(self.data_classes),
            "config_sha256": self.config_sha256,
        }


@dataclass(frozen=True, slots=True)
class ProviderPreflightReport:
    generated_at: str
    target: PreflightTarget
    strict: bool
    status: AuditStatus
    config_sha256: str
    checks: tuple[AuditCheck, ...]
    inventory: dict[str, JSONValue]
    report_path: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "generated_at", normalize_timestamp(self.generated_at)
        )
        if not isinstance(self.target, PreflightTarget):
            object.__setattr__(self, "target", PreflightTarget(self.target))
        if not isinstance(self.strict, bool):
            raise PreflightContractError("strict must be a boolean")
        if not isinstance(self.status, AuditStatus):
            object.__setattr__(self, "status", AuditStatus(self.status))
        validate_sha256(self.config_sha256)
        if not self.checks:
            raise PreflightContractError("preflight report requires at least one check")
        object.__setattr__(
            self,
            "inventory",
            normalize_json_object(self.inventory, "inventory"),
        )
        if self.report_path is not None:
            object.__setattr__(
                self, "report_path", validate_text(self.report_path, "report_path")
            )
        calculated = overall_status(self.checks, strict=self.strict)
        if calculated is not self.status:
            raise PreflightContractError(
                f"preflight status must be derived from checks as {calculated.value}"
            )

    @property
    def exit_code(self) -> int:
        return 0 if self.status is not AuditStatus.FAIL else 2

    def with_report_path(self, report_path: str) -> ProviderPreflightReport:
        return ProviderPreflightReport(
            generated_at=self.generated_at,
            target=self.target,
            strict=self.strict,
            status=self.status,
            config_sha256=self.config_sha256,
            checks=self.checks,
            inventory=self.inventory,
            report_path=report_path,
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "schema_version": PROVIDER_PREFLIGHT_SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "target": self.target.value,
            "strict": self.strict,
            "status": self.status.value,
            "config_sha256": self.config_sha256,
            "checks": [check.to_dict() for check in self.checks],
            "inventory": dict(self.inventory),
            "report_path": self.report_path,
        }


def _text(value: dict[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str):
        raise PreflightContractError(f"{key} must be a string")
    return item


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
