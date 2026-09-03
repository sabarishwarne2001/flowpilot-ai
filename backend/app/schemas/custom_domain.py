"""ARCH-25 §1, §2 — custom domain DTOs.

WHERE THE LINE BETWEEN SHAPE AND POLICY SITS
============================================

This module validates SHAPE. `domain_service` validates POLICY.

Shape means: is this string a lowercase, punycode, two-or-more-label hostname
with no port, no scheme, no path, no wildcard and no trailing dot? That is a
pure function of the string, it produces a readable 422, and it belongs in a
Pydantic validator.

Policy means: is this a public suffix, a consumer mail domain, one of the
platform's own reserved hostnames, or a name another tenant already holds?
Those need the settings object, the public suffix list and the database. They
belong in the service, and ARCH-16's `assert_claimable` already implements the
first two, so `domain_service` reuses it rather than this module reimplementing
a public suffix list that would immediately begin to drift.

The split matters for one specific reason. If this module refused public
suffixes, the refusal would be a 422 with Pydantic's field-error shape, and a
tenant typing `com` would get the same response body as a tenant typing
`ai.acme.com:8443`. They are different problems and deserve different
messages: one is a typo, the other is an attempt to claim something that is
not theirs.

WHY THERE IS NO `status` FIELD ON `CustomDomainCreate`
======================================================

A claim always begins PENDING. Accepting a status from the client would mean
the API had to decide whether to honour `{"status": "VERIFIED"}` — and the
only safe answer is to ignore it, which is a field that lies. Omitting it is
the same guarantee with nothing to explain.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.custom_domain import (
    CERTIFICATE_STATUS_VALUES,
    CHALLENGE_LABEL,
    CUSTOM_DOMAIN_STATUS_VALUES,
    MAX_HOSTNAME_LENGTH,
)

CustomDomainStatus = Literal["PENDING", "VERIFIED", "FAILED", "REVOKED"]
CertificateStatus = Literal["NONE", "PENDING", "ISSUED", "FAILED", "EXPIRED"]

#: The Python mirror of HOSTNAME_SQL_REGEX in app/models/custom_domain.py.
#: verify_arch25.py G8 asserts the two agree, because a schema that admits
#: what the CHECK refuses turns a 422 into a 500 from an IntegrityError.
_HOSTNAME_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)

_IPV4_RE = re.compile(r"^[0-9.]+$")


def normalise_hostname(raw: str) -> str:
    """Reduce tenant input to the one form the Host comparison uses.

    Accepts what a person pastes out of a browser bar — `https://AI.Acme.com/`
    — and returns `ai.acme.com`, or raises ValueError.

    Every transformation here is also a rejection somewhere else. The scheme
    and path are stripped rather than refused because a paste is the common
    case and refusing it teaches nothing. A port is REFUSED rather than
    stripped, because `ai.acme.com:8443` means the tenant believes the
    platform will serve their vanity host on a non-standard port, and quietly
    dropping the port would leave them with a working row and a broken
    expectation.
    """
    value = (raw or "").strip()
    if not value:
        raise ValueError("A hostname is required.")

    if "://" in value:
        value = value.split("://", 1)[1]

    # Credentials in a pasted URL. Dropped, not refused: nobody types them on
    # purpose here, and echoing them back in an error message would put them
    # in a log.
    if "@" in value:
        value = value.rsplit("@", 1)[1]

    value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    value = value.strip().rstrip(".").lower()

    if ":" in value:
        raise ValueError(
            "A custom domain cannot include a port. FlowPilot serves vanity "
            "domains on 443 only."
        )
    if value.startswith("*"):
        raise ValueError(
            "Wildcard domains are not supported. Add each hostname you want "
            "to serve, so that every certificate names exactly what it covers."
        )
    if not value:
        raise ValueError("A hostname is required.")

    try:
        value = value.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError(f"{raw!r} is not a valid domain name.") from exc

    if len(value) > MAX_HOSTNAME_LENGTH:
        raise ValueError(
            f"A hostname cannot exceed {MAX_HOSTNAME_LENGTH} characters."
        )
    if _IPV4_RE.match(value):
        raise ValueError(
            "An IP address cannot be used as a custom domain. Certificate "
            "issuance and host resolution both require a DNS name."
        )
    if not _HOSTNAME_RE.match(value):
        raise ValueError(
            f"{raw!r} is not a valid hostname. Expected something like "
            "ai.acme.com — lowercase letters, digits and hyphens, in two or "
            "more dot-separated labels."
        )
    return value


class CustomDomainCreate(BaseModel):
    """Claim a hostname. Always lands PENDING."""

    model_config = ConfigDict(extra="forbid")

    hostname: str = Field(
        ...,
        max_length=MAX_HOSTNAME_LENGTH + 16,
        description=(
            "The vanity hostname, e.g. ai.acme.com. A scheme and path are "
            "stripped; a port or wildcard is refused."
        ),
    )

    @field_validator("hostname")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return normalise_hostname(value)


class CustomDomainPrimaryUpdate(BaseModel):
    """Designate which verified hostname the platform builds links with."""

    model_config = ConfigDict(extra="forbid")

    is_primary: bool = Field(
        ...,
        description=(
            "Exactly one hostname per organization may be primary. Setting "
            "this clears the flag on any other."
        ),
    )


class DnsChallengeInstructions(BaseModel):
    """Everything the tenant needs to type into their DNS provider.

    Rendered by the console. The record NAME is built here rather than in the
    frontend so that there is exactly one place where `CHALLENGE_LABEL` and
    the hostname are joined — the poller resolves the same string.
    """

    model_config = ConfigDict(from_attributes=True)

    record_name: str = Field(
        ...,
        description="Fully-qualified name of the TXT record to create.",
    )
    record_type: Literal["TXT"] = "TXT"
    record_value: str = Field(
        ...,
        description=(
            "The exact value. Not secret — it is published in public DNS — "
            "but unguessable, so publishing it proves control of the zone."
        ),
    )
    ttl_hint_seconds: int = Field(
        default=300,
        ge=60,
        le=86400,
        description=(
            "A suggestion, not a requirement. A short TTL makes the first "
            "verification faster; nothing breaks at a longer one."
        ),
    )
    expires_at: datetime = Field(
        ...,
        description=(
            "After this the challenge must be reissued. A challenge that "
            "never expired would let a token published years ago verify a "
            "hostname the tenant no longer controls."
        ),
    )

    @staticmethod
    def build(
        *, hostname: str, token: str, expires_at: datetime
    ) -> "DnsChallengeInstructions":
        return DnsChallengeInstructions(
            record_name=f"{CHALLENGE_LABEL}.{hostname}",
            record_value=token,
            expires_at=expires_at,
        )


class CustomDomainResponse(BaseModel):
    """One claimed hostname, as the owner's console sees it.

    `challenge_token` IS present. It is not a credential — it exists to be
    published in public DNS — and hiding it would mean a tenant who closed the
    setup dialog could never recover the value without reissuing the challenge
    and re-editing their zone.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    hostname: str
    status: CustomDomainStatus
    is_primary: bool

    challenge_token: str
    challenge_expires_at: datetime

    verified_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    last_failure_reason: Optional[str] = None
    consecutive_failures: int = 0

    certificate_status: CertificateStatus
    certificate_issued_at: Optional[datetime] = None
    certificate_expires_at: Optional[datetime] = None
    certificate_last_error: Optional[str] = None

    revoked_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class CustomDomainDetail(CustomDomainResponse):
    """A domain plus its rendered DNS instructions and derived readiness.

    `may_request_certificate` is SENT rather than recomputed in the frontend.
    ARCH-24's price-disclosure rule generalises: when the backend owns a
    threshold, shipping the boolean keeps one authority. A console that
    derived it from `status === "VERIFIED"` would show an enabled button for a
    domain the server is about to refuse, and the refusal would read as a bug.
    """

    challenge: DnsChallengeInstructions
    may_request_certificate: bool


