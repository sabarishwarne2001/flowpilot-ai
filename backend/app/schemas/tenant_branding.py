"""ARCH-25 §3, §4 — tenant branding DTOs.

THE PUBLIC MANIFEST IS THE INTERESTING FILE IN THIS PHASE
=========================================================

`BrandingManifest` is served to an UNAUTHENTICATED caller, resolved purely
from an attacker-controlled `Host` header. It is the largest new surface ARCH-
25 adds, and its safety rests entirely on what it omits.

It carries no `organization_id`, no slug, no name beyond the brand string the
tenant chose to display, no member count, no plan, no domain list. An attacker
who guesses or enumerates vanity hostnames learns the same thing a visitor
learns by loading the page: what colour the login button is.

That omission is not a nice-to-have. `HostTenantMiddleware` maps a header to a
tenant; if the manifest echoed the tenant's identity, the endpoint would
become an oracle turning "is ai.acme.com a FlowPilot customer?" into "which
FlowPilot organization is ai.acme.com, and what is its id" — and an
organization id is the parameter half the platform's other endpoints take.

verify_arch25.py G12 asserts the field set of this model directly, so adding
an identifier here fails the gate rather than shipping.

PARTIAL UPDATE SEMANTICS
========================

`TenantBrandingUpdate` distinguishes "not mentioned" from "set to null" using
Pydantic's `model_fields_set`, and `branding_service.update_branding` applies
only the fields the caller actually sent. Without that, a console that renders
four colour pickers and submits all of them would erase a logo the tenant
uploaded on a different screen, and the erasure would look like a save.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.tenant_branding import (
    BRAND_TEXT_FORBIDDEN_RE,
    BRANDING_COLOR_TOKENS,
    COLOR_SCHEME_VALUES,
    HEX_COLOR_RE,
    MAX_BRAND_NAME_LENGTH,
    SENDER_DOMAIN_STATUS_VALUES,
)
from app.schemas.custom_domain import normalise_hostname

ColorScheme = Literal["LIGHT", "DARK", "SYSTEM"]
SenderDomainStatus = Literal["UNSET", "PENDING", "VERIFIED", "LAPSED"]


def validate_hex_color(value: Optional[str]) -> Optional[str]:
    """Accept `#aabbcc` and nothing else.

    Uppercase is lowercased rather than refused — a designer pastes `#1A73E8`
    out of a style guide and that is not an error, it is a different spelling
    of the same token. Everything else is refused with a message that names
    what was expected, because the alternative spellings a person reaches for
    next (`red`, `rgb(...)`, `var(--brand)`) are exactly the strings that make
    this a CSS injection surface rather than a token.
    """
    if value is None:
        return None
    candidate = value.strip().lower()
    if not candidate:
        return None
    if not HEX_COLOR_RE.match(candidate):
        raise ValueError(
            "Colours must be six-digit hex, e.g. #1a73e8. Shorthand (#abc), "
            "named colours, rgb(), hsl() and CSS variables are not accepted: "
            "branding is a fixed set of tokens, not stylesheet input."
        )
    return candidate


def validate_brand_text(value: Optional[str]) -> Optional[str]:
    """Refuse markup characters in the one free-text branding field."""
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if len(candidate) > MAX_BRAND_NAME_LENGTH:
        raise ValueError(
            f"A brand name cannot exceed {MAX_BRAND_NAME_LENGTH} characters."
        )
    if BRAND_TEXT_FORBIDDEN_RE.search(candidate):
        raise ValueError(
            "A brand name cannot contain < > \" ' & or \\. It is rendered "
            "into a page title and an email subject on an origin shared with "
            "other tenants."
        )
    return candidate


class TenantBrandingUpdate(BaseModel):
    """Partial update. Only the fields actually sent are applied.

    Assets are NOT set here. `logo_file_id` and `favicon_file_id` are written
    by the upload endpoints, which are the only code paths that can verify the
    uploaded_files row belongs to this tenant before the reference is stored.
    Accepting a file id from the client here would let an administrator point
    their branding at another tenant's uploaded object, and the FK would allow
    it — see the module docstring in app/models/tenant_branding.py on why the
    composite foreign key that would have made this structurally impossible
    was rejected.
    """

    model_config = ConfigDict(extra="forbid")

    brand_name: Optional[str] = Field(default=None, max_length=MAX_BRAND_NAME_LENGTH)
    primary_color: Optional[str] = Field(default=None, max_length=7)
    accent_color: Optional[str] = Field(default=None, max_length=7)
    background_color: Optional[str] = Field(default=None, max_length=7)
    foreground_color: Optional[str] = Field(default=None, max_length=7)
    color_scheme: Optional[ColorScheme] = None
    support_email: Optional[str] = Field(default=None, max_length=255)
    is_enabled: Optional[bool] = None

    @field_validator(
        "primary_color",
        "accent_color",
        "background_color",
        "foreground_color",
    )
    @classmethod
    def _hex(cls, value: Optional[str]) -> Optional[str]:
        return validate_hex_color(value)

    @field_validator("brand_name")
    @classmethod
    def _brand(cls, value: Optional[str]) -> Optional[str]:
        return validate_brand_text(value)

    @field_validator("support_email")
    @classmethod
    def _email(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        candidate = value.strip().lower()
        if not candidate:
            return None
        local, sep, domain = candidate.partition("@")
        if not sep or not local or "." not in domain or " " in candidate:
            raise ValueError("support_email must be a valid email address.")
        return candidate

    def applied_fields(self) -> dict[str, object]:
        """The subset the caller actually sent.

        `model_fields_set` rather than `exclude_none`, so that an explicit
        `{"accent_color": null}` clears the token while an omitted key leaves
        it alone. Those are different intents and a console needs both.
        """
        return {
            name: getattr(self, name)
            for name in self.model_fields_set
        }


class SenderDomainUpdate(BaseModel):
    """Set or clear the tenant's custom From: domain."""

    model_config = ConfigDict(extra="forbid")

    sender_domain: Optional[str] = Field(
        default=None,
        max_length=269,
        description=(
            "Null clears the configuration and returns the status to UNSET. "
            "Setting a value moves it to PENDING; mail continues to go out "
            "from the platform address until verification succeeds."
        ),
    )

    @field_validator("sender_domain")
    @classmethod
    def _hostname(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        return normalise_hostname(value)


class SenderDomainStatusResponse(BaseModel):
    """DKIM/SPF state, plus the reason mail is degraded if it is.

    `degradation_reason` is a server-computed string rather than a flag the
    console interprets. Invariant 5 requires a lapsed domain to degrade
    VISIBLY, and visibility means the tenant reads a sentence naming their
    domain — not a badge whose copy lives in a frontend switch statement that
    someone will later add a default branch to.
    """

    model_config = ConfigDict(from_attributes=True)

    sender_domain: Optional[str] = None
    sender_domain_status: SenderDomainStatus
    sender_domain_checked_at: Optional[datetime] = None
    sender_domain_last_error: Optional[str] = None
    may_send_as_tenant: bool
    degradation_reason: Optional[str] = None
    required_records: list["SenderDnsRecord"] = Field(default_factory=list)


class SenderDnsRecord(BaseModel):
    """One record the tenant must publish for their sender domain."""

    model_config = ConfigDict(from_attributes=True)

    purpose: Literal["SPF", "DKIM", "DMARC"]
    record_name: str
    record_type: Literal["TXT"] = "TXT"
    record_value: str
    present: bool = False


class TenantBrandingResponse(BaseModel):
    """Full branding state for the organization console."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID

    brand_name: Optional[str] = None
    logo_file_id: Optional[uuid.UUID] = None
    favicon_file_id: Optional[uuid.UUID] = None
    logo_url: Optional[str] = None
    favicon_url: Optional[str] = None

    primary_color: Optional[str] = None
    accent_color: Optional[str] = None
    background_color: Optional[str] = None
    foreground_color: Optional[str] = None
    color_scheme: ColorScheme = "SYSTEM"

    support_email: Optional[str] = None
    is_enabled: bool = False

    sender: SenderDomainStatusResponse

    updated_at: datetime


class BrandingManifest(BaseModel):
    """The unauthenticated, host-resolved theme payload.

    Read the module docstring before adding a field. There is deliberately no
    organization identifier of any kind in this model, and verify_arch25.py
    G12 asserts the exact field set.
    """

    model_config = ConfigDict(extra="forbid")

    brand_name: Optional[str] = None
    primary_color: Optional[str] = None
    accent_color: Optional[str] = None
    background_color: Optional[str] = None
    foreground_color: Optional[str] = None
    color_scheme: ColorScheme = "SYSTEM"
    logo_url: Optional[str] = None
    favicon_url: Optional[str] = None
    support_email: Optional[str] = None
    has_custom_branding: bool = False

    @staticmethod
    def platform_default() -> "BrandingManifest":
        """What an unbranded host gets.

        Note what this is NOT: it is not what an UNRECOGNISED host gets. An
        unmatched vanity hostname is refused by the middleware before any
        handler runs (invariant 3, no default-tenant fallback). This is the
        answer for the platform's own hostname, and for a recognised tenant
        who has not enabled branding.
        """
        return BrandingManifest(has_custom_branding=False)


SenderDomainStatusResponse.model_rebuild()


__all__ = [
    "BrandingManifest",
    "ColorScheme",
    "SenderDnsRecord",
    "SenderDomainStatus",
    "SenderDomainStatusResponse",
    "SenderDomainUpdate",
    "TenantBrandingResponse",
    "TenantBrandingUpdate",
    "validate_brand_text",
    "validate_hex_color",
    "BRANDING_COLOR_TOKENS",
    "COLOR_SCHEME_VALUES",
    "SENDER_DOMAIN_STATUS_VALUES",
]