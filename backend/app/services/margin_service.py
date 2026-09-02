"""ARCH-18 — revenue against COGS, with the unknowns kept visible.

Every function here obeys one rule, and the rule is the whole phase:

    A margin is computed ONLY over rows whose cost is known, and it is always
    returned alongside the share of revenue that computation excluded.

The tempting alternative is `SUM(COALESCE(cost_basis_micros, 0))`. It is one
word shorter, it never returns None, it makes every chart render, and it is
catastrophic: a tenant whose entire spend is on units with no supplier rate
entered reports a 100% gross margin and tops the profitability ranking. The
most profitable-looking tenant on the dashboard would be the one you know
least about. Every aggregate below therefore filters `IS NOT NULL` on the cost
side and carries `unknown_cost_share` next to the answer.

`unknown_cost_share` is measured by REVENUE, not by row count. Ten thousand
zero-revenue rows with unknown cost matter less than one large one, and a
count-based share would say the opposite.

On scale: these aggregate `usage_events` directly rather than `usage_rollups`.
ARCH-14 finding B2 established that scanning the ledger on the request hot
path is the problem rollups exist to solve — but this is not a hot path. It is
a superadmin report over a closed period, run by hand or monthly, and
`usage_rollups` carries no cost basis column to read. Folding cost basis into
the rollup is the right move when these queries get slow; the index
`ix_usage_events_provider_cost_basis` buys the room to defer it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional, Sequence

from sqlalchemy import Select, and_, case, func, select
from sqlalchemy.orm import Session

from app.models.organization import Organization
from app.models.supplier_cogs import HARD_COST_BASIS_SOURCES
from app.models.usage_event import UsageEvent

logger = logging.getLogger("app.services.margin")

TenantOrder = Literal["MARGIN_ASC", "MARGIN_DESC", "REVENUE_DESC", "UNKNOWN_DESC"]

#: Below this share of revenue with a known cost, a margin figure is not
#: meaningful and the API says so rather than rendering a confident number.
MIN_TRUSTWORTHY_KNOWN_SHARE: float = 0.60


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    """Guarded division. Returns None rather than 0.0 for an empty base.

    0.0 and "undefined" are different claims and the dashboard renders them
    differently: a zero-margin period is a business problem, an undefined one
    is a data problem.
    """
    if denominator == 0:
        return None
    return float(Decimal(numerator) / Decimal(denominator))


@dataclass(frozen=True)
class MarginFigures:
    """The shared numeric core of every margin answer in this module."""

    revenue_micros: int
    #: Revenue on rows that also carry a cost basis. The ONLY revenue a
    #: gross margin may be computed against.
    attributed_revenue_micros: int
    cost_basis_micros: int
    event_count: int
    known_cost_event_count: int
    unknown_cost_event_count: int
    #: Revenue on rows whose cost basis is ESTIMATED rather than measured or
    #: rate-carded. Included in the margin, reported separately, because
    #: "62% margin, 40% of it resting on estimates" is a different sentence.
    soft_cost_revenue_micros: int = 0

    @property
    def unknown_cost_revenue_micros(self) -> int:
        return self.revenue_micros - self.attributed_revenue_micros

    @property
    def gross_margin_micros(self) -> Optional[int]:
        if self.known_cost_event_count == 0:
            return None
        return self.attributed_revenue_micros - self.cost_basis_micros

    @property
    def gross_margin_ratio(self) -> Optional[float]:
        margin = self.gross_margin_micros
        if margin is None:
            return None
        return _ratio(margin, self.attributed_revenue_micros)

    @property
    def unknown_cost_share(self) -> Optional[float]:
        """Share of revenue excluded from the margin, by value."""
        return _ratio(self.unknown_cost_revenue_micros, self.revenue_micros)

    @property
    def soft_cost_share(self) -> Optional[float]:
        return _ratio(self.soft_cost_revenue_micros, self.attributed_revenue_micros)

    @property
    def is_trustworthy(self) -> bool:
        """Whether the margin rests on enough known cost to be quoted."""
        if self.gross_margin_micros is None:
            return False
        if self.revenue_micros == 0:
            return False
        known_share = _ratio(self.attributed_revenue_micros, self.revenue_micros)
        return known_share is not None and known_share >= MIN_TRUSTWORTHY_KNOWN_SHARE


@dataclass(frozen=True)
class PlatformMarginSummary:
    period_start: datetime
    period_end: datetime
    currency: str
    figures: MarginFigures
    organization_count: int


@dataclass(frozen=True)
class TenantEconomics:
    organization_id: uuid.UUID
    organization_name: Optional[str]
    organization_slug: Optional[str]
    figures: MarginFigures


@dataclass(frozen=True)
class ProviderCost:
    provider: Optional[str]
    cost_basis_micros: int
    revenue_micros: int
    event_count: int
    unknown_cost_event_count: int


# ---------------------------------------------------------------------------
# The shared aggregate.
#
# Six expressions, defined once, because the difference between the platform
# summary and the per-tenant ranking must be a GROUP BY and nothing else. Two
# hand-written copies of this SQL would drift, and the first symptom would be a
# tenant table whose column totals do not equal the summary above it.
# ---------------------------------------------------------------------------

_KNOWN = UsageEvent.cost_basis_micros.is_not(None)

_REVENUE = func.coalesce(func.sum(UsageEvent.cost_micros), 0)

_ATTRIBUTED_REVENUE = func.coalesce(
    func.sum(case((_KNOWN, UsageEvent.cost_micros), else_=0)), 0
)

_COST = func.coalesce(func.sum(UsageEvent.cost_basis_micros), 0)

_EVENTS = func.count()

_KNOWN_EVENTS = func.coalesce(
    func.sum(case((_KNOWN, 1), else_=0)), 0
)

_SOFT_REVENUE = func.coalesce(
    func.sum(
        case(
            (
                and_(
                    _KNOWN,
                    UsageEvent.cost_basis_source.not_in(
                        tuple(sorted(HARD_COST_BASIS_SOURCES))
                    ),
                ),
                UsageEvent.cost_micros,
            ),
            else_=0,
        )
    ),
    0,
)

_AGGREGATE_COLUMNS = (
    _REVENUE,
    _ATTRIBUTED_REVENUE,
    _COST,
    _EVENTS,
    _KNOWN_EVENTS,
    _SOFT_REVENUE,
)


def _figures_from_row(row: Sequence[Any], offset: int = 0) -> MarginFigures:
    revenue = int(row[offset + 0] or 0)
    attributed = int(row[offset + 1] or 0)
    cost = int(row[offset + 2] or 0)
    events = int(row[offset + 3] or 0)
    known = int(row[offset + 4] or 0)
    soft = int(row[offset + 5] or 0)
    return MarginFigures(
        revenue_micros=revenue,
        attributed_revenue_micros=attributed,
        cost_basis_micros=cost,
        event_count=events,
        known_cost_event_count=known,
        unknown_cost_event_count=events - known,
        soft_cost_revenue_micros=soft,
    )


def _window(stmt: Select, *, period_start: datetime, period_end: datetime) -> Select:
    return stmt.where(
        UsageEvent.occurred_at >= _as_utc(period_start),
        UsageEvent.occurred_at < _as_utc(period_end),
    )


# ---------------------------------------------------------------------------
# Public reads
# ---------------------------------------------------------------------------


def platform_summary(
    db: Session,
    *,
    period_start: datetime,
    period_end: datetime,
    currency: str = "USD",
) -> PlatformMarginSummary:
    """Revenue, COGS and gross margin across every tenant in a window."""
    row = db.execute(
        _window(
            select(*_AGGREGATE_COLUMNS),
            period_start=period_start,
            period_end=period_end,
        )
    ).one()

    org_count = int(
        db.execute(
            _window(
                select(func.count(func.distinct(UsageEvent.organization_id))),
                period_start=period_start,
                period_end=period_end,
            )
        ).scalar_one()
        or 0
    )

    figures = _figures_from_row(row)

    if figures.event_count and not figures.is_trustworthy:
        logger.warning(
            "margin.low_cost_coverage",
            extra={
                "period_start": _as_utc(period_start).isoformat(),
                "period_end": _as_utc(period_end).isoformat(),
                "unknown_cost_share": figures.unknown_cost_share,
                "unknown_cost_event_count": figures.unknown_cost_event_count,
            },
        )

    return PlatformMarginSummary(
        period_start=_as_utc(period_start),
        period_end=_as_utc(period_end),
        currency=currency,
        figures=figures,
        organization_count=org_count,
    )


def tenant_economics(
    db: Session,
    *,
    period_start: datetime,
    period_end: datetime,
    limit: int = 50,
    order: TenantOrder = "MARGIN_ASC",
    organization_id: Optional[uuid.UUID] = None,
) -> list[TenantEconomics]:
    """Per-tenant unit economics, ranked.

    Default order is MARGIN_ASC — worst margin first. A profitability table
    sorted best-first is a vanity metric; the tenants you need to see are the
    ones costing more than they pay.

    Ordering happens in Python rather than SQL. The ranking keys are ratios
    over a filtered denominator that is zero for any tenant with no known
    cost, and expressing "undefined sorts last, not as zero" in SQL takes a
    NULLIF-plus-NULLS-LAST construction per key that is easy to get subtly
    wrong. The row count here is one per tenant per period; the platform will
    reach a rewrite of the reporting layer long before this sort matters.
    """
    stmt = _window(
        select(
            UsageEvent.organization_id,
            *_AGGREGATE_COLUMNS,
        ),
        period_start=period_start,
        period_end=period_end,
    ).group_by(UsageEvent.organization_id)

    if organization_id is not None:
        stmt = stmt.where(UsageEvent.organization_id == organization_id)

    rows = db.execute(stmt).all()
    if not rows:
        return []

    org_ids = [row[0] for row in rows]
    names = {
        org.id: org
        for org in db.execute(
            select(Organization).where(Organization.id.in_(org_ids))
        )
        .scalars()
        .all()
    }

    entries = [
        TenantEconomics(
            organization_id=row[0],
            organization_name=getattr(names.get(row[0]), "name", None),
            organization_slug=getattr(names.get(row[0]), "slug", None),
            figures=_figures_from_row(row, offset=1),
        )
        for row in rows
    ]

    def margin_key(entry: TenantEconomics) -> tuple[int, float]:
        ratio = entry.figures.gross_margin_ratio
        # A tenant with no known cost has no margin. It sorts to the end of
        # either direction rather than being treated as 0% or 100% — both of
        # which would put an unmeasured tenant at an extreme of the ranking.
        if ratio is None:
            return (1, 0.0)
        return (0, ratio)

    if order == "MARGIN_ASC":
        entries.sort(key=margin_key)
    elif order == "MARGIN_DESC":
        entries.sort(key=lambda e: (margin_key(e)[0], -margin_key(e)[1]))
    elif order == "REVENUE_DESC":
        entries.sort(key=lambda e: -e.figures.revenue_micros)
    elif order == "UNKNOWN_DESC":
        entries.sort(key=lambda e: -(e.figures.unknown_cost_revenue_micros))
    else:  # pragma: no cover - Literal keeps this unreachable from the API
        entries.sort(key=margin_key)

    return entries[: max(1, limit)]


def provider_costs(
    db: Session,
    *,
    period_start: datetime,
    period_end: datetime,
) -> list[ProviderCost]:
    """Modelled COGS by supplier. The left-hand side of reconciliation."""
    rows = db.execute(
        _window(
            select(
                UsageEvent.provider,
                _COST,
                _REVENUE,
                _EVENTS,
                _KNOWN_EVENTS,
            ),
            period_start=period_start,
            period_end=period_end,
        ).group_by(UsageEvent.provider)
    ).all()

    return sorted(
        [
            ProviderCost(
                provider=row[0],
                cost_basis_micros=int(row[1] or 0),
                revenue_micros=int(row[2] or 0),
                event_count=int(row[3] or 0),
                unknown_cost_event_count=int(row[3] or 0) - int(row[4] or 0),
            )
            for row in rows
        ],
        key=lambda p: -p.cost_basis_micros,
    )


def modelled_cost_for_provider(
    db: Session,
    *,
    provider: str,
    period_start: datetime,
    period_end: datetime,
) -> tuple[int, int, int]:
    """(cost_micros, rows_with_cost, rows_without_cost) for one supplier.

    The exact figure `supplier_reconciliation_service` compares an invoice
    against. Provider matching is case-folded because `pricing_service`
    normalises provider to lowercase on resolve while `usage_events.provider`
    can also be written from a descriptor default.
    """
    row = db.execute(
        _window(
            select(_COST, _KNOWN_EVENTS, _EVENTS),
            period_start=period_start,
            period_end=period_end,
        ).where(func.lower(UsageEvent.provider) == (provider or "").strip().lower())
    ).one()

    cost = int(row[0] or 0)
    known = int(row[1] or 0)
    total = int(row[2] or 0)
    return cost, known, total - known


__all__ = [
    "MIN_TRUSTWORTHY_KNOWN_SHARE",
    "MarginFigures",
    "PlatformMarginSummary",
    "ProviderCost",
    "TenantEconomics",
    "TenantOrder",
    "modelled_cost_for_provider",
    "platform_summary",
    "provider_costs",
    "tenant_economics",
]
