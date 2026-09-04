"""
Queryable audit trail (ARCH-07 §B.1, §B.2, §B.4, ARCH-08, ARCH-12, ARCH-15, ARCH-20).
"""

from __future__ import annotations

import uuid
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import ENUM as PgEnum, JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

AUDIT_RESOURCE_TYPE_ENUM_NAME = "audit_resource_type"
AUDIT_ACTION_ENUM_NAME = "audit_action"
AUDIT_OUTCOME_ENUM_NAME = "audit_outcome"


class AuditOutcome(str, PyEnum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"


class AuditResourceType(str, PyEnum):
    ORGANIZATION = "ORGANIZATION"
    WORKSPACE = "WORKSPACE"
    MEMBERSHIP = "MEMBERSHIP"
    INVITATION = "INVITATION"
    OWNERSHIP_TRANSFER = "OWNERSHIP_TRANSFER"
    EMAIL_SETTINGS = "EMAIL_SETTINGS"
    UPLOADED_FILE = "UPLOADED_FILE"
    USER = "USER"
    SESSION = "SESSION"
    AUDIT_LOG = "AUDIT_LOG"
    API_KEY = "API_KEY"
    WEBHOOK_ENDPOINT = "WEBHOOK_ENDPOINT"
    SPEND_LIMIT = "SPEND_LIMIT"
    CONVERSATION = "CONVERSATION"
    BILLING_ACCOUNT = "BILLING_ACCOUNT"
    SUBSCRIPTION = "SUBSCRIPTION"
    INVOICE = "INVOICE"
    # ---- ARCH-20 -------------------------------------------------------
    COMPLIANCE_EXPORT = "COMPLIANCE_EXPORT"
    ERASED_SUBJECT = "ERASED_SUBJECT"
    RETENTION_POLICY = "RETENTION_POLICY"
    DATA_RESIDENCY = "DATA_RESIDENCY"
    # ARCH-22 — BYOK. Added to the PostgreSQL type by
    # arch22_step1_byok_vocabulary; this enum must stay in step with it.
    PROVIDER_CREDENTIAL = "PROVIDER_CREDENTIAL"
    MODEL_ROUTE = "MODEL_ROUTE"
    # ARCH-25 — white-label. Added to the PostgreSQL type by
    # arch25_step1_branding_vocabulary; this enum must stay in step with it.
    CUSTOM_DOMAIN = "CUSTOM_DOMAIN"
    TENANT_BRANDING = "TENANT_BRANDING"
    # ARCH-26 — enterprise analytics and BI egress. Added to the PostgreSQL
    # type by arch26_step1_export_vocabulary; this enum must stay in step with
    # it. verify_arch26.py G2 asserts both sides agree.
    WAREHOUSE_DESTINATION = "WAREHOUSE_DESTINATION"
    EXPORT_SCHEDULE = "EXPORT_SCHEDULE"
    EXPORT_SYNC_RUN = "EXPORT_SYNC_RUN"
    # ARCH-27 — partner marketplace and reseller tenancy. Added to the
    # PostgreSQL type by arch27_step1_partner_vocabulary; this enum must stay
    # in step with it. verify_arch27.py G2 asserts both sides agree.
    #
    # Four, not seven. MARKETPLACE_ITEM covers manifests, signatures and
    # installations: unlike ARCH-26's destination/schedule/run split, where run
    # rows arrive orders of magnitude more often and would drown the credential
    # rows, a catalog item and its versions share a lifetime and a reader.
    # `details.manifest_id` and `details.installation_id` carry the finer grain.
    PARTNER = "PARTNER"
    PARTNER_AGREEMENT = "PARTNER_AGREEMENT"
    REV_SHARE_LEDGER = "REV_SHARE_LEDGER"
    MARKETPLACE_ITEM = "MARKETPLACE_ITEM"


class AuditAction(str, PyEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    DELETED = "DELETED"
    ARCHIVED = "ARCHIVED"
    RESTORED = "RESTORED"
    ROLE_CHANGED = "ROLE_CHANGED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    REVOKED = "REVOKED"
    TRANSFERRED = "TRANSFERRED"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    EXPORTED = "EXPORTED"
    ROTATED = "ROTATED"
    ACCESSED = "ACCESSED"
    WEBHOOK_ENDPOINT_AUTO_DISABLED = "WEBHOOK_ENDPOINT_AUTO_DISABLED"
    EXCEEDED = "EXCEEDED"
    GENERATED = "GENERATED"
    PORTAL_SESSION_MINTED = "PORTAL_SESSION_MINTED"
    CHECKOUT_STARTED = "CHECKOUT_STARTED"
    SEATS_CHANGED = "SEATS_CHANGED"
    DUNNING_STEP_APPLIED = "DUNNING_STEP_APPLIED"
    # ---- ARCH-20 -------------------------------------------------------
    ERASED = "ERASED"
    RESIDENCY_CHANGED = "RESIDENCY_CHANGED"
    RETENTION_CHANGED = "RETENTION_CHANGED"
    EXPORT_REQUESTED = "EXPORT_REQUESTED"
    EXPORT_COMPLETED = "EXPORT_COMPLETED"
    PURGED = "PURGED"
    # ARCH-22 — BYOK.
    CREDENTIAL_VALIDATED = "CREDENTIAL_VALIDATED"
    FALLBACK_POLICY_CHANGED = "FALLBACK_POLICY_CHANGED"
    # ARCH-25 — white-label.
    #
    # DOMAIN_VERIFIED is the event that unlocks certificate issuance, and
    # TLS_ISSUED records a certificate now existing for a customer-controlled
    # hostname. Both are things an incident review filters on directly, which
    # is why neither is an UPDATED carrying a details payload.
    #
    # A lapsed sender domain reuses DISABLED rather than adding a fifth
    # action: the visibility invariant is carried by
    # tenant_branding.sender_domain_status = 'LAPSED', and a second
    # vocabulary for one event makes the audit log harder to read, not easier.
    DOMAIN_VERIFIED = "DOMAIN_VERIFIED"
    DOMAIN_REVOKED = "DOMAIN_REVOKED"
    TLS_ISSUED = "TLS_ISSUED"
    BRANDING_UPDATED = "BRANDING_UPDATED"
    # ARCH-26 — enterprise analytics and BI egress.
    #
    # EXPORTED is deliberately NOT reused for a warehouse push. ARCH-20 emits
    # it when an operator downloads a compliance bundle: a human pulling data
    # out under a legal obligation. A warehouse sync is a scheduled machine
    # push into infrastructure the tenant controls, and a reviewer filtering
    # EXPORTED to answer "what left by human hand?" must not have to subtract
    # several thousand cron-driven rows to get the answer.
    DESTINATION_CREATED = "DESTINATION_CREATED"
    DESTINATION_TESTED = "DESTINATION_TESTED"
    SYNC_TRIGGERED = "SYNC_TRIGGERED"
    SYNC_COMPLETED = "SYNC_COMPLETED"
    SYNC_FAILED = "SYNC_FAILED"
    # ARCH-27 — partner marketplace and reseller tenancy.
    #
    # CREATED is deliberately NOT reused for PARTNER_CREATED. A reseller tier
    # gaining standing over customer accounts is the row somebody reaches for
    # when asking "when did a third party acquire authority over these
    # tenants?", and CREATED cannot answer it without filtering out every work
    # item, webhook and API key ever made.
    #
    # TENANT_ASSIGNED is emitted on BOTH directions with `details.direction`
    # carrying which. A release is the more interesting event: a burst of
    # assign/release pairs against varying organizations is what book-scope
    # probing looks like.
    #
    # MANIFEST_INSTALLED is the highest-consequence row in the phase — a
    # tenant admitting third-party workflow code into their own automation
    # engine — and shares an action with nothing else.
    PARTNER_CREATED = "PARTNER_CREATED"
    TENANT_ASSIGNED = "TENANT_ASSIGNED"
    REV_SHARE_SETTLED = "REV_SHARE_SETTLED"
    MANIFEST_PUBLISHED = "MANIFEST_PUBLISHED"
    MANIFEST_INSTALLED = "MANIFEST_INSTALLED"


_resource_type_pg = PgEnum(
    AuditResourceType,
    name=AUDIT_RESOURCE_TYPE_ENUM_NAME,
    create_type=False,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)

_action_pg = PgEnum(
    AuditAction,
    name=AUDIT_ACTION_ENUM_NAME,
    create_type=False,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)

_outcome_pg = PgEnum(
    AuditOutcome,
    name=AUDIT_OUTCOME_ENUM_NAME,
    create_type=False,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class AuditLog(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "audit_logs"

    __table_args__ = (
        Index(
            "ix_audit_logs_organization_id_created_at_id",
            "organization_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_audit_logs_denied_organization_id_created_at",
            "organization_id",
            text("created_at DESC"),
            postgresql_where=text("outcome = 'DENIED'::audit_outcome"),
        ),
        Index(
            "ix_audit_logs_organization_id_api_key_id",
            "organization_id",
            "api_key_id",
            postgresql_where=text("api_key_id IS NOT NULL"),
        ),
        Index(
            "ix_audit_logs_organization_id_resource_type_resource_id",
            "organization_id",
            "resource_type",
            "resource_id",
        ),
        Index(
            "ix_audit_logs_organization_id_actor_id",
            "organization_id",
            "actor_id",
        ),
        Index(
            "ix_audit_logs_workspace_id",
            "workspace_id",
            postgresql_where=text("workspace_id IS NOT NULL"),
        ),
        CheckConstraint(
            "actor_id IS NULL OR api_key_id IS NULL",
            name="actor_xor_api_key",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )

    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="RESTRICT"),
        nullable=True,
    )

    resource_type: Mapped[AuditResourceType] = mapped_column(
        _resource_type_pg,
        nullable=False,
    )

    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        nullable=True,
    )

    action: Mapped[AuditAction] = mapped_column(
        _action_pg,
        nullable=False,
    )

    outcome: Mapped[AuditOutcome] = mapped_column(
        _outcome_pg,
        nullable=False,
        server_default=text("'ALLOWED'"),
    )

    details: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
    )

    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45),
        nullable=True,
    )

    user_agent: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog {self.resource_type}/{self.action} "
            f"outcome={self.outcome} org={self.organization_id} resource={self.resource_id}>"
        )
