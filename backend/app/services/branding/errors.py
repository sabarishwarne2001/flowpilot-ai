"""ARCH-25 — branding and custom domain failures.

Every class here subclasses `FlowPilotError`. Status code and error codes are
stored as class attributes and instance attributes for clean inspection by
FastAPI exception handlers.

WHY `HostNotClaimedError` IS 404 AND NOT 403
============================================

An unrecognised `Host` gets "not found", never "forbidden". 403 confirms the
hostname is known to the platform and merely off-limits, which turns the
middleware into a hostname oracle: an attacker sweeping a customer's DNS
namespace could distinguish `ai.acme.com` (a FlowPilot tenant) from
`ai.example.com` (not one) by status code alone. 404 tells them nothing.
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.exceptions import FlowPilotError


class BrandingError(FlowPilotError):
    """Base class for every ARCH-25 failure."""

    status_code: int = 400
    code: str = "BAD_REQUEST"

    def __init__(self, message: str, *, reason: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason or message
        self.detail = message
        self.details: dict[str, Any] = {}
        # Ensure status_code and code are available as instance attributes
        if not hasattr(self, "status_code"):
            self.status_code = getattr(self.__class__, "status_code", 400)
        if not hasattr(self, "code"):
            self.code = getattr(self.__class__, "code", "BAD_REQUEST")


class DomainError(BrandingError):
    """Base class for custom domain failures."""


class DomainPolicyError(DomainError):
    """The hostname may not be claimed: reserved, a public suffix, malformed."""

    status_code = 422
    code = "DOMAIN_POLICY_ERROR"


class DomainAlreadyClaimedError(DomainError):
    """Another organization already holds this hostname.

    The message deliberately does not say WHICH organization. That would turn
    a claim attempt into a lookup of who owns a hostname, which is the same
    oracle `HostNotClaimedError` avoids at the middleware.
    """

    status_code = 409
    code = "DOMAIN_ALREADY_CLAIMED"


class DomainNotFoundError(DomainError):
    status_code = 404
    code = "DOMAIN_NOT_FOUND"


class DomainLimitExceededError(DomainError):
    status_code = 409
    code = "DOMAIN_LIMIT_EXCEEDED"


class DomainVerificationError(DomainError):
    """The TXT record was absent or did not match."""

    status_code = 409
    code = "DOMAIN_VERIFICATION_ERROR"


class ResolverUnavailableError(DomainError):
    """A DNS resolver could not be reached.

    Distinct from DomainVerificationError, and 503 rather than 409, because
    this is our failure and not the tenant's. It must not increment their
    failure count, must not move their domain to FAILED, and must not produce
    console copy telling them to check their DNS.
    """

    status_code = 503
    code = "RESOLVER_UNAVAILABLE"


class CertificateRefusedError(DomainError):
    """ARCH-25 invariant 1: no certificate for an unverified domain."""

    status_code = 409
    code = "CERTIFICATE_REFUSED"


class CertificateProvisioningError(DomainError):
    """The ACME agent refused, timed out, or is not configured."""

    status_code = 502
    code = "CERTIFICATE_PROVISIONING_ERROR"


class CustomDomainsDisabledError(DomainError):
    """CUSTOM_DOMAINS_ENABLED is false on this deployment."""

    status_code = 501
    code = "CUSTOM_DOMAINS_DISABLED"


class BrandingAssetError(BrandingError):
    """An uploaded logo or favicon was rejected."""

    status_code = 400
    code = "BRANDING_ASSET_ERROR"


class CrossTenantAssetError(BrandingError):
    """ARCH-25 invariant 6.

    An attempt to point one tenant's branding at another tenant's uploaded
    object. 404 rather than 403, for the same reason as HostNotClaimedError:
    confirming the file exists is itself the leak.
    """

    status_code = 404
    code = "CROSS_TENANT_ASSET_ERROR"


class SenderDomainError(BrandingError):
    status_code = 409
    code = "SENDER_DOMAIN_ERROR"


__all__ = [
    "BrandingAssetError",
    "BrandingError",
    "CertificateProvisioningError",
    "CertificateRefusedError",
    "CrossTenantAssetError",
    "CustomDomainsDisabledError",
    "DomainAlreadyClaimedError",
    "DomainError",
    "DomainLimitExceededError",
    "DomainNotFoundError",
    "DomainPolicyError",
    "DomainVerificationError",
    "ResolverUnavailableError",
    "SenderDomainError",
]