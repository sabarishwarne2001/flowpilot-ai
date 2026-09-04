"""ARCH-27 — DTOs for partner tenancy, revenue share and the marketplace.

THE SHAPE OF THIS MODULE CARRIES TWO INVARIANTS
===============================================

**Invariant 4 (ZERO_BYOK transparency).** `RevShareLedgerLine.basis_class` is
a required, non-defaulted field, and `PayoutPeriodResponse` carries the three
`zero_byok_*` totals as required fields rather than optional ones. A response
model in which the ZERO_BYOK split is optional is a response model that omits
it the first time somebody constructs one by hand, and the partner then reads
a margin number that silently blends 100%-margin BYOK revenue with ordinary
margin.

**Unknown is never zero.** `supplier_cost_micros` and `margin_micros` are
`Optional[int]` on every response in this module, all the way to the wire.
`Optional` on the model and `int` with a default of 0 on the DTO is the
mechanism by which a nullable database column becomes a confident zero in a
browser, and `verify_arch27.py` G12 walks this file's AST asserting the
nullability survives.

WHY REQUEST AND RESPONSE MODELS SHARE NO BASE CLASS
===================================================

Same discipline as ARCH-26's credential invariant. `class Response(Create)`
minus a field is the economy that leaks: the day someone adds a field to the
request base it appears on the response too, silently, and no test written
before that day fails. There is no private-key field anywhere in this phase —
signing happens on the partner's own infrastructure — and the way that stays
true is that no request model has a field capable of carrying one.

WHY THE MANIFEST IS A TYPED SUBMISSION AND NOT A FREE-FORM BLOB
===============================================================

`ManifestSubmission` names `nodes` and `edges` explicitly. The alternative —
`manifest: dict[str, Any]` validated later in the service — moves the refusal
of a malformed DAG from the request boundary to a worker, hours later, in a
context where the partner who submitted it is no longer on the phone.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.partner import (
    AGREEMENT_BASIS_VALUES,
    MARKETPLACE_VISIBILITY_VALUES,
    MAX_KEY_ID_LENGTH,
    MAX_NAME_LENGTH,
    MAX_SLUG_LENGTH,
    REV_SHARE_BASIS_CLASS_VALUES,
    SIGNING_ALGORITHM_VALUES,
    UNKNOWN_COST_BASIS_POLICY_VALUES,
)

_SLUG_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
#: `\Z` and not `$`. `$` matches before a trailing newline in Python, so
#: `"acme\n"` passes a `$`-anchored check and then fails PostgreSQL's own
#: `slug = lower(slug)` semantics in a way that is painful to diagnose. This
#: is the exact anchor drift caught in ARCH-25 in-container validation.
_VERSION_RE = re.compile(r"\A[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?\Z")
_DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_BASE64_RE = re.compile(r"\A[A-Za-z0-9+/]+={0,2}\Z")

MAX_MANIFEST_NODES: int = 200
MAX_MANIFEST_EDGES: int = 400
MAX_SIGNATURE_CHARS: int = 4096
MAX_PUBLIC_KEY_CHARS: int = 8192


def _slug(value: str) -> str:
    candidate = value.strip().lower()
    if not _SLUG_RE.match(candidate):
        raise ValueError(
            "must be 1-63 characters of lowercase letters, digits and "
            "hyphens, starting and ending alphanumeric"
        )
    return candidate


# ===========================================================================
# Partner tenancy — requests
# ===========================================================================


class PartnerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., max_length=MAX_SLUG_LENGTH)
    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    owner_organization_id: uuid.UUID = Field(
        ...,
        description=(
            "The reseller's own tenant. Every partner-scoped audit row "
            "anchors to it, and it may never appear in this partner's own "
            "book of business."
        ),
    )
    billing_email: Optional[str] = Field(default=None, max_length=320)
    notes: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, value: str) -> str:
        return _slug(value)


class PartnerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=MAX_NAME_LENGTH)
    status: Optional[Literal["ACTIVE", "SUSPENDED", "TERMINATED"]] = None
    billing_email: Optional[str] = Field(default=None, max_length=320)
    notes: Optional[str] = Field(default=None, max_length=4000)


class PartnerMemberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    role: Literal["OWNER", "ADMIN", "ANALYST"] = "ANALYST"


class PartnerMemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Optional[Literal["OWNER", "ADMIN", "ANALYST"]] = None
    status: Optional[Literal["ACTIVE", "SUSPENDED"]] = None


class OrganizationAssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: uuid.UUID
    effective_from: Optional[datetime] = Field(
        default=None,
        description=(
            "Defaults to now. Rev-share counts sealed periods at or after "
            "this moment only: a partner does not earn on a tenant's history."
        ),
    )


class SigningKeyCreate(BaseModel):
    """Registers the PUBLIC half of a partner's signing key.

    There is no private-key counterpart to this model, in this file or
    anywhere else in the phase. A platform that holds the marketplace signing
    key is a platform whose admission control verifies its own signature.
    """

    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(..., min_length=1, max_length=MAX_KEY_ID_LENGTH)
    algorithm: Literal["ED25519", "RSA_PSS_SHA256"]
    public_key_pem: str = Field(..., min_length=1, max_length=MAX_PUBLIC_KEY_CHARS)

    @field_validator("public_key_pem")
    @classmethod
    def _refuse_private_key(cls, value: str) -> str:
        if "PRIVATE KEY" in value.upper():
            raise ValueError(
                "this endpoint takes the PUBLIC half of the keypair. The "
                "submitted PEM contains a private key — treat it as "
                "compromised and generate a new one."
            )
        if "BEGIN PUBLIC KEY" not in value:
            raise ValueError(
                "expected a PEM-encoded SubjectPublicKeyInfo block "
                "('-----BEGIN PUBLIC KEY-----')."
            )
        return value.strip()


class SigningKeyRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)


# ===========================================================================
# Partner tenancy — responses
# ===========================================================================


class PartnerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    slug: str
    name: str
    status: str
    owner_organization_id: uuid.UUID
    billing_email: Optional[str]
    created_at: datetime
    updated_at: datetime


class PartnerMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    partner_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    status: str
    created_at: datetime


class BookOfBusinessEntry(BaseModel):
    """One organization in a partner's book, with its display identity.

    `organization_name` and `organization_slug` are joined in rather than left
    to the client, because a book of business rendered as a column of UUIDs is
    a book of business nobody audits.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    organization_slug: str
    status: str
    effective_from: datetime
    effective_to: Optional[datetime]


