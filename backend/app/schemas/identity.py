"""ARCH-16 — request/response schemas for the identity admin surface."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DomainClaimRequest(BaseModel):
    domain: str = Field(min_length=4, max_length=253)

    @field_validator("domain")
    @classmethod
    def _normalise(cls, v: str) -> str:
        from app.services.identity.domain_service import normalise_domain
        return normalise_domain(v)


class DomainRead(ORMModel):
    id: str
    domain: str
    status: str
    is_sso_binding: bool
    expected_txt_record: str
    challenge_expires_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_seen_at: datetime | None = None
    grace_expires_at: datetime | None = None
    provisioning_allowed: bool


class IdpConfigCreate(BaseModel):
    verified_domain_id: str
    protocol: Literal["SAML2", "OIDC"]
    display_name: str = Field(max_length=200)

    idp_entity_id: str | None = None
    idp_sso_url: str | None = None
    idp_slo_url: str | None = None
    metadata_url: str | None = None
    allow_unsolicited: bool = False
    name_id_format: str | None = None

    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_discovery_url: str | None = None

    jit_provisioning_mode: Literal["OPEN", "CAPPED", "INVITE_ONLY"] = "CAPPED"
    jit_default_org_role: Literal["ADMIN", "BILLING", "MEMBER"] = "MEMBER"
    jit_seat_cap: int | None = Field(default=None, ge=0)
    force_reauth_max_age_s: int | None = Field(default=None, ge=0)

    @field_validator("jit_default_org_role")
    @classmethod
    def _never_owner(cls, v: str) -> str:
        if v == "OWNER":
            raise ValueError(
                "An identity provider cannot grant OWNER. Ownership transfer is "
                "an explicit, audited action in FlowPilot."
            )
        return v


class IdpConfigRead(ORMModel):
    id: str
    protocol: str
    display_name: str
    is_active: bool
    idp_entity_id: str | None = None
    idp_sso_url: str | None = None
    oidc_issuer: str | None = None
    jit_provisioning_mode: str
    jit_default_org_role: str
    jit_seat_cap: int | None = None
    current_billable_seats: int
    effective_seat_cap: int | None = None


class CertificateCreate(BaseModel):
    certificate_pem: str
    side: Literal["IDP", "SP"] = "IDP"
    is_primary: bool = False


class CertificateRead(ORMModel):
    id: str
    side: str
    fingerprint_sha256: str
    not_before: datetime | None = None
    not_after: datetime | None = None
    is_primary: bool
    retired_at: datetime | None = None


class RoleMappingCreate(BaseModel):
    priority: int = Field(default=100, ge=0)
    attribute_name: str
    match_kind: Literal["EQUALS", "CONTAINS", "PREFIX"] = "EQUALS"
    match_value: str
    organization_role: Literal["ADMIN", "BILLING", "MEMBER"]


class DryRunRequest(BaseModel):
    attributes: dict[str, list[str] | str] = Field(default_factory=dict)


class DryRunResult(BaseModel):
    resolved_role: str
    would_consume_seat: bool
    current_seats: int
    seat_cap: int | None = None


class ScimKeyCreate(BaseModel):
    idp_config_id: str
    display_name: str = Field(default="SCIM", max_length=200)


class ScimKeyRead(ORMModel):
    id: str
    display_name: str
    key_prefix: str
    scopes: list[str]
    last_used_at: datetime | None = None
    previous_secret_expires_at: datetime | None = None
    previous_last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class ScimKeyIssued(BaseModel):
    id: str
    token: str
    note: str


class SecurityPolicyUpdate(BaseModel):
    require_sso: bool | None = None
    sso_bypass_for_owners: bool | None = None
    ip_pinning: Literal["OFF", "PREFIX", "STRICT"] | None = None
    ip_prefix_v4: int | None = Field(default=None, ge=8, le=32)
    ip_prefix_v6: int | None = Field(default=None, ge=32, le=128)
    ip_allowlist: list[str] | None = None
    max_session_age_s: int | None = Field(default=None, ge=300)
    idp_session_sync: bool | None = None


class SecurityPolicyRead(BaseModel):
    require_sso: bool
    sso_bypass_for_owners: bool
    ip_pinning: str
    ip_prefix_v4: int
    ip_prefix_v6: int
    ip_allowlist: list[str]
    max_session_age_s: int | None = None
    idp_session_sync: bool


class DirectoryIdentityRead(ORMModel):
    id: str
    user_name: str
    external_id: str
    active: bool
    provisioned_via: str
    last_login_at: datetime | None = None
    last_synced_at: datetime | None = None
    deprovisioned_at: datetime | None = None
    deprovision_reason: str | None = None


class SsoDiscoveryResult(BaseModel):
    sso_enabled: bool
    protocol: str | None = None
    display_name: str | None = None
    start_url: str | None = None
