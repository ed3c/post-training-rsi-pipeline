from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ApprovalError(RuntimeError):
    """Base class for fail-closed approval failures."""


class ApprovalContractError(ApprovalError, ValueError):
    """Raised when an approval record violates its exact schema."""


class ApprovalConflictError(ApprovalError):
    """Raised when immutable approval history would be overwritten."""


class ApprovalIntegrityError(ApprovalError):
    """Raised when stored approval bytes or cross-record links are invalid."""


class ApprovalState(StrEnum):
    MISSING = "MISSING"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class ApprovalNotGranted(ApprovalError):
    request_id: str
    state: ApprovalState
    reason: str

    def __str__(self) -> str:
        return (
            f"approval {self.request_id!r} was not granted: "
            f"{self.state.value}: {self.reason}"
        )
