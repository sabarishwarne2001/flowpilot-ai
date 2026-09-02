"""
Per-organization SMTP configuration for FlowPilot AI.

ARCH-06 Step 8, §B.5 Option B: a NEW table, with the workspace-level
`email_settings` table left entirely untouched.

WHY A SECOND TABLE RATHER THAN A NULLABLE workspace_id ON THE FIRST
----------------------------------------------------------------------
Option A would have relaxed `email_settings.workspace_id` to nullable and
added `organization_id` alongside it — the same expand/migrate/contract shape
Steps 3-5 applied to `notifications`. It was rejected, and the reason is
worth stating because the two situations look identical and are not:

`notifications` needed the widening because a notification row's SCOPE
genuinely varies — the same kind of object is sometimes workspace-level and
sometimes organization-level. An SMTP configuration is not one kind of object
with two scopes. The workspace row and the organization row differ in what
they are FOR: a workspace row is a team's own outbound relay for that team's
mail, and an organization row is a tenant-wide default. Collapsing them into
one table with a nullable discriminator would mean every read had to
disambiguate, and `resolve_smtp_config`'s override chain would be expressed
as ordering within one table rather than as an explicit sequence of lookups.

The decisive practical point: `email_settings` has NOT NULL on `smtp_host`,
`smtp_port`, `smtp_username`, `encrypted_password`, `sender_name`, and
`encryption`. Option A would have had to relax all six to nullable to admit a
partially-configured organization row, which would have removed the guarantee
that every existing workspace row is complete — a real loss of integrity on a
live table, in exchange for avoiding a new table. Option B costs one table
and preserves every existing constraint exactly.

WHY EVERY SMTP FIELD HERE *IS* NULLABLE, UNLIKE email_settings
------------------------------------------------------------------
This is the difference the new table buys. A row exists as soon as an
administrator opens the settings page and saves anything at all — a sender
name, a host with the credentials still to come. `email_settings` cannot
represent that state; every field is required, so a row is either complete or
absent, and a half-finished configuration is lost on navigation.

The invariant that actually matters is not "every column is populated" but
"a row that is ENABLED is complete enough to send". That is enforced in
`organization_email_settings_service.set_settings` rather than by NOT NULL,
because it is a conditional invariant — it binds only when `is_enabled` is
true — and PostgreSQL CHECK constraints cannot express "these six columns are
NOT NULL only when a seventh is true" without a long and easily-drifted
expression. A CHECK could be added later if the service ever grows a second
writer; today it has exactly one.

WHY encrypted_password IS String(512) AND NOT String(255)
-------------------------------------------------------------
`email_settings.encrypted_password` is `String(255)`, which is tight for
Fernet output. A Fernet token is base64 of (version + timestamp + IV +
ciphertext + HMAC) — roughly 100 bytes of overhead before the payload, so a
long relay password can approach and exceed 255 characters after encryption.
That is a latent truncation bug on the existing table, not one this file
introduces; sizing the new column at 512 means the new path does not inherit
it. Flagged in the Step 8 gate document rather than fixed here, because
widening a column on a live table is its own migration with its own review.

THE PASSWORD IS NEVER RETURNED, ANYWHERE
--------------------------------------------
`OrganizationEmailSettingsResponse` has no field for it — not masked, not
null, absent. See that schema's docstring for why omission beats masking.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.email_settings import EmailEncryption

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User


class OrganizationEmailSettings(Base, UUIDMixin, TimestampMixin):
    """
    Tenant-wide outbound SMTP configuration for one organization.

    At most one row per organization, enforced by
    `uq_organization_email_settings_organization_id`. A plain UNIQUE, not a
    partial index: unlike `email_change_requests` or `ownership_transfers`,
    there is no lifecycle here and no history to preserve — a configuration
    is current or it is edited, never superseded. One row, updated in place.

    `EmailEncryption` is REUSED from `app.models.email_settings`, not
    redeclared. The PostgreSQL type `email_encryption` already exists (ARCH-01
    renamed it from `emailencryption` in 74a07cbe5d7e), so `create_type=False`
    below binds to it. Declaring a second Python enum over the same three
    values would let the two drift and would produce a second CREATE TYPE
    attempt in the migration.
    """

    __tablename__ = "organization_email_settings"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            name="uq_organization_email_settings_organization_id",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        doc=(
            "The owning tenant. CASCADE, matching every other tenant-scoped "
            "FK in this schema: a deleted organization must not leave live "
            "SMTP credentials behind. That is a stronger argument here than "
            "elsewhere — an orphaned row holds an encrypted password for a "
            "relay someone still operates."
        ),
    )

    smtp_host: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    smtp_port: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    smtp_username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc=(
            "The SMTP AUTH identity, which is frequently not an email "
            "address — SendGrid authenticates as the literal string "
            "'apikey'. String(255) rather than an EmailStr-backed column for "
            "exactly that reason; see SMTPConfig.sender_address, which exists "
            "because deriving the From header from this value produces a hard "
            "bounce on hosted relays."
        ),
    )

    encrypted_password: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        doc=(
            "Fernet ciphertext, never plaintext, and never returned by any "
            "response schema. 512 rather than email_settings' 255 — see the "
            "module docstring on why 255 is a latent truncation bug for long "
            "relay passwords."
        ),
    )

    sender_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    sender_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc=(
            "The visible From address, distinct from smtp_username. Maps to "
            "SMTPConfig.from_email, which the workspace path leaves None. "
            "Having this column is most of the point of the organization "
            "tier: a tenant sending as 'noreply@theircompany.com' through a "
            "relay that authenticates as 'apikey' cannot express that at the "
            "workspace tier at all."
        ),
    )

    encryption: Mapped[EmailEncryption | None] = mapped_column(
        PgEnum(
            EmailEncryption,
            name="email_encryption",
            create_type=False,
        ),
        nullable=True,
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        doc=(
            "The override switch. NOT NULL and defaulting to false — a row "
            "that exists is not thereby active. This is what makes a "
            "partially-configured row safe to persist: an administrator can "
            "save a half-filled form without silently redirecting the "
            "tenant's mail through an unfinished configuration. "
            "resolve_smtp_config consults this before anything else."
        ),
    )

    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc=(
            "Who last changed the tenant's mail routing — an audit question "
            "worth answering when mail starts going somewhere unexpected. "
            "SET NULL rather than CASCADE, matching email_settings' identical "
            "column: deleting a user must not delete the organization's SMTP "
            "configuration along with them."
        ),
    )

    # ------------------------------------------------------------------
    # Relationships — unidirectional (ARCH-02 discipline)
    # ------------------------------------------------------------------
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )

    updated_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[updated_by_user_id],
    )

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------
    @property
    def is_complete(self) -> bool:
        """
        True when every field required to actually open an SMTP session is
        present.
        """
        return all(
            (
                self.smtp_host,
                self.smtp_port,
                self.smtp_username,
                self.encrypted_password,
                self.sender_name,
                self.encryption,
            )
        )
