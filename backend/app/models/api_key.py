"""Database representation of API Keys for FlowPilot AI (ARCH-08, ARCH-21)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.public_api import ApiKeyUsageDaily
    from app.models.user import User


class ApiKey(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "api_keys"

    __table_args__ = (
        Index(
            "uq_api_keys_organization_id_name_active",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=text("deactivated_at IS NULL"),
        ),
        Index(
            "ix_api_keys_organization_id_deactivated_at",
            "organization_id",
            "deactivated_at",
        ),
        Index("ix_api_keys_user_id", "user_id"),
        Index(
            "ix_api_keys_public_api_enabled",
            "organization_id",
            "tier_key",
            postgresql_where=text("is_public_api_enabled"),
        ),
        CheckConstraint(
            "scopes <@ ARRAY["
            "'organizations:read','workspaces:read','workspaces:write',"
            "'members:read','work_items:read','work_items:write',"
            "'audit_logs:read','files:read','files:write',"
            "'webhooks:read','webhooks:write','webhooks:admin',"
            "'billing:read',"
            "'public_documents:read','public_query:write',"
            "'public_workflows:read','public_workflows:write']::text[]",
            name="ck_api_keys_scopes_allowed",
        ),
        CheckConstraint("array_length(scopes, 1) >= 1", name="ck_api_keys_scopes_not_empty"),
        CheckConstraint(
            "(previous_secret_hash IS NULL) = (previous_secret_expires_at IS NULL)",
            name="ck_api_keys_previous_secret_paired",
        ),
        # ---- ARCH-21 §3.2 --------------------------------------------------
        CheckConstraint(
            "tier_key IN ('FREE', 'BUILDER', 'PRO', 'ENTERPRISE')",
            name="ck_api_keys_tier_key_vocabulary",
        ),
        CheckConstraint(
            "rate_limit_per_minute > 0", name="ck_api_keys_rate_limit_positive"
        ),
        CheckConstraint(
            "monthly_request_quota > 0", name="ck_api_keys_monthly_quota_positive"
        ),
        CheckConstraint(
            "NOT is_public_api_enabled OR scopes && ARRAY["
            "'public_documents:read','public_query:write',"
            "'public_workflows:read','public_workflows:write']::text[]",
            name="ck_api_keys_public_enabled_requires_scope",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)

    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_reason: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    previous_secret_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    previous_secret_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ---- ARCH-21 §3.2 ------------------------------------------------------
    tier_key: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'FREE'")
    )
    rate_limit_per_minute: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("60")
    )
    monthly_request_quota: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("10000")
    )
    is_public_api_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    organization: Mapped["Organization"] = relationship("Organization")
    user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[user_id])

    usage_daily: Mapped[list["ApiKeyUsageDaily"]] = relationship(
        "ApiKeyUsageDaily",
        back_populates="api_key",
        cascade="all, delete-orphan",
        lazy="select",
    )

    @property
    def display_prefix(self) -> str:
        from app.core.api_key_secret import current_env_prefix, uuid_to_base32

        return f"{current_env_prefix()}{uuid_to_base32(self.id)[:10]}"