class SigningKeyResponse(BaseModel):
    """Public key material and its fingerprint. No secret exists to omit."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    partner_id: uuid.UUID
    key_id: str
    algorithm: str
    fingerprint: str
    status: str
    revoked_at: Optional[datetime]
    revocation_reason: Optional[str]
    created_at: datetime


# ===========================================================================
# Revenue share — requests
# ===========================================================================


class RevShareAgreementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    basis: Literal["GROSS_MARGIN", "NET_REVENUE"] = "GROSS_MARGIN"
    share_bps: int = Field(..., ge=0, le=10_000)
    zero_byok_share_bps: Optional[int] = Field(
        default=None,
        ge=0,
        le=10_000,
        description=(
            "Optional distinct rate for ZERO_BYOK traffic, which carries 100% "
            "margin because the tenant pays the supplier directly. NULL means "
            "share_bps applies; the class stays visible in the ledger either "
            "way."
        ),
    )
    currency: str = Field(default="USD", min_length=3, max_length=3)
    minimum_payout_micros: int = Field(default=0, ge=0)
    unknown_cost_basis_policy: Literal["EXCLUDE", "FAIL"] = Field(
        default="EXCLUDE",
        description=(
            "EXCLUDE records the revenue, pays nothing on it, and surfaces "
            "the exclusion on the statement. FAIL refuses to settle the "
            "period. There is deliberately no option that treats an unknown "
            "supplier cost as zero."
        ),
    )
    effective_from: date
    effective_to: Optional[date] = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class PayoutPeriodCompute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: date
    period_end: date = Field(..., description="Last day covered, INCLUSIVE.")


class PayoutPeriodSeal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_notes: Optional[str] = Field(default=None, max_length=4000)


class PayoutPeriodMarkPaid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_reference: str = Field(..., min_length=1, max_length=200)


# ===========================================================================
# Revenue share — responses
# ===========================================================================


class RevShareAgreementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    partner_id: uuid.UUID
    name: str
    basis: str
    share_bps: int
    zero_byok_share_bps: Optional[int]
    currency: str
    minimum_payout_micros: int
    unknown_cost_basis_policy: str
    effective_from: date
    effective_to: Optional[date]
    status: str
    created_at: datetime


class RevShareLedgerLine(BaseModel):
    """One (period, organization, basis_class) line.

    `supplier_cost_micros` and `margin_micros` stay Optional all the way to
    the wire. A client that renders `null` as an em dash is telling the truth;
    a schema that defaults them to 0 makes the same client render 100% margin.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: Optional[str] = None
    basis_class: Literal["SUPPLIER_COST", "ZERO_BYOK", "UNKNOWN_COST_BASIS"]
    revenue_micros: int
    supplier_cost_micros: Optional[int]
    margin_micros: Optional[int]
    share_bps: int
    payout_micros: int
    event_count: int
    unknown_cost_basis_event_count: int
    source_rollup_ids: list[str] = Field(default_factory=list)
    cost_basis_source_mix: Optional[dict[str, int]] = None


class PayoutPeriodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    partner_id: uuid.UUID
    agreement_id: uuid.UUID
    period_start: date
    period_end: date
    status: str
    currency: str

    gross_revenue_micros: int
    supplier_cost_micros: Optional[int]
    margin_micros: Optional[int]
    payout_micros: int
    carried_forward_micros: int

    # Invariant 4 on the wire. Required, not optional: a statement that can
    # omit the ZERO_BYOK split is a statement that eventually does.
    zero_byok_revenue_micros: int
    zero_byok_margin_micros: int
    zero_byok_payout_micros: int

    excluded_revenue_micros: int
    excluded_unknown_cost_basis_event_count: int
    organization_count: int
    source_rollup_count: int

    content_digest: str
    sealed_at: Optional[datetime]
    paid_at: Optional[datetime]
    payment_reference: Optional[str]
    created_at: datetime


class PayoutStatementResponse(BaseModel):
    """A sealed period plus its lines plus a live digest re-verification.

    `digest_matches` travels from the backend and the frontend never
    recomputes the comparison. The same rule ARCH-24 applied to
    `is_trustworthy`: a threshold or a hash evaluated independently on two
    sides eventually disagrees, and the side the user is looking at is the one
    that is wrong.
    """

    model_config = ConfigDict(extra="forbid")

    period: PayoutPeriodResponse
    lines: list[RevShareLedgerLine]
    digest_matches: bool
    recomputed_digest: str


class PartnerEconomicsSummary(BaseModel):
    """Book-wide totals across sealed periods.

    Nothing here is derived from an unsealed period. An unsealed rollup can
    still move, and a summary that blends settled and unsettled figures is a
    summary that changes after a partner has read it.
    """

    model_config = ConfigDict(extra="forbid")

    partner_id: uuid.UUID
    currency: str
    organization_count: int
    sealed_period_count: int
    lifetime_revenue_micros: int
    lifetime_margin_micros: Optional[int]
    lifetime_payout_micros: int
    lifetime_zero_byok_revenue_micros: int
    lifetime_excluded_revenue_micros: int
    zero_byok_revenue_share_bps: int = Field(
        ...,
        description=(
            "ZERO_BYOK revenue as a fraction of lifetime revenue, in basis "
            "points. Surfaced as its own figure because a book that is 60% "
            "BYOK has a completely different cost profile from one that is "
            "0%, and a single blended margin percentage hides that."
        ),
    )


# ===========================================================================
# Marketplace — requests
# ===========================================================================


class ManifestNodeSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_key: str = Field(..., min_length=1, max_length=64)
    node_type: Literal["trigger", "condition", "action", "branch", "join"]
    config: dict[str, Any] = Field(default_factory=dict)


class ManifestEdgeSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_node_key: str = Field(..., min_length=1, max_length=64)
    to_node_key: str = Field(..., min_length=1, max_length=64)
    branch: Literal["default", "true", "false"] = "default"


class MarketplaceItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., max_length=MAX_SLUG_LENGTH)
    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    summary: Optional[str] = Field(default=None, max_length=500)
    category: str = Field(default="GENERAL", max_length=32)
    visibility: Literal["PUBLIC", "PARTNER_ONLY"] = "PARTNER_ONLY"

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, value: str) -> str:
        return _slug(value)


class MarketplaceItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=MAX_NAME_LENGTH)
    summary: Optional[str] = Field(default=None, max_length=500)
    category: Optional[str] = Field(default=None, max_length=32)
    status: Optional[Literal["DRAFT", "PUBLISHED", "DEPRECATED", "WITHDRAWN"]] = None
    visibility: Optional[Literal["PUBLIC", "PARTNER_ONLY"]] = None


