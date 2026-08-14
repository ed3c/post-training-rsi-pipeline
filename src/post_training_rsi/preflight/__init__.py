"""Fail-closed provider admission checks that run before anything leaves the process."""

from .contracts import (
    DESTINATION_AUTHORIZATION_SCHEMA_VERSION,
    PROVIDER_PREFLIGHT_SCHEMA_VERSION,
    DestinationAuthorization,
    PreflightContractError,
    PreflightTarget,
    ProviderPreflightReport,
)
from .service import ProviderPreflight, load_authorization_file

__all__ = [
    "DESTINATION_AUTHORIZATION_SCHEMA_VERSION",
    "PROVIDER_PREFLIGHT_SCHEMA_VERSION",
    "DestinationAuthorization",
    "PreflightContractError",
    "PreflightTarget",
    "ProviderPreflight",
    "ProviderPreflightReport",
    "load_authorization_file",
]
