"""ARCH-10 Step 3 — per-tenant spend ceilings.

A separate table rather than columns on `organizations`, because a limit is
not one number: an org has a total-cost ceiling *and* per-dimension ceilings
(OCR pages, LLM tokens), each with its own period and its own enforcement
posture. Columns would force one of those to be primary.

Rows are optional. An organization with no rows is not unlimited — it inherits
the platform defaults from `settings`. Default-deny is the entire point of the
control: the risk being managed is unbounded provider spend from a tenant
nobody has configured yet.
"""

from __future__ import annotations

import uuid
from enum import Enum as PyEnum
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

SPEND_LIMIT_PERIOD_ENUM_NAME = "spend_limit_period"


class SpendLimitPeriod(str, PyEnum):
    DAY = "DAY"
    MONTH = "MONTH"


class SpendLimit(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "spend_limits"

    __table_args__ = (
        CheckConstraint(
            "max_quantity IS NOT NULL OR max_cost_micros IS NOT NULL",
            name="at_least_one_ceiling",
        ),
        CheckConstraint(
            "max_quantity IS NULL OR max_quantity >= 0", name="quantity_non_negative"
        ),
        CheckConstraint(
            "max_cost_micros IS NULL OR max_cost_micros >= 0",
            name="cost_non_negative",
        ),
        CheckConstraint("length(limit_key) > 0", name="limit_key_not_blank"),
        # One active limit per (org, dimension, period). Historical/disabled
        # rows are retained for the audit story, so the index is partial.
        Index(
            "uq_spend_limits_active_key",
            "organization_id",
            "limit_key",
            "period",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index(
            "ix_spend_limits_organization_id",
            "organization_id",
            postgresql_where=text("is_active"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Either a billable usage event type ("ocr.page") or "*" for total cost.
    limit_key: Mapped[str] = mapped_column(String(64), nullable=False)

    period: Mapped[SpendLimitPeriod] = mapped_column(
        PGEnum(
            SpendLimitPeriod,
            name=SPEND_LIMIT_PERIOD_ENUM_NAME,
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text(f"'MONTH'::{SPEND_LIMIT_PERIOD_ENUM_NAME}"),
    )

    max_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    max_cost_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    #: True refuses the operation. False records the breach and allows it —
    #: used to observe a proposed ceiling before it starts refusing traffic.
    hard_stop: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SpendLimit org={self.organization_id} key={self.limit_key} "
            f"period={self.period} qty={self.max_quantity} "
            f"cost={self.max_cost_micros} hard={self.hard_stop}>"
        )