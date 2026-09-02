"""ARCH-14 Step 8 — invoice reproduction (finding A9)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.usage_events import USAGE_EVENT_TYPES
from app.models.price_book import PriceBook
from app.models.usage_rollup import RollupWindow, UsageRollup
from app.services import pricing_service, rollup_service

logger = logging.getLogger("app.services.invoice_preview")

DEFAULT_CURRENCY = "USD"
REPRODUCTION_MONTHS = 11

NOT_REPRODUCIBLE_LEGACY = "legacy_pricing"
NOT_REPRODUCIBLE_UNPRICED = "unpriced"
NOT_REPRODUCIBLE_MIXED = "price_mixed"


class InvoiceReproductionError(Exception):
    """A period could not be reproduced at all."""


@dataclass(frozen=True)
class InvoiceLine:
    event_type: str
    unit: str
    provider: Optional[str]
    model: Optional[str]
    quantity: Decimal
    unit_price_micros: Optional[Decimal]
    cost_micros: int
    price_book_id: Optional[uuid.UUID]
    price_book_version: Optional[int]
    estimated_quantity: Decimal
    late_quantity: Decimal
    event_count: int
    reproducible: bool
    reason: Optional[str] = None
    recomputed_cost_micros: Optional[int] = None

    @property
    def is_overage(self) -> bool:
        return self.event_type.endswith(".overage")

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "unit": self.unit,
            "provider": self.provider,
            "model": self.model,
            "quantity": format(self.quantity, "f"),
            "unit_price_micros": (
                format(self.unit_price_micros, "f")
                if self.unit_price_micros is not None
                else None
            ),
            "cost_micros": self.cost_micros,
            "recomputed_cost_micros": self.recomputed_cost_micros,
            "price_book_version": self.price_book_version,
            "estimated_quantity": format(self.estimated_quantity, "f"),
            "late_quantity": format(self.late_quantity, "f"),
            "event_count": self.event_count,
            "reproducible": self.reproducible,
            "reason": self.reason,
            "is_overage": self.is_overage,
        }


@dataclass
class InvoicePreview:
    organization_id: uuid.UUID
    period_start: datetime
    period_end: datetime
    currency: str
    sealed: bool
    sealed_at: Optional[datetime]
    lines: list[InvoiceLine] = field(default_factory=list)

    @property
    def total_cost_micros(self) -> int:
        return sum(line.cost_micros for line in self.lines)

    @property
    def reproducible_cost_micros(self) -> int:
        return sum(line.cost_micros for line in self.lines if line.reproducible)

    @property
    def unreproducible_cost_micros(self) -> int:
        return self.total_cost_micros - self.reproducible_cost_micros

    @property
    def fully_reproducible(self) -> bool:
        return all(line.reproducible for line in self.lines)

    @property
    def price_book_versions(self) -> list[int]:
        return sorted(
            {
                line.price_book_version
                for line in self.lines
                if line.price_book_version is not None
            }
        )

    def failures(self) -> list[InvoiceLine]:
        return [line for line in self.lines if not line.reproducible]

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": str(self.organization_id),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "currency": self.currency,
            "sealed": self.sealed,
            "sealed_at": self.sealed_at.isoformat() if self.sealed_at else None,
            "total_cost_micros": self.total_cost_micros,
            "reproducible_cost_micros": self.reproducible_cost_micros,
            "unreproducible_cost_micros": self.unreproducible_cost_micros,
            "fully_reproducible": self.fully_reproducible,
            "price_book_versions": self.price_book_versions,
            "lines": [line.as_dict() for line in self.lines],
        }


def _unit_for(event_type: str) -> str:
    descriptor = USAGE_EVENT_TYPES.get(event_type)
    if descriptor is not None:
        return descriptor.unit.value
    base = event_type.removesuffix(".overage")
    descriptor = USAGE_EVENT_TYPES.get(base)
    return descriptor.unit.value if descriptor else "unit"


def _book_versions(db: Session, book_ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not book_ids:
        return {}
    rows = db.execute(
        select(PriceBook.id, PriceBook.version).where(PriceBook.id.in_(book_ids))
    ).all()
    return {row[0]: int(row[1]) for row in rows}


def reproduce(
    db: Session,
    *,
    organization_id: uuid.UUID,
    period_start: datetime,
    period_end: Optional[datetime] = None,
    currency: str = DEFAULT_CURRENCY,
) -> InvoicePreview:
    start = period_start.astimezone(timezone.utc)
    month_start = rollup_service.month_bucket(start)
    natural_end = rollup_service.bucket_end(rollup_service.MONTH, month_start)
    end = (period_end or natural_end).astimezone(timezone.utc)

    if start == month_start and end == natural_end:
        granularity = rollup_service.MONTH
    else:
        granularity = rollup_service.DAY

    stmt = (
        select(
            UsageRollup.event_type,
            UsageRollup.provider,
            UsageRollup.model,
            UsageRollup.price_book_id,
            UsageRollup.unit_price_micros,
            func.coalesce(func.sum(UsageRollup.quantity), 0),
            func.coalesce(func.sum(UsageRollup.cost_micros), 0),
            func.coalesce(func.sum(UsageRollup.estimated_quantity), 0),
            func.coalesce(func.sum(UsageRollup.late_quantity), 0),
            func.coalesce(func.sum(UsageRollup.event_count), 0),
            func.bool_or(
                func.coalesce(
                    UsageRollup.details["price_mixed"].astext == "true", False
                )
            ),
        )
        .where(
            UsageRollup.organization_id == organization_id,
            UsageRollup.grain == "DETAIL",
            UsageRollup.granularity == granularity,
            UsageRollup.bucket_start >= start,
            UsageRollup.bucket_start < end,
        )
        .group_by(
            UsageRollup.event_type,
            UsageRollup.provider,
            UsageRollup.model,
            UsageRollup.price_book_id,
            UsageRollup.unit_price_micros,
        )
        .order_by(UsageRollup.event_type, UsageRollup.provider, UsageRollup.model)
    )

    rows = db.execute(stmt).all()
    versions = _book_versions(db, {row[3] for row in rows if row[3] is not None})

    lines: list[InvoiceLine] = []
    for row in rows:
        (
            event_type,
            provider,
            model,
            price_book_id,
            unit_price,
            quantity,
            cost_micros,
            estimated_quantity,
            late_quantity,
            event_count,
            mixed,
        ) = row

        quantity = Decimal(quantity)
        cost_micros = int(cost_micros)
        unit_price_dec = (
            Decimal(str(unit_price)) if unit_price is not None else None
        )

        recomputed: Optional[int] = None
        reproducible = False
        reason: Optional[str] = None

        if mixed:
            reason = NOT_REPRODUCIBLE_MIXED
        elif price_book_id is None or unit_price_dec is None:
            reason = (
                NOT_REPRODUCIBLE_LEGACY
                if cost_micros and unit_price_dec is None and price_book_id is None
                else NOT_REPRODUCIBLE_UNPRICED
            )
        else:
            recomputed = pricing_service.cost_micros(quantity, unit_price_dec)
            if recomputed == cost_micros:
                reproducible = True
            else:
                reason = "arithmetic_mismatch"

        lines.append(
            InvoiceLine(
                event_type=event_type,
                unit=_unit_for(event_type),
                provider=provider,
                model=model,
                quantity=quantity,
                unit_price_micros=unit_price_dec,
                cost_micros=cost_micros,
                price_book_id=price_book_id,
                price_book_version=versions.get(price_book_id),
                estimated_quantity=Decimal(estimated_quantity),
                late_quantity=Decimal(late_quantity),
                event_count=int(event_count),
                reproducible=reproducible,
                reason=reason,
                recomputed_cost_micros=recomputed,
            )
        )

    window = (
        db.execute(
            select(RollupWindow).where(
                RollupWindow.granularity == rollup_service.MONTH,
                RollupWindow.bucket_start == month_start,
            )
        )
        .scalars()
        .first()
    )

    preview = InvoicePreview(
        organization_id=organization_id,
        period_start=start,
        period_end=end,
        currency=currency,
        sealed=bool(window and window.status == "SEALED"),
        sealed_at=window.sealed_at if window else None,
        lines=lines,
    )

    if not preview.fully_reproducible:
        logger.warning(
            "invoice.not_fully_reproducible",
            extra={
                "organization_id": str(organization_id),
                "period_start": start.isoformat(),
                "unreproducible_cost_micros": preview.unreproducible_cost_micros,
                "reasons": sorted(
                    {line.reason for line in preview.failures() if line.reason}
                ),
            },
        )
    return preview


@dataclass
class ReproductionReport:
    organization_id: uuid.UUID
    months: int
    previews: list[InvoicePreview] = field(default_factory=list)

    @property
    def sealed_previews(self) -> list[InvoicePreview]:
        return [preview for preview in self.previews if preview.sealed]

    @property
    def all_sealed_reproducible(self) -> bool:
        return all(preview.fully_reproducible for preview in self.sealed_previews)

    @property
    def total_unreproducible_micros(self) -> int:
        return sum(
            preview.unreproducible_cost_micros for preview in self.sealed_previews
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": str(self.organization_id),
            "months": self.months,
            "sealed_months": len(self.sealed_previews),
            "all_sealed_reproducible": self.all_sealed_reproducible,
            "total_unreproducible_micros": self.total_unreproducible_micros,
            "periods": [preview.as_dict() for preview in self.previews],
        }


def reproduce_history(
    db: Session,
    *,
    organization_id: uuid.UUID,
    months: int = REPRODUCTION_MONTHS,
    now: Optional[datetime] = None,
) -> ReproductionReport:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cursor = rollup_service.month_bucket(moment)

    previews: list[InvoicePreview] = []
    for _ in range(max(1, months)):
        cursor = rollup_service.month_bucket(cursor - timedelta(days=1))
        previews.append(
            reproduce(db, organization_id=organization_id, period_start=cursor)
        )

    report = ReproductionReport(
        organization_id=organization_id, months=months, previews=previews
    )
    logger.info("invoice.history_reproduced", extra=report.as_dict() | {"periods": None})
    return report


def sample_organizations(db: Session, *, limit: int = 25) -> list[uuid.UUID]:
    rows = db.execute(
        select(
            UsageRollup.organization_id,
            func.max(UsageRollup.bucket_start).label("latest"),
        )
        .where(
            UsageRollup.grain == "DETAIL",
            UsageRollup.granularity == rollup_service.MONTH,
            UsageRollup.sealed_at.is_not(None),
        )
        .group_by(UsageRollup.organization_id)
        .order_by(func.max(UsageRollup.bucket_start).desc())
        .limit(limit)
    ).all()
    return [row[0] for row in rows]


__all__ = [
    "DEFAULT_CURRENCY",
    "NOT_REPRODUCIBLE_LEGACY",
    "NOT_REPRODUCIBLE_MIXED",
    "NOT_REPRODUCIBLE_UNPRICED",
    "REPRODUCTION_MONTHS",
    "InvoiceLine",
    "InvoicePreview",
    "InvoiceReproductionError",
    "ReproductionReport",
    "reproduce",
    "reproduce_history",
    "sample_organizations",
]
