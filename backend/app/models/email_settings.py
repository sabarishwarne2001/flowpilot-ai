from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING, Union

from sqlalchemy import Boolean, Enum as SQLEnum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


class EmailEncryption(str, enum.Enum):
    NONE = "NONE"
    TLS = "TLS"
    SSL = "SSL"


class EmailSettings(Base, UUIDMixin, TimestampMixin):
    """
    Persistent SMTP configuration owned by a single workspace.
    """
    __tablename__ = "email_settings"

    smtp_host: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    smtp_port: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

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

    encryption: Mapped[EmailEncryption] = mapped_column(
        SQLEnum(
            EmailEncryption,
            name="email_encryption",
            create_type=False,
        ),
        nullable=False,
        default=EmailEncryption.TLS,
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    updated_by_user_id: Mapped[Union[uuid.UUID, None]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    updated_by: Mapped[Union["User", None]] = relationship("User")
