"""
Write-once archive of settings rows discarded by the ARCH-02 cardinality
collapse.

ai_settings, email_settings, and document_settings move from UNIQUE(user_id)
to UNIQUE(workspace_id). Where a workspace held more than one row, §B.4
Option A keeps the earliest workspace ADMIN's row and this table preserves
the rest, so "my AI key vanished" has a recoverable answer rather than an
apology.

Deliberately carries NO foreign keys. An FK to users or workspaces would let
a later CASCADE delete the evidence this table exists to hold, which defeats
its only purpose. Identifiers are stored as bare UUIDs with an email snapshot
alongside, so a row remains interpretable after its subject is gone.

Operator-only. No CRUD module, no schema, no router. Nothing in the request
path reads this table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UUID

from app.db.base import Base, UUIDMixin


class SettingsMigrationArchive(Base, UUIDMixin):
    """One discarded settings row, preserved verbatim."""

    __tablename__ = "settings_migration_archive"

    settings_kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        doc="AI | EMAIL | DOCUMENT. Plain String rather than a PostgreSQL "
            "ENUM: a new type would need creating in EXPAND and dropping in "
            "downgrade() for a table no query ever filters on by kind.",
    )

    source_row_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        doc="Primary key of the deleted row. No FK — the referent is gone.",
    )
    source_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    source_user_email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Snapshot. Keeps the row readable after the account is deleted.",
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    winning_row_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        doc="The row that survived the collapse, for reconstructing the "
            "decision without re-running the migration's logic.",
    )

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        doc="The complete discarded row, column-for-column.",
    )

    migration_revision: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Alembic revision that wrote this record.",
    )
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
