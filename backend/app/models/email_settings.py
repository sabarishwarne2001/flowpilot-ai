"""
Database representation of user-specific SMTP Email Settings
for FlowPilot AI.

Stores one SMTP configuration per user and is used by the
Email Service for test emails, automation emails, and future
notification providers.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship
from sqlalchemy.types import UUID

from app.db.base import Base
from app.db.base import TimestampMixin
from app.db.base import UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    

# ============================================================================
# Email Encryption
# ============================================================================


class EmailEncryption(str, enum.Enum):
    """
    Supported SMTP transport encryption.
    """

    NONE = "NONE"

    TLS = "TLS"

    SSL = "SSL"


# ============================================================================
# Email Settings
# ============================================================================


class EmailSettings(Base, UUIDMixin, TimestampMixin):
    """
    Persistent SMTP configuration owned by a single user.

    Exactly one EmailSettings record may exist per user.
    """

    __tablename__ = "email_settings"

    # ------------------------------------------------------------------
    # SMTP Server
    # ------------------------------------------------------------------

    smtp_host: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    smtp_port: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    smtp_username: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    encrypted_password: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    sender_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    encryption: Mapped[EmailEncryption] = mapped_column(
        SQLEnum(EmailEncryption),
        nullable=False,
        default=EmailEncryption.TLS,
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    user: Mapped["User"] = relationship(
        "User",
        back_populates="email_settings",
        passive_deletes=True,
    )