from __future__ import annotations

from dataclasses import dataclass


class AdapterError(RuntimeError):
    """Base class for provider-neutral adapter failures."""


class AdapterConfigurationError(AdapterError, ValueError):
    """Raised when a selected adapter cannot be constructed safely."""


class AdapterExecutionError(AdapterError):
    """Raised when an external process or provider request fails."""


class AdapterResultError(AdapterError):
    """Raised when an adapter result violates its exact contract."""


class AdapterIntegrityError(AdapterError):
    """Raised when result paths or artifact bytes fail integrity checks."""


@dataclass(frozen=True, slots=True)
class AdapterLifecycleFailure:
    stage: str
    message: str


class AdapterLifecycleError(AdapterError):
    """Raised when evaluation and teardown both need to be reported."""

    def __init__(
        self,
        message: str,
        *,
        failures: tuple[AdapterLifecycleFailure, ...],
    ) -> None:
        super().__init__(message)
        self.failures = failures
