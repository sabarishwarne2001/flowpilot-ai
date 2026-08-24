"""
Queryable audit trail (ARCH-07 §B.1, §B.2, §B.4, ARCH-08 §B.1, §B.7, §B.9, §B.10, ARCH-12 Step 6, ARCH-15 Step 7).
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
    # ---- ARCH-15 Step 15.7a --------------------------------------------
    BILLING_ACCOUNT = "BILLING_ACCOUNT"
    SUBSCRIPTION = "SUBSCRIPTION"
    INVOICE = "INVOICE"


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
    # ---- ARCH-15 Step 15.7a --------------------------------------------
    PORTAL_SESSION_MINTED = "PORTAL_SESSION_MINTED"
    CHECKOUT_STARTED = "CHECKOUT_STARTED"
    SEATS_CHANGED = "SEATS_CHANGED"
    DUNNING_STEP_APPLIED = "DUNNING_STEP_APPLIED"


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
    """One immutable record of one tenant-scoped state change."""

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