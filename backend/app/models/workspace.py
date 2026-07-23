from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean
from sqlalchemy import ForeignKey
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


class Workspace(Base, UUIDMixin, TimestampMixin):
    """
    Persistent workspace configuration owned by a single user.
    """

    __tablename__ = "workspaces"

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    workspace_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    company_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    company_logo_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Regional
    # ------------------------------------------------------------------

    timezone: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="UTC",
    )

    language: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="en",
    )

    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="USD",
    )

    date_format: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="YYYY-MM-DD",
    )

    # ------------------------------------------------------------------
    # Branding
    # ------------------------------------------------------------------

    primary_color: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="#2563EB",
    )

    secondary_color: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="#0F172A",
    )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    is_active: Mapped[bool] = mapped_column(
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
        back_populates="workspace",
        passive_deletes=True,
    )