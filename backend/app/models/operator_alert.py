"""Campaign session 4 (§C.4) — what the platform's owner is told about, and when.

`operator_alerts`: one row per thing worth an email to whoever runs the deployment (a dead job
type, a gateway event that will never be applied, a burst of server errors, a customer with two
live gateway subscriptions). A repeat raises `occurrences` on the same row (`dedupe_key`), so a
storm of failures is one email an hour, not one per failure. `last_emailed_at` records when the
owner was last told.

`server_error_minutes`: every API process adds its 5xx answers per minute here (in memory first,
flushed with the request measurements), so the alert sweep sees the whole fleet.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

OPERATOR_ALERT_KINDS: tuple[str, ...] = (
    "DEAD_JOBS",
    "DEAD_GATEWAY_EVENTS",
    "SERVER_ERRORS",
    "DUPLICATE_SUBSCRIPTION",
    # Website and payments session (C5): a chargeback, and a payment the bank reversed.
    "PAYMENT_DISPUTED",
    "PAYMENT_REVERSED",
)


class OperatorAlert(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "operator_alerts"
    __table_args__ = (
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in OPERATOR_ALERT_KINDS) + ")",
            name="kind_known",
        ),
        CheckConstraint("occurrences >= 1", name="occurrences_positive"),
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_emailed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    emailed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class ServerErrorMinute(Base):
    __tablename__ = "server_error_minutes"

    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


__all__ = ["OPERATOR_ALERT_KINDS", "OperatorAlert", "ServerErrorMinute"]
