"""N-020 item 7 — a person's correction of one extracted field, made in the document viewer.

One row per changed field: what the record said before, what the person wrote,
who wrote it and why. The document's `extracted_entities` carries the current
value; this table is the history the viewer shows ("corrected by …, was …")
and the evidence an auditor asks for. Values are document content, so the rows
go with the document (CASCADE) and with a GDPR erasure of its content.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UUID

from app.db.base import Base, UUIDMixin


class WorkItemFieldCorrection(Base, UUIDMixin):
    __tablename__ = "work_item_field_corrections"
    __table_args__ = (
        Index(
            "ix_work_item_field_corrections_work_item_created",
            "work_item_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    field_path: Mapped[str] = mapped_column(String(200), nullable=False)
    previous_value: Mapped[Optional[Any]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    corrected_value: Mapped[Optional[Any]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    corrected_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


__all__ = ["WorkItemFieldCorrection"]
