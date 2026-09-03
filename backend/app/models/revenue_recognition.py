"""ARCH-24 Step 2 — revenue recognition primitives.

Deferred and recognised revenue, anchored on ARCH-15's sealed invoices. These
are primitives, not an engine: ARCH-27's revenue share needs somewhere truthful
to stand, and landing the tables while the financial model is already open means
ARCH-27 does not have to reopen it.

THE TWO THINGS THE DATABASE ENFORCES THAT THIS MODULE DOES NOT
==============================================================
`recognized_revenue_within_schedule()` refuses an insert that would take total
recognised revenue above the schedule's total, or below zero. That is a trigger
rather than a CHECK because the sum spans rows and a CHECK sees only the row
being written. Over-recognition is the one error here a reader cannot catch by
eye, because every individual row looks perfectly reasonable.

`recognized_revenue_ledger_append_only()` refuses every UPDATE and DELETE. A
restatement is a new CORRECTION row carrying its own reason, never an edit to a
figure that has already been reported. Same discipline ARCH-18 applies to
supplier reconciliation, for the same reason: a silently corrected financial
history is worse than an annotated wrong one.

Nothing in this module re-implements those checks. A service that catches
IntegrityError and carries on has defeated them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

DEFAULT_CURRENCY: str = "USD"


class RevenueScheduleStatus(str, PyEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


SCHEDULE_STATUS_VALUES: tuple[str, ...] = tuple(
    status.value for status in RevenueScheduleStatus
)
_SCHEDULE_STATUS_IN = ", ".join(f"'{v}'" for v in SCHEDULE_STATUS_VALUES)


class RecognitionMethod(str, PyEnum):
    #: Spread evenly across the service period. The default for seat licences.
    RATABLE = "RATABLE"
    #: Recognised entirely at a moment. Usage overages, one-off fees.
    POINT_IN_TIME = "POINT_IN_TIME"


RECOGNITION_METHOD_VALUES: tuple[str, ...] = tuple(
    method.value for method in RecognitionMethod
)


class RecognitionReason(str, PyEnum):
    RATABLE = "RATABLE"
    POINT_IN_TIME = "POINT_IN_TIME"
    #: A period opened late and is catching up several periods at once.
    CATCH_UP = "CATCH_UP"
    #: The only reason under which a negative amount is legal.
    CORRECTION = "CORRECTION"


RECOGNITION_REASON_VALUES: tuple[str, ...] = tuple(
    reason.value for reason in RecognitionReason
)
_REASON_IN = ", ".join(f"'{v}'" for v in RECOGNITION_REASON_VALUES)


class RevenueSchedule(Base, UUIDMixin, TimestampMixin):
    """One finalized invoice's revenue, and the period it is earned over."""

    __tablename__ = "revenue_schedules"

    __table_args__ = (
        CheckConstraint(f"status IN ({_SCHEDULE_STATUS_IN})", name="status_known"),
        CheckConstraint("total_micros >= 0", name="total_non_negative"),
        CheckConstraint("length(currency) = 3", name="currency_iso4217"),
        CheckConstraint(
            "service_period_end > service_period_start",
            name="service_period_ordered",
        ),
        # One schedule per invoice. A second schedule against the same invoice
        # is precisely how the same revenue gets recognised twice, and it is
        # the failure that survives every test written against one schedule.
        Index("uq_revenue_schedules_invoice", "invoice_id", unique=True),
        Index(
            "ix_revenue_schedules_org_period",
            "organization_id",
            "service_period_start",
        ),
        Index(
            "ix_revenue_schedules_open",
            "service_period_end",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="RESTRICT"),
        nullable=False,
        doc=(
            "Must be FINALIZED. Enforced by trg_revenue_schedules_source_"
            "finalized, because a schedule built from a draft can be "
            "invalidated by the draft moving underneath it."
        ),
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'DRAFT'")
    )
    total_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text(f"'{DEFAULT_CURRENCY}'")
    )

    service_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    service_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    recognition_method: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'RATABLE'")
    )

    source_sealed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc=(
            "The invoice's finalized_at at the moment the schedule was built. "
            "Copied rather than joined so a reader can see the schedule was "
            "derived from a sealed figure without trusting that the invoice "
            "still says so."
        ),
    )

    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    entries: Mapped[list["RecognizedRevenueEntry"]] = relationship(
        "RecognizedRevenueEntry",
        back_populates="schedule",
        order_by="RecognizedRevenueEntry.period_start",
        lazy="selectin",
    )

    @property
    def is_active(self) -> bool:
        return self.status == RevenueScheduleStatus.ACTIVE.value

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RevenueSchedule {self.status} {self.total_micros}µ "
            f"org={self.organization_id} "
            f"{self.service_period_start.date()}..{self.service_period_end.date()}>"
        )


class RecognizedRevenueEntry(Base, UUIDMixin, TimestampMixin):
    """One period's recognised slice of a schedule. Append-only."""

    __tablename__ = "recognized_revenue_ledger"

    __table_args__ = (
        CheckConstraint(f"reason IN ({_REASON_IN})", name="reason_known"),
        CheckConstraint("length(currency) = 3", name="currency_iso4217"),
        CheckConstraint("period_end > period_start", name="period_ordered"),
        # A negative amount is legal only as a declared correction. Anything
        # else negative is a writer bug wearing an accounting costume.
        CheckConstraint(
            "amount_micros >= 0 OR reason = 'CORRECTION'",
            name="negative_is_correction",
        ),
        Index(
            "ix_recognized_revenue_schedule",
            "revenue_schedule_id",
            "period_start",
        ),
        Index(
            "ix_recognized_revenue_org_period",
            "organization_id",
            "period_start",
        ),
    )

    revenue_schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("revenue_schedules.id", ondelete="RESTRICT"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text(f"'{DEFAULT_CURRENCY}'")
    )
    reason: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'RATABLE'")
    )

    recognized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    recognized_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    schedule: Mapped[RevenueSchedule] = relationship(
        "RevenueSchedule", back_populates="entries"
    )

    @property
    def is_correction(self) -> bool:
        return self.reason == RecognitionReason.CORRECTION.value

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RecognizedRevenueEntry {self.reason} {self.amount_micros}µ "
            f"schedule={self.revenue_schedule_id} @{self.period_start.date()}>"
        )


__all__ = [
    "DEFAULT_CURRENCY",
    "RECOGNITION_METHOD_VALUES",
    "RECOGNITION_REASON_VALUES",
    "SCHEDULE_STATUS_VALUES",
    "RecognitionMethod",
    "RecognitionReason",
    "RecognizedRevenueEntry",
    "RevenueSchedule",
    "RevenueScheduleStatus",
]