class ManifestSubmission(BaseModel):
    """A DAG plus the signature that admits it.

    Both halves arrive together, deliberately. A two-step "upload then sign"
    flow leaves an unsigned manifest row in the catalog between the steps, and
    invariant 5 then depends on every reader remembering to check a status
    column instead of on the row being unrepresentable.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = Field(..., max_length=32)
    nodes: list[ManifestNodeSubmission] = Field(
        ..., min_length=1, max_length=MAX_MANIFEST_NODES
    )
    edges: list[ManifestEdgeSubmission] = Field(
        default_factory=list, max_length=MAX_MANIFEST_EDGES
    )
    signing_key_id: str = Field(..., min_length=1, max_length=MAX_KEY_ID_LENGTH)
    signature: str = Field(..., min_length=1, max_length=MAX_SIGNATURE_CHARS)

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        candidate = value.strip()
        if not _VERSION_RE.match(candidate):
            raise ValueError(
                "version must be semantic (MAJOR.MINOR.PATCH with an optional "
                "pre-release suffix), e.g. '1.4.0' or '2.0.0-rc.1'."
            )
        return candidate

    @field_validator("signature")
    @classmethod
    def _check_signature(cls, value: str) -> str:
        candidate = "".join(value.split())
        if not _BASE64_RE.match(candidate):
            raise ValueError(
                "signature must be standard base64 (padded). It is computed "
                "over the ASCII bytes of the manifest content digest."
            )
        return candidate


class InstallationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_id: uuid.UUID
    workspace_id: uuid.UUID = Field(
        ...,
        description=(
            "Where the manifest materialises as an automation rule. Required "
            "rather than inferred: `automation_rules.workspace_id` is NOT "
            "NULL, and picking a workspace on the tenant's behalf means "
            "third-party workflow code lands wherever the query happened to "
            "sort first."
        ),
    )
    rule_name: Optional[str] = Field(
        default=None,
        max_length=MAX_NAME_LENGTH,
        description="Overrides the catalog item's name on the created rule.",
    )
    enabled: bool = Field(
        default=False,
        description=(
            "Defaults to False. Third-party workflow code that begins firing "
            "on live documents the instant it is installed is not a default "
            "anybody chose; it is a default nobody was asked about."
        ),
    )


# ===========================================================================
# Marketplace — responses
# ===========================================================================


class ManifestSignatureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    algorithm: str
    signed_digest: str
    verified_at: datetime
    signing_key_fingerprint: Optional[str] = None
    signing_key_status: Optional[str] = None


class ManifestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    item_id: uuid.UUID
    version: str
    status: str
    content_digest: str
    node_count: int
    edge_count: int
    published_at: Optional[datetime]
    signatures: list[ManifestSignatureResponse] = Field(default_factory=list)


class ManifestDetailResponse(BaseModel):
    """A manifest with its DAG body, for the pre-install inspection view.

    The body is returned so an administrator can read what they are about to
    admit into their automation engine. `signature_verified` travels from the
    backend rather than being recomputed in the browser: a verification that
    a client performs is a verification an attacker's client can skip.
    """

    model_config = ConfigDict(extra="forbid")

    manifest: ManifestResponse
    nodes: list[ManifestNodeSubmission]
    edges: list[ManifestEdgeSubmission]
    signature_verified: bool
    verified_key_fingerprint: Optional[str]


class MarketplaceItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    partner_id: uuid.UUID
    partner_name: Optional[str] = None
    slug: str
    name: str
    summary: Optional[str]
    category: str
    status: str
    visibility: str
    latest_version: Optional[str] = None
    latest_manifest_id: Optional[uuid.UUID] = None
    installed: bool = False
    created_at: datetime


class InstallationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    organization_id: uuid.UUID
    item_id: uuid.UUID
    item_name: Optional[str] = None
    manifest_id: uuid.UUID
    manifest_version: Optional[str] = None
    verified_signature_id: uuid.UUID
    automation_rule_id: Optional[uuid.UUID]
    status: str
    installed_at: datetime


__all__ = [
    "MAX_MANIFEST_EDGES",
    "MAX_MANIFEST_NODES",
    "MAX_PUBLIC_KEY_CHARS",
    "MAX_SIGNATURE_CHARS",
    "BookOfBusinessEntry",
    "InstallationCreate",
    "InstallationResponse",
    "ManifestDetailResponse",
    "ManifestEdgeSubmission",
    "ManifestNodeSubmission",
    "ManifestResponse",
    "ManifestSignatureResponse",
    "ManifestSubmission",
    "MarketplaceItemCreate",
    "MarketplaceItemResponse",
    "MarketplaceItemUpdate",
    "OrganizationAssignmentCreate",
    "PartnerCreate",
    "PartnerEconomicsSummary",
    "PartnerMemberCreate",
    "PartnerMemberResponse",
    "PartnerMemberUpdate",
    "PartnerResponse",
    "PartnerUpdate",
    "PayoutPeriodCompute",
    "PayoutPeriodMarkPaid",
    "PayoutPeriodResponse",
    "PayoutPeriodSeal",
    "PayoutStatementResponse",
    "RevShareAgreementCreate",
    "RevShareAgreementResponse",
    "RevShareLedgerLine",
    "SigningKeyCreate",
    "SigningKeyRevoke",
]