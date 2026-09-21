"""ARCH-40 — the workspace email override. One owner for workspace-level mail.

ARCH40-S1:email-override-model.

WHAT THIS REPLACES
==================

Before ARCH-40 there were three surfaces and no single answer to "what sender
does this email use?":

  email_settings                 workspace SMTP (ARCH-02), every column NOT NULL
  organization_email_settings    organization SMTP (ARCH-06 §B.5), all nullable
  tenant_branding.sender_domain  the tenant's own domain and its status (ARCH-25)

`app.core.smtp.resolve_smtp_config` walked the first two and consulted neither
the third. Worse, both of its production callers omitted `organization_id`, so
the organization tier never applied to a notification at all — a table with a
settings page, a service and no effect.

ARCH-40 makes `workspace_email_overrides` the owner of workspace-level email
and routes every send through `app.services.email_resolution`.
`email_settings` keeps its rows, loses its readers, and is a candidate for a
later contract migration.

WHY ANOTHER TABLE RATHER THAN WIDENING email_settings
=====================================================

The same reasoning `organization_email_settings` recorded, and for the same
reason it applies again: `email_settings` has NOT NULL on six columns, so it
cannot hold a half-finished configuration. An override is exactly the thing an
administrator saves half of — a from-address today, credentials when IT sends
them. Relaxing six NOT NULLs on a live table to admit that state would remove
the guarantee that every existing row is complete.

The conditional invariant lives in the schema here, not only in a service:
`ck_workspace_email_overrides_enabled_is_complete` binds the six fields to
`is_enabled`, so a row that claims to be sending is a row that can send.
`organization_email_settings` argued that a CHECK of this shape was too easily
drifted and left it to its single writer; with two writers now (the settings
API and the migration backfill) the constraint earns its place.

TENANT ISOLATION
================

`(workspace_id, organization_id)` references
`workspaces (id, organization_id)`. The resolver reads `organization_id` off
this row to find the organization tier, so a row naming a workspace from
another organization would be a cross-tenant read with a settings page in
front of it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User

#: Mirrors ck_workspace_email_overrides_from_address_shape. Deliberately not
#: RFC 5322: the constraint exists to refuse a value that would produce a hard
#: bounce, not to adjudicate the grammar of addressing.
ADDRESS_SQL_REGEX: str = "^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$"

ENCRYPTION_VALUES: tuple[str, ...] = ("NONE", "TLS", "SSL")

_ENCRYPTION_SQL_IN = ", ".join(f"'{value}'" for value in ENCRYPTION_VALUES)


class WorkspaceEmailOverride(Base):
    """One workspace's override of its organization's transactional email."""

    __tablename__ = "workspace_email_overrides"

    __table_args__ = (
        CheckConstraint(
            "smtp_port IS NULL OR (smtp_port BETWEEN 1 AND 65535)",
            name="ck_workspace_email_overrides_port_range",
        ),
        CheckConstraint(
            f"encryption IN ({_ENCRYPTION_SQL_IN})",
            name="ck_workspace_email_overrides_encryption_known",
        ),
        CheckConstraint(
            "is_enabled = false OR ("
            "smtp_host IS NOT NULL AND smtp_port IS NOT NULL AND "
            "smtp_username IS NOT NULL AND smtp_password_encrypted IS NOT NULL AND "
            "sender_name IS NOT NULL)",
            name="ck_workspace_email_overrides_enabled_is_complete",
        ),
        Index(
            "ix_workspace_email_overrides_organization_id",
            "organization_id",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    smtp_host: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    smtp_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    #: 512, not 255. A Fernet token of a long relay password exceeds 255 —
    #: the latent truncation bug `email_settings` carries and
    #: `organization_email_settings` documented rather than inheriting.
    smtp_password_encrypted: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )

    encryption: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="TLS"
    )

    sender_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    from_address: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    reply_to_address: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)

    updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    updated_by: Mapped[Optional["User"]] = relationship("User")

    @property
    def can_send(self) -> bool:
        """Whether this override is usable as a relay right now."""
        return bool(
            self.is_enabled
            and self.smtp_host
            and self.smtp_port
            and self.smtp_username
            and self.smtp_password_encrypted
            and self.sender_name
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<WorkspaceEmailOverride workspace={self.workspace_id} "
            f"enabled={self.is_enabled} host={self.smtp_host}>"
        )


__all__ = ["ADDRESS_SQL_REGEX", "ENCRYPTION_VALUES", "WorkspaceEmailOverride"]
