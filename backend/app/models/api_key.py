"""
Database representation of API Keys for FlowPilot AI (ARCH-08 §B.1, §B.2, §B.4).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
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
        CheckConstraint(
            "scopes <@ ARRAY["
            "'organizations:read','workspaces:read','workspaces:write',"
            "'members:read','work_items:read','work_items:write',"
            "'audit_logs:read','files:read','files:write']::text[]",
            name="ck_api_keys_scopes_allowed",
        ),
        CheckConstraint("array_length(scopes, 1) >= 1", name="ck_api_keys_scopes_not_empty"),
        CheckConstraint(
            "(previous_secret_hash IS NULL) = (previous_secret_expires_at IS NULL)",
            name="ck_api_keys_previous_secret_paired",
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
        doc="Issuer user id.",
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

    organization: Mapped["Organization"] = relationship("Organization")
    user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[user_id])