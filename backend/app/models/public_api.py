"""ARCH-21 §4.2 — the developer portal's daily usage rollup."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.api_key import ApiKey
    from app.models.organization import Organization


LATENCY_BOUNDS_MS: tuple[float, ...] = (
    10.0, 25.0, 50.0, 80.0, 100.0, 200.0, 300.0, 500.0,
    800.0, 1200.0, 2000.0, 3000.0, 5000.0, 8000.0, 15000.0, 30000.0,
)

LATENCY_BUCKET_COUNT: int = len(LATENCY_BOUNDS_MS)


def empty_buckets() -> list[int]:
    return [0] * LATENCY_BUCKET_COUNT


def bucket_index_for(latency_ms: float) -> int:
    value = float(latency_ms)
    for index, bound in enumerate(LATENCY_BOUNDS_MS):
        if value <= bound:
            return index
    return LATENCY_BUCKET_COUNT - 1


class ApiKeyUsageDaily(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "api_key_usage_daily"

    __table_args__ = (
        CheckConstraint(
            "request_count >= 0", name="request_count_non_negative"
        ),
        CheckConstraint("error_count >= 0", name="error_count_non_negative"),
        CheckConstraint("throttled_count >= 0", name="throttled_non_negative"),
        CheckConstraint("total_latency_ms >= 0", name="latency_non_negative"),
        CheckConstraint(
            "throttled_count <= error_count", name="throttles_within_errors"
        ),
        CheckConstraint(
            "jsonb_typeof(latency_bucket_counts) = 'array'",
            name="buckets_are_array",
        ),
        CheckConstraint(
            f"jsonb_array_length(latency_bucket_counts) = {LATENCY_BUCKET_COUNT}",
            name="buckets_arity",
        ),
        Index(
            "uq_api_key_usage_daily_key_date",
            "api_key_id",
            "usage_date",
            unique=True,
        ),
        Index(
            "ix_api_key_usage_daily_org_date",
            "organization_id",
            "usage_date",
        ),
    )

    api_key_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)

    request_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    error_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    throttled_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    total_latency_ms: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    latency_bucket_counts: Mapped[list[int]] = mapped_column(
        JSONB,
        nullable=False,
        default=empty_buckets,
        server_default=text(
            "'[" + ", ".join("0" for _ in LATENCY_BOUNDS_MS) + "]'::jsonb"
        ),
    )

    api_key: Mapped["ApiKey"] = relationship(
        "ApiKey", back_populates="usage_daily"
    )
    organization: Mapped["Organization"] = relationship("Organization")

    @property
    def mean_latency_ms(self) -> Optional[float]:
        served = self.request_count - self.throttled_count
        if served <= 0:
            return None
        return float(self.total_latency_ms) / float(served)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ApiKeyUsageDaily key={self.api_key_id} {self.usage_date} "
            f"n={self.request_count} err={self.error_count}>"
        )


__all__ = [
    "ApiKeyUsageDaily",
    "LATENCY_BOUNDS_MS",
    "LATENCY_BUCKET_COUNT",
    "bucket_index_for",
    "empty_buckets",
]
