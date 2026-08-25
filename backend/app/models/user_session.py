"""
Refresh session records for FlowPilot AI.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import ENUM as PgEnum, INET
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


class SessionRevokedReason(str, Enum):
    LOGOUT = "LOGOUT"
    LOGOUT_ALL = "LOGOUT_ALL"
    ROTATED = "ROTATED"
    REUSE_DETECTED = "REUSE_DETECTED"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    EXPIRED = "EXPIRED"
    EMAIL_CHANGE = "EMAIL_CHANGE"


class AuthMethod(str, Enum):
    PASSWORD = "PASSWORD"
    SAML2 = "SAML2"
    OIDC = "OIDC"


class UserSession(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "sessions"
    __table_args__ = (
        Index(
            "ix_sessions_user_revoked",
            "user_id",
            "revoked_at",
        ),
        Index(
            "ix_sessions_family_id",
            "family_id",
        ),
        Index(
            "ix_sessions_expires_at",
            "expires_at",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        doc="Groups every session descended from one login.",
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        doc="SHA-256 of the refresh secret, hex encoded.",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="Refresh TTL is 14 days, applied at issuance.",
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Advanced on each successful refresh.",
    )

    # --- Authentication moment (SEC-1 Step 1 / ARCH-16 AuthnInstant) -------
    authenticated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    # --- ARCH-16 Federation & Session Policy -------------------------------
    auth_method: Mapped[str] = mapped_column(
        PgEnum(AuthMethod, name="auth_method", create_type=False),
        nullable=False,
        server_default="PASSWORD",
    )
    idp_config_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enterprise_idp_configs.id", ondelete="SET NULL"),
        nullable=True,
    )
    idp_session_index: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
    )
    pinned_ip: Mapped[Optional[str]] = mapped_column(
        INET,
        nullable=True,
    )
    pinned_ip_prefix: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    # --- Rotation chain ----------------------------------------------------
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # --- Revocation --------------------------------------------------------
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_reason: Mapped[SessionRevokedReason | None] = mapped_column(
        PgEnum(
            SessionRevokedReason,
            name="session_revoked_reason",
            create_type=False,
        ),
        nullable=True,
    )

    # --- Device provenance -------------------------------------------------
    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
    )
    replaced_by: Mapped["UserSession | None"] = relationship(
        "UserSession",
        remote_side="UserSession.id",
        foreign_keys=[replaced_by_id],
    )