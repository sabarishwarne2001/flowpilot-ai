r"""ARCH-14 Step 5 — the reconciliation engine."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.reconciliation import (
    CATEGORY_ORDER,
    DRIFT_ALERT_BPS,
    Attribution,
    FindingSeverity,
    ProviderStatement,
    ProviderStatementLine,
    ReconciliationCategory,
    ReconciliationFinding,
    ReconciliationRun,
    ReconciliationStatus,
)
from app.models.usage_rollup import UsageRollup
from app.services import rollup_service
from app.services.reconciliation.base import (
    StatementPayload,
    StatementSourceError,
    source_for,
)

logger = logging.getLogger("app.services.reconciliation.engine")


class ReconciliationRefused(Exception):
    """The period is not eligible for reconciliation yet."""


class ReconciliationError(Exception):
    """The run could not be completed."""


@dataclass
class LedgerSlice:
    model: Optional[str]
    event_type: Optional[str]
    quantity: Decimal = Decimal(0)
    cost_micros: int = 0
    estimated_quantity: Decimal = Decimal(0)
    estimated_cost_micros: int = 0
    boundary_cost_micros: int = 0

    @property
    def rate(self) -> Optional[Decimal]:
        if self.quantity <= 0:
            return None
        return Decimal(self.cost_micros) / self.quantity


@dataclass
class StatementSlice:
    model: Optional[str]
    event_type: Optional[str]
    quantity: Optional[Decimal] = None
    cost_micros: int = 0

    @property
    def rate(self) -> Optional[Decimal]:
        if self.quantity is None or self.quantity <= 0:
            return None
        return Decimal(self.cost_micros) / self.quantity


@dataclass
class FindingSpec:
    category: ReconciliationCategory
    severity: FindingSeverity
    drift_micros: int
    explanation: str
    model: Optional[str] = None
    event_type: Optional[str] = None
    ledger_quantity: Optional[Decimal] = None
    statement_quantity: Optional[Decimal] = None
    ledger_cost_micros: int = 0
    statement_cost_micros: int = 0
    details: dict[str, Any] = field(default_factory=dict)


def persist_statement(
    db: Session,
    payload: StatementPayload,
    *,
    imported_by_user_id: Optional[uuid.UUID] = None,
) -> ProviderStatement:
    existing = (
        db.execute(
            select(ProviderStatement).where(
                ProviderStatement.provider == payload.provider,
                ProviderStatement.source_key == payload.source_key,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        if (
            payload.source_digest
            and existing.source_digest
            and payload.source_digest != existing.source_digest
        ):
            raise StatementSourceError(
                f"Statement {payload.provider}/{payload.source_key} was already imported with a different digest. "
                "The provider restated this period. Import the restatement under a new source_key so both versions survive."
            )
        logger.info(
            "reconciliation.statement_already_imported",
            extra={
                "provider": payload.provider,
                "source_key": payload.source_key,
                "statement_id": str(existing.id),
            },
        )
        return existing

    if payload.attribution is not Attribution.ATTESTED:
        offenders = [line for line in payload.lines if line.organization_id]
        if offenders:
            raise StatementSourceError(
                f"{payload.provider} declares {payload.attribution.value} attribution but lines carry an organization_id."
            )

    statement = ProviderStatement(
        provider=payload.provider,
        source_key=payload.source_key,
        source_reference=payload.source_reference,
        source_digest=payload.source_digest,
        grain=payload.grain.value,
        attribution=payload.attribution.value,
        currency=payload.currency,
        period_start=payload.period_start,
        period_end=payload.period_end,
        imported_by_user_id=imported_by_user_id,
        line_count=len(payload.lines),
        total_cost_micros=payload.total_cost_micros,
        details=payload.details or None,
    )
    db.add(statement)
    db.flush([statement])

    for line in payload.lines:
        db.add(
            ProviderStatementLine(
                provider_statement_id=statement.id,
                provider=payload.provider,
                sku=line.sku,
                model=line.model,
                event_type=line.event_type,
                organization_id=line.organization_id,
                occurred_on=line.occurred_on,
                quantity=line.quantity,
                unit=line.unit,
                cost_micros=line.cost_micros,
                currency=line.currency,
                raw=line.raw,
            )
        )
    db.flush()

    logger.info(
        "reconciliation.statement_imported",
        extra={
            "provider": payload.provider,
            "source_key": payload.source_key,
            "lines": len(payload.lines),
            "total_cost_micros": payload.total_cost_micros,
            "attribution": payload.attribution.value,
        },
    )
    return statement


def _boundary_exposure(
    db: Session,
    *,
    provider: str,
    period_start: datetime,
    period_end: datetime,
    hours: int,
) -> dict[tuple[Optional[str], Optional[str]], int]:
    if hours <= 0:
        return {}

    window = timedelta(hours=hours)
    lower_end = period_start + window
    upper_start = period_end - window

    stmt = (
        select(
            UsageRollup.model,
            UsageRollup.event_type,
            func.coalesce(func.sum(UsageRollup.cost_micros), 0),
        )
        .where(
            UsageRollup.grain == "DETAIL",
            UsageRollup.granularity == rollup_service.HOUR,
            UsageRollup.provider == provider,
            UsageRollup.bucket_start >= period_start,
            UsageRollup.bucket_start < period_end,
            (UsageRollup.bucket_start < lower_end)
            | (UsageRollup.bucket_start >= upper_start),
        )
        .group_by(UsageRollup.model, UsageRollup.event_type)
    )
    return {(row[0], row[1]): int(row[2]) for row in db.execute(stmt).all()}


def ledger_side(
    db: Session,
    *,
    provider: str,
    period_start: datetime,
    period_end: datetime,
    boundary_hours: int,
) -> dict[tuple[Optional[str], Optional[str]], LedgerSlice]:
    stmt = (
        select(
            UsageRollup.model,
            UsageRollup.event_type,
            func.coalesce(func.sum(UsageRollup.quantity), 0),
            func.coalesce(func.sum(UsageRollup.cost_micros), 0),
            func.coalesce(func.sum(UsageRollup.estimated_quantity), 0),
            func.coalesce(func.sum(UsageRollup.estimated_cost_micros), 0),
        )
        .where(
            UsageRollup.grain == "DETAIL",
            UsageRollup.granularity == rollup_service.HOUR,
            UsageRollup.provider == provider,
            UsageRollup.bucket_start >= period_start,
            UsageRollup.bucket_start < period_end,
        )
        .group_by(UsageRollup.model, UsageRollup.event_type)
    )

    exposure = _boundary_exposure(
        db,
        provider=provider,
        period_start=period_start,
        period_end=period_end,
        hours=boundary_hours,
    )

    slices: dict[tuple[Optional[str], Optional[str]], LedgerSlice] = {}
    for row in db.execute(stmt).all():
        key = (row[0], row[1])
        slices[key] = LedgerSlice(
            model=row[0],
            event_type=row[1],
            quantity=Decimal(row[2]),
            cost_micros=int(row[3]),
            estimated_quantity=Decimal(row[4]),
            estimated_cost_micros=int(row[5]),
            boundary_cost_micros=exposure.get(key, 0),
        )
    return slices


def statement_side(
    statement: ProviderStatement,
) -> tuple[dict[tuple[Optional[str], Optional[str]], StatementSlice], list[ProviderStatementLine]]:
    slices: dict[tuple[Optional[str], Optional[str]], StatementSlice] = {}
    unmapped: list[ProviderStatementLine] = []

    for line in statement.lines:
        if not line.model:
            unmapped.append(line)
            continue
        key = (line.model, line.event_type)
        current = slices.setdefault(
            key, StatementSlice(model=line.model, event_type=line.event_type)
        )
        current.cost_micros += int(line.cost_micros)
        if line.quantity is not None:
            current.quantity = (current.quantity or Decimal(0)) + Decimal(
                str(line.quantity)
            )

    return slices, unmapped


def _bps(part: int, whole: int) -> Decimal:
    if not whole:
        return Decimal(0)
    return (Decimal(part) / Decimal(abs(whole)) * Decimal(10_000)).quantize(
        Decimal("0.0001")
    )


def _consume(pool: int, cap: int) -> tuple[int, int]:
    if pool == 0 or cap <= 0:
        return 0, pool
    magnitude = min(abs(pool), abs(cap))
    taken = magnitude if pool > 0 else -magnitude
    return taken, pool - taken


def categorise_pair(
    *,
    ledger: LedgerSlice,
    statement: StatementSlice,
) -> list[FindingSpec]:
    drift = statement.cost_micros - ledger.cost_micros
    specs: list[FindingSpec] = []

    shared = dict(
        model=ledger.model or statement.model,
        event_type=ledger.event_type or statement.event_type,
        ledger_quantity=ledger.quantity,
        statement_quantity=statement.quantity,
        ledger_cost_micros=ledger.cost_micros,
        statement_cost_micros=statement.cost_micros,
    )

    rate_s = statement.rate
    rate_l = ledger.rate

    if rate_s is not None and rate_l is not None:
        quantity_effect = int(
            ((statement.quantity or Decimal(0)) - ledger.quantity) * rate_s
        )
        price_effect = int(ledger.quantity * (rate_s - rate_l))
    elif ledger.quantity <= 0 and statement.cost_micros > 0:
        quantity_effect = drift
        price_effect = 0
    elif statement.quantity is None:
        quantity_effect = 0
        price_effect = 0
    else:
        quantity_effect = drift
        price_effect = 0

    pool = quantity_effect

    taken, pool = _consume(pool, ledger.boundary_cost_micros)
    if taken:
        specs.append(
            FindingSpec(
                category=ReconciliationCategory.TIMING_BOUNDARY,
                severity=FindingSeverity.INFO,
                drift_micros=taken,
                explanation="Usage near a period edge was dated on the other side by the provider.",
                details={
                    "boundary_exposure_micros": ledger.boundary_cost_micros,
                    "is_upper_bound": True,
                },
                **shared,
            )
        )

    taken, pool = _consume(pool, ledger.estimated_cost_micros)
    if taken:
        specs.append(
            FindingSpec(
                category=ReconciliationCategory.ESTIMATE_DRIFT,
                severity=FindingSeverity.INFO,
                drift_micros=taken,
                explanation="Count derived from local emission count rather than usage metadata.",
                details={
                    "estimated_cost_micros": ledger.estimated_cost_micros,
                    "estimated_quantity": str(ledger.estimated_quantity),
                    "is_upper_bound": True,
                },
                **shared,
            )
        )

    if price_effect:
        implied = (
            format(rate_s.quantize(Decimal("0.000000001")), "f")
            if rate_s is not None
            else None
        )
        ours = (
            format(rate_l.quantize(Decimal("0.000000001")), "f")
            if rate_l is not None
            else None
        )
        magnitude_bps = _bps(price_effect, statement.cost_micros or ledger.cost_micros)
        specs.append(
            FindingSpec(
                category=ReconciliationCategory.PRICE_DRIFT,
                severity=(
                    FindingSeverity.HIGH
                    if abs(magnitude_bps) > DRIFT_ALERT_BPS
                    else FindingSeverity.INFO
                ),
                drift_micros=price_effect,
                explanation="Quantities agree; unit price in price book differs from provider effective rate.",
                details={
                    "provider_rate_micros_per_unit": implied,
                    "book_rate_micros_per_unit": ours,
                    "is_upper_bound": False,
                },
                **shared,
            )
        )

    if pool > 0:
        specs.append(
            FindingSpec(
                category=ReconciliationCategory.UNMETERED_GENERATION,
                severity=FindingSeverity.CRITICAL,
                drift_micros=pool,
                explanation="Provider billed for units with no ledger row behind them.",
                details={"is_upper_bound": False},
                **shared,
            )
        )
    elif pool < 0:
        specs.append(
            FindingSpec(
                category=ReconciliationCategory.OVERMETERED_LEDGER,
                severity=FindingSeverity.HIGH,
                drift_micros=pool,
                explanation="Ledger holds rows the provider did not bill for.",
                details={"is_upper_bound": False},
                **shared,
            )
        )

    attributed = sum(spec.drift_micros for spec in specs)
    residue = drift - attributed
    if residue:
        magnitude_bps = _bps(residue, statement.cost_micros or ledger.cost_micros)
        specs.append(
            FindingSpec(
                category=ReconciliationCategory.UNEXPLAINED,
                severity=(
                    FindingSeverity.HIGH
                    if abs(magnitude_bps) > DRIFT_ALERT_BPS
                    else FindingSeverity.WARNING
                ),
                drift_micros=residue,
                explanation="Residue after the five explained categories.",
                details={
                    "statement_quantity_available": statement.quantity is not None,
                    "is_upper_bound": False,
                },
                **shared,
            )
        )

    return specs


def _eligibility(
    *, period_end: datetime, now: datetime, min_age_days: int
) -> Optional[str]:
    age = now - period_end
    if age < timedelta(days=min_age_days):
        return f"Period ended {age.total_seconds() / 3600:.1f}h ago; statements not final until T+{min_age_days} days."
    return None


def _allocation(
    db: Session,
    *,
    provider: str,
    period_start: datetime,
    period_end: datetime,
    drift_micros: int,
) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            UsageRollup.organization_id,
            func.coalesce(func.sum(UsageRollup.cost_micros), 0),
        )
        .where(
            UsageRollup.grain == "DETAIL",
            UsageRollup.granularity == rollup_service.HOUR,
            UsageRollup.provider == provider,
            UsageRollup.bucket_start >= period_start,
            UsageRollup.bucket_start < period_end,
        )
        .group_by(UsageRollup.organization_id)
    ).all()

    total = sum(int(row[1]) for row in rows)
    if not total:
        return []

    return [
        {
            "organization_id": str(row[0]),
            "ledger_cost_micros": int(row[1]),
            "share": float(Decimal(int(row[1])) / Decimal(total)),
            "allocated_drift_micros": int(
                Decimal(drift_micros) * Decimal(int(row[1])) / Decimal(total)
            ),
            "measured": False,
            "basis": "ledger_cost_share",
        }
        for row in rows
    ]


def reconcile(
    db: Session,
    *,
    provider: str,
    period_start: datetime,
    period_end: datetime,
    statement: ProviderStatement,
    now: Optional[datetime] = None,
    boundary_hours: Optional[int] = None,
    min_age_days: Optional[int] = None,
    alert_bps: int = DRIFT_ALERT_BPS,
) -> ReconciliationRun:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = period_start.astimezone(timezone.utc)
    end = period_end.astimezone(timezone.utc)
    window = int(
        boundary_hours
        if boundary_hours is not None
        else getattr(settings, "RECONCILE_BOUNDARY_HOURS", 6)
    )
    min_age = int(
        min_age_days
        if min_age_days is not None
        else getattr(settings, "RECONCILE_MIN_AGE_DAYS", 2)
    )

    run = ReconciliationRun(
        provider=provider,
        period_start=start,
        period_end=end,
        provider_statement_id=statement.id,
        grain=statement.grain,
        attribution=statement.attribution,
        status=ReconciliationStatus.RUNNING.value,
        started_at=moment,
    )
    db.add(run)
    db.flush([run])

    refusal = _eligibility(period_end=end, now=moment, min_age_days=min_age)
    if refusal:
        run.status = ReconciliationStatus.REFUSED.value
        run.completed_at = moment
        run.details = {"refused_because": refusal, "min_age_days": min_age}
        db.flush([run])
        logger.warning(
            "reconciliation.refused",
            extra={"provider": provider, "period_start": start.isoformat()},
        )
        raise ReconciliationRefused(refusal)

    ledger = ledger_side(
        db,
        provider=provider,
        period_start=start,
        period_end=end,
        boundary_hours=window,
    )
    provider_side, unmapped = statement_side(statement)

    ledger_total = sum(item.cost_micros for item in ledger.values())
    statement_total = int(statement.total_cost_micros)
    drift = statement_total - ledger_total

    specs: list[FindingSpec] = []
    for key in sorted(
        set(ledger) | set(provider_side), key=lambda k: (k[0] or "", k[1] or "")
    ):
        specs.extend(
            categorise_pair(
                ledger=ledger.get(key, LedgerSlice(model=key[0], event_type=key[1])),
                statement=provider_side.get(
                    key, StatementSlice(model=key[0], event_type=key[1])
                ),
            )
        )

    if unmapped:
        unmapped_cost = sum(int(line.cost_micros) for line in unmapped)
        skus = sorted({line.sku for line in unmapped if line.sku})[:20]
        specs.append(
            FindingSpec(
                category=ReconciliationCategory.UNEXPLAINED,
                severity=(
                    FindingSeverity.HIGH
                    if abs(_bps(unmapped_cost, statement_total)) > alert_bps
                    else FindingSeverity.WARNING
                ),
                drift_micros=unmapped_cost,
                explanation=f"{len(unmapped)} statement lines carry an unmapped SKU.",
                statement_cost_micros=unmapped_cost,
                details={"skus": skus, "line_count": len(unmapped)},
            )
        )

    attributed = sum(spec.drift_micros for spec in specs)
    if attributed != drift:
        specs.append(
            FindingSpec(
                category=ReconciliationCategory.UNEXPLAINED,
                severity=FindingSeverity.WARNING,
                drift_micros=drift - attributed,
                explanation="Run-level rounding residue.",
                details={"pair_count": len(specs)},
            )
        )

    for spec in specs:
        db.add(
            ReconciliationFinding(
                reconciliation_run_id=run.id,
                category=spec.category.value,
                severity=spec.severity.value,
                attribution=statement.attribution,
                organization_id=None,
                provider=provider,
                model=spec.model,
                event_type=spec.event_type,
                ledger_quantity=spec.ledger_quantity,
                statement_quantity=spec.statement_quantity,
                ledger_cost_micros=spec.ledger_cost_micros,
                statement_cost_micros=spec.statement_cost_micros,
                drift_micros=spec.drift_micros,
                drift_bps=_bps(spec.drift_micros, statement_total or ledger_total),
                explanation=spec.explanation,
                details=spec.details or None,
            )
        )

    # Alert on UNMETERED_GENERATION or UNEXPLAINED drift exceeding threshold
    unexplained_drift = sum(
        spec.drift_micros
        for spec in specs
        if spec.category is ReconciliationCategory.UNEXPLAINED
    )
    unexplained_bps = _bps(unexplained_drift, statement_total or ledger_total)
    has_critical = any(
        spec.severity is FindingSeverity.CRITICAL for spec in specs
    )
    alert = bool(has_critical or abs(unexplained_bps) > alert_bps)

    details: dict[str, Any] = {
        "boundary_hours": window,
        "alert_bps_threshold": alert_bps,
        "ledger_pairs": len(ledger),
        "statement_pairs": len(provider_side),
        "unmapped_statement_lines": len(unmapped),
        "fidelity_note": (statement.details or {}).get("fidelity_note"),
        "categories": {
            category.value: sum(
                spec.drift_micros for spec in specs if spec.category is category
            )
            for category in CATEGORY_ORDER
        },
    }
    if statement.attribution != Attribution.ATTESTED.value and drift:
        details["allocation"] = _allocation(
            db,
            provider=provider,
            period_start=start,
            period_end=end,
            drift_micros=drift,
        )
        details["allocation_warning"] = (
            "Pro-rata by ledger cost share. Not measured. Do not display to customer."
        )

    run.status = ReconciliationStatus.COMPLETED.value
    run.completed_at = datetime.now(timezone.utc)
    run.ledger_cost_micros = ledger_total
    run.statement_cost_micros = statement_total
    run.drift_micros = drift
    run.drift_bps = _bps(drift, statement_total or ledger_total)
    run.findings_count = len(specs)
    run.alert_raised = alert
    run.details = details
    
    # Flush all findings and run to database
    db.flush()

    log = logger.critical if alert else logger.info
    log(
        "reconciliation.completed",
        extra={
            "provider": provider,
            "period_start": start.isoformat(),
            "ledger_cost_micros": ledger_total,
            "statement_cost_micros": statement_total,
            "drift_micros": drift,
            "drift_bps": str(run.drift_bps),
            "alert_raised": alert,
            "findings": len(specs),
        },
    )
    return run


def reconcile_provider(
    db: Session,
    *,
    provider: str,
    period_start: datetime,
    period_end: datetime,
    fetch_options: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
    imported_by_user_id: Optional[uuid.UUID] = None,
) -> ReconciliationRun:
    source_class = source_for(provider)
    payload = source_class().fetch(
        period_start=period_start,
        period_end=period_end,
        **(fetch_options or {}),
    )
    statement = persist_statement(
        db, payload, imported_by_user_id=imported_by_user_id
    )
    return reconcile(
        db,
        provider=provider,
        period_start=period_start,
        period_end=period_end,
        statement=statement,
        now=now,
    )


__all__ = [
    "LedgerSlice",
    "ReconciliationError",
    "ReconciliationRefused",
    "StatementSlice",
    "categorise_pair",
    "ledger_side",
    "persist_statement",
    "reconcile",
    "reconcile_provider",
    "statement_side",
]