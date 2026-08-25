"""ARCH-16 — enterprise identity models."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    ARRAY, Boolean, CHAR, CheckConstraint, Column, DateTime, Enum, ForeignKey,
    Index, Integer, LargeBinary, PrimaryKeyConstraint, Text, UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CIDR, INET, JSONB, UUID

from app.db.base import Base

TBL_JOBS = "jobs"
TBL_SESSIONS = "sessions"


class DomainStatus(str, enum.Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    GRACE = "GRACE"
    LAPSED = "LAPSED"
    REVOKED = "REVOKED"


class IdpProtocol(str, enum.Enum):
    SAML2 = "SAML2"
    OIDC = "OIDC"


class JitProvisioningMode(str, enum.Enum):
    OPEN = "OPEN"
    CAPPED = "CAPPED"
    INVITE_ONLY = "INVITE_ONLY"


class AuthMethod(str, enum.Enum):
    PASSWORD = "PASSWORD"
    SAML2 = "SAML2"
    OIDC = "OIDC"


class IpPinningMode(str, enum.Enum):
    OFF = "OFF"
    PREFIX = "PREFIX"
    STRICT = "STRICT"


class AssertionOutcome(str, enum.Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED_SIGNATURE = "REJECTED_SIGNATURE"
    REJECTED_AUDIENCE = "REJECTED_AUDIENCE"
    REJECTED_DESTINATION = "REJECTED_DESTINATION"
    REJECTED_EXPIRED = "REJECTED_EXPIRED"
    REJECTED_REPLAY = "REJECTED_REPLAY"
    REJECTED_NO_AUTHN_INSTANT = "REJECTED_NO_AUTHN_INSTANT"
    REJECTED_UNSOLICITED = "REJECTED_UNSOLICITED"
    REJECTED_DOMAIN = "REJECTED_DOMAIN"
    REJECTED_SEAT_CAP = "REJECTED_SEAT_CAP"
    REJECTED_UNKNOWN = "REJECTED_UNKNOWN"


class ProvisionedVia(str, enum.Enum):
    JIT = "JIT"
    SCIM = "SCIM"
    INVITATION = "INVITATION"


_domain_status = Enum(DomainStatus, name="domain_status", create_type=False,
                      values_callable=lambda e: [m.value for m in e])
_idp_protocol = Enum(IdpProtocol, name="idp_protocol", create_type=False,
                     values_callable=lambda e: [m.value for m in e])
_jit_mode = Enum(JitProvisioningMode, name="jit_provisioning_mode",
                 create_type=False,
                 values_callable=lambda e: [m.value for m in e])
_auth_method = Enum(AuthMethod, name="auth_method", create_type=False,
                    values_callable=lambda e: [m.value for m in e])
_ip_pinning = Enum(IpPinningMode, name="ip_pinning_mode", create_type=False,
                   values_callable=lambda e: [m.value for m in e])


def _uuid_pk():
    return Column(UUID(as_uuid=True), primary_key=True,
                  server_default=func.gen_random_uuid())


def _created():
    return Column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _updated():
    return Column(DateTime(timezone=True), nullable=False,
                  server_default=func.now(), onupdate=func.now())


class VerifiedDomain(Base):
    __tablename__ = "verified_domains"

    id = _uuid_pk()
    organization_id = Column(UUID(as_uuid=True),
                             ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False)
    domain = Column(Text, nullable=False)
    status = Column(_domain_status, nullable=False, default=DomainStatus.PENDING)

    challenge_token = Column(Text, nullable=False)
    challenge_issued_at = Column(DateTime(timezone=True), nullable=False,
                                 server_default=func.now())
    challenge_expires_at = Column(DateTime(timezone=True), nullable=False)

    first_verified_at = Column(DateTime(timezone=True))
    last_checked_at = Column(DateTime(timezone=True))
    last_seen_at = Column(DateTime(timezone=True))
    grace_expires_at = Column(DateTime(timezone=True))
    consecutive_failures = Column(Integer, nullable=False, default=0)

    is_sso_binding = Column(Boolean, nullable=False, default=False)

    created_by_user_id = Column(UUID(as_uuid=True),
                                ForeignKey("users.id", ondelete="SET NULL"))
    created_at = _created()
    updated_at = _updated()

    __table_args__ = (
        UniqueConstraint("organization_id", "domain",
                         name="uq_verified_domains_org_domain"),
    )

    @property
    def provisioning_allowed(self) -> bool:
        return self.status in (DomainStatus.VERIFIED, DomainStatus.GRACE)


class EnterpriseIdpConfig(Base):
    __tablename__ = "enterprise_idp_configs"

    id = _uuid_pk()
    organization_id = Column(UUID(as_uuid=True),
                             ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False)
    verified_domain_id = Column(UUID(as_uuid=True),
                                ForeignKey("verified_domains.id", ondelete="RESTRICT"),
                                nullable=False)

    protocol = Column(_idp_protocol, nullable=False)
    display_name = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)

    # SAML
    idp_entity_id = Column(Text)
    idp_sso_url = Column(Text)
    idp_slo_url = Column(Text)
    metadata_url = Column(Text)
    metadata_fetched_at = Column(DateTime(timezone=True))
    want_assertions_signed = Column(Boolean, nullable=False, default=True)
    want_response_signed = Column(Boolean, nullable=False, default=True)
    want_assertions_encrypted = Column(Boolean, nullable=False, default=False)
    allow_unsolicited = Column(Boolean, nullable=False, default=False)
    name_id_format = Column(Text)

    # OIDC
    oidc_issuer = Column(Text)
    oidc_client_id = Column(Text)
    oidc_client_secret_encrypted = Column(LargeBinary)
    oidc_discovery_url = Column(Text)
    oidc_jwks_json = Column(JSONB)
    oidc_jwks_cached_at = Column(DateTime(timezone=True))
    oidc_authorization_endpoint = Column(Text)
    oidc_token_endpoint = Column(Text)
    oidc_jwks_uri = Column(Text)

    # JIT / Seats
    jit_provisioning_mode = Column(_jit_mode, nullable=False,
                                   default=JitProvisioningMode.CAPPED)
    jit_default_org_role = Column(Text, nullable=False, default="MEMBER")
    jit_seat_cap = Column(Integer)

    force_reauth_max_age_s = Column(Integer)

    created_by_user_id = Column(UUID(as_uuid=True),
                                ForeignKey("users.id", ondelete="SET NULL"))
    created_at = _created()
    updated_at = _updated()


class IdpSigningCertificate(Base):
    __tablename__ = "idp_signing_certificates"

    id = _uuid_pk()
    idp_config_id = Column(UUID(as_uuid=True),
                           ForeignKey("enterprise_idp_configs.id", ondelete="CASCADE"),
                           nullable=False)
    side = Column(Text, nullable=False)
    certificate_pem = Column(Text, nullable=False)
    private_key_encrypted = Column(LargeBinary)
    fingerprint_sha256 = Column(CHAR(64), nullable=False)
    not_before = Column(DateTime(timezone=True))
    not_after = Column(DateTime(timezone=True))
    is_primary = Column(Boolean, nullable=False, default=False)
    retired_at = Column(DateTime(timezone=True))
    created_at = _created()

    __table_args__ = (
        UniqueConstraint("idp_config_id", "side", "fingerprint_sha256",
                         name="uq_idp_cert_fingerprint"),
        CheckConstraint("side IN ('IDP','SP')", name="ck_idp_cert_side"),
    )

    def is_live(self, now: datetime) -> bool:
        if self.retired_at is not None:
            return False
        if self.not_before is not None and now < self.not_before:
            return False
        if self.not_after is not None and now > self.not_after:
            return False
        return True


class IdpRoleMapping(Base):
    __tablename__ = "idp_role_mappings"

    id = _uuid_pk()
    idp_config_id = Column(UUID(as_uuid=True),
                           ForeignKey("enterprise_idp_configs.id", ondelete="CASCADE"),
                           nullable=False)
    priority = Column(Integer, nullable=False)
    attribute_name = Column(Text, nullable=False)
    match_kind = Column(Text, nullable=False)
    match_value = Column(Text, nullable=False)
    organization_role = Column(Text, nullable=False)
    created_at = _created()

    __table_args__ = (
        UniqueConstraint("idp_config_id", "priority",
                         name="uq_idp_role_mapping_priority"),
        CheckConstraint("organization_role <> 'OWNER'",
                        name="ck_idp_role_mapping_not_owner"),
    )


class DirectoryIdentity(Base):
    __tablename__ = "directory_identities"

    id = _uuid_pk()
    organization_id = Column(UUID(as_uuid=True),
                             ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False)
    idp_config_id = Column(UUID(as_uuid=True),
                           ForeignKey("enterprise_idp_configs.id", ondelete="CASCADE"),
                           nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False)

    external_id = Column(Text, nullable=False)
    name_id_format = Column(Text)
    user_name = Column(Text, nullable=False)
    active = Column(Boolean, nullable=False, default=True)

    attributes = Column(JSONB, nullable=False, default=dict)

    provisioned_via = Column(Text, nullable=False)
    last_synced_at = Column(DateTime(timezone=True))
    last_login_at = Column(DateTime(timezone=True))
    deprovisioned_at = Column(DateTime(timezone=True))
    deprovision_reason = Column(Text)

    created_at = _created()
    updated_at = _updated()

    __table_args__ = (
        UniqueConstraint("idp_config_id", "external_id", name="uq_directory_external"),
        UniqueConstraint("idp_config_id", "user_id", name="uq_directory_user"),
    )


class ScimGroup(Base):
    __tablename__ = "scim_groups"

    id = _uuid_pk()
    organization_id = Column(UUID(as_uuid=True),
                             ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False)
    idp_config_id = Column(UUID(as_uuid=True),
                           ForeignKey("enterprise_idp_configs.id", ondelete="CASCADE"),
                           nullable=False)
    external_id = Column(Text)
    display_name = Column(Text, nullable=False)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="SET NULL"))
    workspace_role = Column(Text)
    created_at = _created()
    updated_at = _updated()

    __table_args__ = (
        UniqueConstraint("idp_config_id", "external_id", name="uq_scim_group_external"),
    )


class ScimGroupMember(Base):
    __tablename__ = "scim_group_members"

    group_id = Column(UUID(as_uuid=True),
                      ForeignKey("scim_groups.id", ondelete="CASCADE"),
                      nullable=False)
    identity_id = Column(UUID(as_uuid=True),
                         ForeignKey("directory_identities.id", ondelete="CASCADE"),
                         nullable=False)
    added_at = Column(DateTime(timezone=True), nullable=False,
                      server_default=func.now())

    __table_args__ = (PrimaryKeyConstraint("group_id", "identity_id"),)


class ScimApiKey(Base):
    __tablename__ = "scim_api_keys"

    id = _uuid_pk()
    organization_id = Column(UUID(as_uuid=True),
                             ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False)
    idp_config_id = Column(UUID(as_uuid=True),
                           ForeignKey("enterprise_idp_configs.id", ondelete="CASCADE"),
                           nullable=False)

    key_prefix = Column(Text, nullable=False, unique=True)
    secret_hmac = Column(LargeBinary, nullable=False)
    previous_secret_hmac = Column(LargeBinary)
    previous_secret_expires_at = Column(DateTime(timezone=True))

    display_name = Column(Text, nullable=False)
    scopes = Column(ARRAY(Text), nullable=False,
                    default=lambda: ["scim:users", "scim:groups"])

    created_by_user_id = Column(UUID(as_uuid=True),
                                ForeignKey("users.id", ondelete="SET NULL"))

    last_used_at = Column(DateTime(timezone=True))
    previous_last_used_at = Column(DateTime(timezone=True))
    last_used_ip = Column(INET)
    expires_at = Column(DateTime(timezone=True))
    revoked_at = Column(DateTime(timezone=True))
    revoked_reason = Column(Text)
    created_at = _created()

    def is_live(self, now: datetime) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        return True


class TenantSecurityPolicy(Base):
    __tablename__ = "tenant_security_policies"

    id = _uuid_pk()
    organization_id = Column(UUID(as_uuid=True),
                             ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, unique=True)

    require_sso = Column(Boolean, nullable=False, default=False)
    sso_bypass_for_owners = Column(Boolean, nullable=False, default=True)

    ip_pinning = Column(_ip_pinning, nullable=False, default=IpPinningMode.OFF)
    ip_prefix_v4 = Column(Integer, nullable=False, default=24)
    ip_prefix_v6 = Column(Integer, nullable=False, default=48)
    ip_allowlist = Column(ARRAY(CIDR))

    max_session_age_s = Column(Integer)
    idp_session_sync = Column(Boolean, nullable=False, default=False)

    updated_by_user_id = Column(UUID(as_uuid=True),
                                ForeignKey("users.id", ondelete="SET NULL"))
    created_at = _created()
    updated_at = _updated()


class SsoAuthRequest(Base):
    __tablename__ = "sso_auth_requests"

    id = _uuid_pk()
    idp_config_id = Column(UUID(as_uuid=True),
                           ForeignKey("enterprise_idp_configs.id", ondelete="CASCADE"),
                           nullable=False)
    protocol = Column(Text, nullable=False)
    request_id = Column(Text, nullable=False, unique=True)
    nonce = Column(Text)
    code_verifier_encrypted = Column(LargeBinary)
    relay_state = Column(Text)
    redirect_path = Column(Text)
    force_authn = Column(Boolean, nullable=False, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True))
    created_ip = Column(INET)
    created_at = _created()


class SsoAssertion(Base):
    __tablename__ = "sso_assertions"

    id = _uuid_pk()
    organization_id = Column(UUID(as_uuid=True),
                             ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False)
    idp_config_id = Column(UUID(as_uuid=True),
                           ForeignKey("enterprise_idp_configs.id", ondelete="CASCADE"),
                           nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    session_id = Column(UUID(as_uuid=True),
                        ForeignKey(f"{TBL_SESSIONS}.id", ondelete="SET NULL"))

    raw_payload = Column(LargeBinary)
    raw_purge_after = Column(DateTime(timezone=True), nullable=False)
    payload_digest = Column(CHAR(71), nullable=False)

    authn_instant = Column(DateTime(timezone=True))
    session_index = Column(Text)
    outcome = Column(Text, nullable=False)
    reject_reason = Column(Text)
    consumed_attributes = Column(JSONB, nullable=False, default=dict)
    source_ip = Column(INET)
    created_at = _created()


class SamlAssertionReplayGuard(Base):
    __tablename__ = "saml_assertion_replay_guard"

    assertion_id = Column(Text, primary_key=True)
    idp_config_id = Column(UUID(as_uuid=True),
                           ForeignKey("enterprise_idp_configs.id", ondelete="CASCADE"),
                           nullable=False)
    not_on_or_after = Column(DateTime(timezone=True), nullable=False)
    seen_at = Column(DateTime(timezone=True), nullable=False,
                     server_default=func.now())


__all__ = [
    "DomainStatus", "IdpProtocol", "JitProvisioningMode", "AuthMethod",
    "IpPinningMode", "AssertionOutcome", "ProvisionedVia",
    "VerifiedDomain", "EnterpriseIdpConfig", "IdpSigningCertificate",
    "IdpRoleMapping", "DirectoryIdentity", "ScimGroup", "ScimGroupMember",
    "ScimApiKey", "TenantSecurityPolicy", "SsoAuthRequest", "SsoAssertion",
    "SamlAssertionReplayGuard",
]