class DomainVerificationResult(BaseModel):
    """The outcome of one challenge check.

    `resolver_failed` is separate from `verified` on purpose. "We could not
    reach a resolver" is our failure and must not increment the tenant's
    failure count or move the domain to FAILED; "the record is not there" is
    theirs. Collapsing the two produces a console that blames the customer for
    our DNS outage.
    """

    model_config = ConfigDict(from_attributes=True)

    hostname: str
    verified: bool
    status: CustomDomainStatus
    resolver_failed: bool = False
    checked_at: datetime
    detail: str = ""
    records_seen: int = Field(
        default=0,
        ge=0,
        description=(
            "How many TXT records were returned at the challenge name. Zero "
            "with verified=false and resolver_failed=false means the record "
            "has not propagated yet, which is the overwhelmingly common case "
            "and deserves different console copy from a wrong value."
        ),
    )


class CertificateStatusResponse(BaseModel):
    """TLS lifecycle for one hostname."""

    model_config = ConfigDict(from_attributes=True)

    hostname: str
    certificate_status: CertificateStatus
    certificate_issued_at: Optional[datetime] = None
    certificate_expires_at: Optional[datetime] = None
    certificate_serial: Optional[str] = None
    certificate_last_error: Optional[str] = None
    days_until_expiry: Optional[int] = Field(
        default=None,
        description=(
            "Negative when already expired. Computed server-side so the "
            "console cannot disagree with the renewal sweep about what "
            "counts as urgent."
        ),
    )


__all__ = [
    "CertificateStatus",
    "CertificateStatusResponse",
    "CustomDomainCreate",
    "CustomDomainDetail",
    "CustomDomainPrimaryUpdate",
    "CustomDomainResponse",
    "CustomDomainStatus",
    "DnsChallengeInstructions",
    "DomainVerificationResult",
    "normalise_hostname",
    "CERTIFICATE_STATUS_VALUES",
    "CUSTOM_DOMAIN_STATUS_VALUES",
]