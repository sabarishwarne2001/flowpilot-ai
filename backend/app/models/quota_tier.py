"""ARCH-14 Step 4 — quota tiers, the default that `spend_limits` overrides."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.spend_limit import SPEND_LIMIT_PERIOD_ENUM_NAME, SpendLimitPeriod


class QuotaTierKey(str, PyEnum):
    FREE = "free"
    DEVELOPER = "developer"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"


class OveragePolicy(str, PyEnum):
    REFUSE = "REFUSE"
    ALLOW_AND_BILL = "ALLOW_AND_BILL"
    ALLOW_AND_WARN = "ALLOW_AND_WARN"


POLICIES_REQUIRING_PRICE: frozenset[str] = frozenset({OveragePolicy.ALLOW_AND_BILL.value})


class QuotaTier(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "quota_tiers"

    __table_args__ = (
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("length(key) > 0", name="key_not_blank"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="effective_window_ordered",
        ),
        CheckConstraint(
            "NOT is_active OR published_at IS NOT NULL",
            name="active_implies_published",
        ),
        Index("uq_quota_tiers_key_version", "key", "version", unique=True),
        Index(
            "ix_quota_tiers_key_effective",
            "key",
            "effective_from",
            "effective_to",
            postgresql_where=text("is_active AND published_at IS NOT NULL"),
        ),
    )

    key: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_to: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    entries: Mapped[list["QuotaTierEntry"]] = relationship(
        "QuotaTierEntry",
        back_populates="quota_tier",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def is_published(self) -> bool:
        return self.published_at is not None

    def __repr__(self) -> str:  # pragma: no cover
        state = "published" if self.is_published else "draft"
        return f"<QuotaTier {self.key} v{self.version} {state}>"


class QuotaTierEntry(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "quota_tier_entries"

    __table_args__ = (
        CheckConstraint(
            "max_quantity IS NOT NULL OR max_cost_micros IS NOT NULL",
            name="at_least_one_ceiling",
        ),
        CheckConstraint(
            "max_quantity IS NULL OR max_quantity >= 0",
            name="quantity_non_negative",
        ),
        CheckConstraint(
            "max_cost_micros IS NULL OR max_cost_micros >= 0",
            name="cost_non_negative",
        ),
        CheckConstraint(
            "grace_quantity IS NULL OR grace_quantity >= 0",
            name="grace_non_negative",
        ),
        CheckConstraint("length(limit_key) > 0", name="limit_key_not_blank"),
        CheckConstraint(
            "overage_policy IN ('REFUSE', 'ALLOW_AND_BILL', 'ALLOW_AND_WARN')",
            name="overage_policy_known",
        ),
        CheckConstraint(
            "overage_policy <> 'ALLOW_AND_BILL' "
            "OR overage_price_tier_key IS NOT NULL",
            name="allow_and_bill_requires_price",
        ),
        Index(
            "uq_quota_tier_entries_scope",
            "quota_tier_id",
            "limit_key",
            "period",
            unique=True,
        ),
    )

    quota_tier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quota_tiers.id", ondelete="CASCADE"),
        nullable=False,
    )

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

    overage_policy: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'REFUSE'")
    )

    overage_price_tier_key: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )

    grace_quantity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )

    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    quota_tier: Mapped[QuotaTier] = relationship(
        "QuotaTier", back_populates="entries"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<QuotaTierEntry {self.limit_key}/{self.period} "
            f"qty={self.max_quantity} cost={self.max_cost_micros} "
            f"{self.overage_policy}>"
        )


__all__ = [
    "POLICIES_REQUIRING_PRICE",
    "OveragePolicy",
    "QuotaTier",
    "QuotaTierEntry",
    "QuotaTierKey",
]
