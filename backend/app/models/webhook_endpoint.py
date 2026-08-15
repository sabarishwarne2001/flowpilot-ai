"""ARCH-09 §B.3 — customer webhook endpoint registration."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.webhook_events import WEBHOOK_EVENT_TYPES
from app.db.base import Base, TimestampMixin, UUIDMixin


class WebhookEndpointStatus(str, PyEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class WebhookEndpoint(Base, UUIDMixin, TimestampMixin):
    """A customer's registered delivery target."""

    __tablename__ = "webhook_endpoints"

    __table_args__ = (
        CheckConstraint("url LIKE 'https://%'", name="https_only"),
        CheckConstraint(
            "cardinality(event_types) >= 1",
            name="event_types_non_empty",
        ),
        CheckConstraint(
            "(status = 'DISABLED'::webhook_endpoint_status) = (disabled_at IS NOT NULL)",
            name="disabled_at_matches_status",
        ),
        CheckConstraint(
            "(previous_secret_encrypted IS NULL) = (previous_secret_expires_at IS NOT NULL)",
            name="previous_secret_paired",
        ),
        Index("ix_webhook_endpoints_organization_id", "organization_id"),
        Index(
            "ix_webhook_endpoints_event_types_active",
            "event_types",
            postgresql_using="gin",
            postgresql_where=text("status = 'ACTIVE'::webhook_endpoint_status"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )

    url: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)

    status: Mapped[WebhookEndpointStatus] = mapped_column(
        Enum(
            WebhookEndpointStatus,
            name="webhook_endpoint_status",
            native_enum=True,
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text("'ACTIVE'::webhook_endpoint_status"),
    )
    disabled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disabled_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    disabled_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    previous_secret_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    previous_secret_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    secret_last_rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    @property
    def is_active(self) -> bool:
        return self.status is WebhookEndpointStatus.ACTIVE

    @property
    def is_rotating(self) -> bool:
        return self.previous_secret_encrypted is not None

    def subscribes_to(self, event_type: str) -> bool:
        return event_type in self.event_types

    def __repr__(self) -> str:
        return f"<WebhookEndpoint {self.id} org={self.organization_id} url={self.url!r}>"


def assert_vocabulary_is_subset(event_types: list[str]) -> None:
    unknown = set(event_types) - WEBHOOK_EVENT_TYPES
    if unknown:
        raise ValueError(
            f"Unknown webhook event type(s): {sorted(unknown)}. "
            f"Known: {sorted(WEBHOOK_EVENT_TYPES)}"
        )