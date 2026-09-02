"""
ARCH-17 — per-tenant SLO definitions, observations and sealed measurements.
"""

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
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

SLO_UNIT_ENUM_NAME = "slo_unit"
SLO_WINDOW_ENUM_NAME = "slo_window"
SLO_METHOD_ENUM_NAME = "slo_method"


class SLOUnit(str, PyEnum):
    """What `target_value` means, and therefore which direction breaches."""

    #: Latency. Target is a ceiling; observed > target is a breach.
    MILLISECONDS = "MILLISECONDS"
    #: Success ratio in [0, 1]. Target is a floor; observed < target breaches.
    RATIO = "RATIO"

    @property
    def is_ceiling(self) -> bool:
        return self is SLOUnit.MILLISECONDS


class SLOWindow(str, PyEnum):
    """The period over which a measurement is taken and sealed."""

    HOUR = "HOUR"
    DAY = "DAY"
    MONTH = "MONTH"


class SLOMethod(str, PyEnum):
    """How `observed_value` was arrived at."""

    EXACT = "EXACT"
    HISTOGRAM_INTERPOLATED = "HISTOGRAM_INTERPOLATED"


DEFAULT_LATENCY_BOUNDS_MS: tuple[float, ...] = (
    10.0, 25.0, 50.0, 80.0, 100.0, 200.0, 300.0, 500.0,
    800.0, 1200.0, 2000.0, 3000.0, 5000.0, 8000.0, 15000.0, 30000.0,
)


def bucket_bounds_for(
    target_value: Optional[Decimal | float], *, unit: SLOUnit
) -> tuple[float, ...]:
    if unit is not SLOUnit.MILLISECONDS:
        return ()
    bounds = set(DEFAULT_LATENCY_BOUNDS_MS)
    if target_value is not None:
        bounds.add(float(target_value))
    return tuple(sorted(bounds))


class SLODefinition(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "slo_definitions"

    __table_args__ = (
        CheckConstraint("length(slo_key) > 0", name="slo_key_not_blank"),
        CheckConstraint("target_value >= 0", name="target_non_negative"),
        CheckConstraint(
            f"unit <> 'RATIO'::{SLO_UNIT_ENUM_NAME} OR target_value <= 1",
            name="ratio_target_is_a_proportion",
        ),
        CheckConstraint(
            "NOT is_contractual OR organization_id IS NOT NULL",
            name="contractual_requires_tenant",
        ),
        Index(
            "uq_slo_definitions_tenant_key",
            "slo_key",
            "organization_id",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
        Index(
            "uq_slo_definitions_platform_key",
            "slo_key",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),
        Index(
            "ix_slo_definitions_organization_id",
            "organization_id",
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
    )

    slo_key: Mapped[str] = mapped_column(String(100), nullable=False)
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )
    target_value: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit: Mapped[SLOUnit] = mapped_column(
        PGEnum(SLOUnit, name=SLO_UNIT_ENUM_NAME, create_type=False, validate_strings=True),
        nullable=False,
    )
    window_period: Mapped[SLOWindow] = mapped_column(
        PGEnum(SLOWindow, name=SLO_WINDOW_ENUM_NAME, create_type=False, validate_strings=True),
        nullable=False,
        server_default=text(f"'DAY'::{SLO_WINDOW_ENUM_NAME}"),
    )
    is_contractual: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    display_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        scope = self.organization_id or "PLATFORM"
        return f"<SLODefinition {self.slo_key} scope={scope} target={self.target_value}>"


class SLOObservation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "slo_observations"

    __table_args__ = (
        CheckConstraint("sample_count >= 0", name="sample_count_non_negative"),
        CheckConstraint("error_count >= 0", name="error_count_non_negative"),
        CheckConstraint("error_count <= sample_count", name="errors_within_samples"),
        CheckConstraint("sum_value >= 0", name="sum_non_negative"),
        CheckConstraint(
            "jsonb_typeof(bucket_counts) = 'array'", name="buckets_are_an_array"
        ),
        CheckConstraint(
            "date_trunc('hour', window_start) = window_start",
            name="window_start_is_an_hour",
        ),
        Index(
            "uq_slo_observations_scope",
            "organization_id",
            "slo_key",
            "window_start",
            unique=True,
        ),
        Index("ix_slo_observations_key_window", "slo_key", "window_start"),
        Index("ix_slo_observations_window_start", "window_start"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    slo_key: Mapped[str] = mapped_column(String(100), nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    sample_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    error_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    sum_value: Mapped[Decimal] = mapped_column(
        Numeric(20, 4), nullable=False, server_default=text("0")
    )
    bucket_bounds: Mapped[list[float]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    bucket_counts: Mapped[list[int]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SLOObservation {self.slo_key} org={self.organization_id} "
            f"hour={self.window_start} n={self.sample_count}>"
        )


class SLOMeasurement(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "slo_measurements"

    __table_args__ = (
        CheckConstraint("sample_count >= 0", name="sample_count_non_negative"),
        CheckConstraint("window_end > window_start", name="window_ordered"),
        CheckConstraint("observed_value >= 0", name="observed_non_negative"),
        CheckConstraint(
            "sample_count > 0 OR NOT breached", name="empty_window_cannot_breach"
        ),
        Index(
            "uq_slo_measurements_scope",
            "slo_definition_id",
            "organization_id",
            "window_start",
            unique=True,
        ),
        Index(
            "ix_slo_measurements_org_window",
            "organization_id",
            text("window_start DESC"),
        ),
        Index(
            "ix_slo_measurements_breaches",
            "organization_id",
            text("window_start DESC"),
            postgresql_where=text("breached"),
        ),
    )

    slo_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("slo_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    slo_key: Mapped[str] = mapped_column(String(100), nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_value: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    target_value: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit: Mapped[SLOUnit] = mapped_column(
        PGEnum(SLOUnit, name=SLO_UNIT_ENUM_NAME, create_type=False, validate_strings=True),
        nullable=False,
    )
    method: Mapped[SLOMethod] = mapped_column(
        PGEnum(SLOMethod, name=SLO_METHOD_ENUM_NAME, create_type=False, validate_strings=True),
        nullable=False,
        server_default=text(f"'HISTOGRAM_INTERPOLATED'::{SLO_METHOD_ENUM_NAME}"),
    )
    sample_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    error_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    breached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_contractual: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    sealed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    @property
    def is_sealed(self) -> bool:
        return self.sealed_at is not None

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SLOMeasurement {self.slo_key} org={self.organization_id} "
            f"{self.window_start} observed={self.observed_value} "
            f"target={self.target_value} breached={self.breached}>"
        )


__all__ = [
    "DEFAULT_LATENCY_BOUNDS_MS",
    "SLODefinition",
    "SLOMeasurement",
    "SLOMethod",
    "SLOObservation",
    "SLOUnit",
    "SLOWindow",
    "SLO_METHOD_ENUM_NAME",
    "SLO_UNIT_ENUM_NAME",
    "SLO_WINDOW_ENUM_NAME",
    "bucket_bounds_for",
]
