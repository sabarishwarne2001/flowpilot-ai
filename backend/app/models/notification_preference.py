"""Campaign session 3 — which notification emails a person gets, and which limit alerts went out.

`user_notification_preferences`: one row per person per OPTIONAL category they changed; no row
means the default (on). The essential categories (security, billing) have no switch and never
get a row (`notification_preferences_service`).

`usage_limit_alerts`: one row per organization, meter, level ("NEAR" at 80%, "REACHED" at 100%),
limit and billing period, written before the alert is sent, so an alert goes out once per period
however often the hourly sweep runs and however many workers run it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UUID

from app.db.base import Base


class UserNotificationPreference(Base):
    __tablename__ = "user_notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[str] = mapped_column(String(32), primary_key=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UsageLimitAlert(Base):
    __tablename__ = "usage_limit_alerts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "limit_key", "level", "ceiling", "period_start",
            name="uq_usage_limit_alerts_once_per_period",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    limit_key: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The limit the alert was about: a plan change mid-month is a new limit, and re-arms both.
    ceiling: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
