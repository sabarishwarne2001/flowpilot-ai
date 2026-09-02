"""
ARCH-17 — SLO target resolution, measurement and summarisation.
"""

from __future__ import annotations

import calendar
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.slo_registry import SLO_REGISTRY, SLOSpec, spec_for
from app.models.slo import (
    SLODefinition,
    SLOMeasurement,
    SLOMethod,
    SLOObservation,
    SLOUnit,
    SLOWindow,
    bucket_bounds_for,
)

logger = logging.getLogger("app.services.slo_service")

LATENCY_PERCENTILE: float = 0.95
MIN_SAMPLES_FOR_CONTRACTUAL_BREACH: int = 100


class SLOServiceError(Exception):
    pass


class MeasurementSealedError(SLOServiceError):
    """A sealed window cannot be rewritten."""


def window_start_for(window: SLOWindow, moment: datetime) -> datetime:
    at = moment.astimezone(timezone.utc)
    if window is SLOWindow.HOUR:
        return at.replace(minute=0, second=0, microsecond=0)
    if window is SLOWindow.DAY:
        return at.replace(hour=0, minute=0, second=0, microsecond=0)
    return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def window_end_for(window: SLOWindow, start: datetime) -> datetime:
    if window is SLOWindow.HOUR:
        return start + timedelta(hours=1)
    if window is SLOWindow.DAY:
        return start + timedelta(days=1)
    return start + timedelta(days=calendar.monthrange(start.year, start.month)[1])


@dataclass(frozen=True)
class EffectiveSLO:
    slo_key: str
    display_name: str
    description: str
    unit: SLOUnit
    target_value: Decimal
    window_period: SLOWindow
    is_contractual: bool
    source: str
    definition_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None

    @property
    def is_ceiling(self) -> bool:
        return self.unit.is_ceiling

    def is_breach(self, observed: Decimal) -> bool:
        if self.is_ceiling:
            return observed > self.target_value
        return observed < self.target_value


def _from_spec(spec: SLOSpec) -> EffectiveSLO:
    return EffectiveSLO(
        slo_key=spec.key,
        display_name=spec.display_name,
        description=spec.description,
        unit=spec.unit,
        target_value=Decimal(str(spec.default_target)),
        window_period=spec.default_window,
        is_contractual=False,
        source="REGISTRY_DEFAULT",
    )


def _from_definition(definition: SLODefinition, spec: SLOSpec) -> EffectiveSLO:
    return EffectiveSLO(
        slo_key=definition.slo_key,
        display_name=definition.display_name or spec.display_name,
        description=spec.description,
        unit=definition.unit,
        target_value=Decimal(definition.target_value),
        window_period=definition.window_period,
        is_contractual=definition.is_contractual,
        source="ORGANIZATION" if definition.organization_id else "PLATFORM_DEFAULT",
        definition_id=definition.id,
        notes=definition.notes,
    )


def resolve_slo_targets(
    db: Session, organization_id: uuid.UUID
) -> list[EffectiveSLO]:
    rows = db.execute(
        select(SLODefinition).where(
            SLODefinition.organization_id.in_([organization_id, None])
            | SLODefinition.organization_id.is_(None)
        )
    ).scalars().all()

    tenant: dict[str, SLODefinition] = {}
    platform: dict[str, SLODefinition] = {}
    for row in rows:
        if row.organization_id == organization_id:
            tenant[row.slo_key] = row
        elif row.organization_id is None:
            platform[row.slo_key] = row

    resolved: list[EffectiveSLO] = []
    for key, spec in SLO_REGISTRY.items():
        definition = tenant.get(key) or platform.get(key)
        resolved.append(
            _from_definition(definition, spec) if definition else _from_spec(spec)
        )

    resolved.sort(key=lambda item: item.slo_key)
    return resolved


def resolve_one(
    db: Session, *, organization_id: uuid.UUID, slo_key: str
) -> EffectiveSLO:
    spec = spec_for(slo_key)
    definition = db.execute(
        select(SLODefinition)
        .where(
            SLODefinition.slo_key == slo_key,
            SLODefinition.organization_id == organization_id,
        )
        .limit(1)
    ).scalar_one_or_none()

    if definition is None:
        definition = db.execute(
            select(SLODefinition)
            .where(
                SLODefinition.slo_key == slo_key,
                SLODefinition.organization_id.is_(None),
            )
            .limit(1)
        ).scalar_one_or_none()

    return _from_definition(definition, spec) if definition else _from_spec(spec)


@dataclass(frozen=True)
class Histogram:
    bounds: tuple[float, ...]
    counts: tuple[int, ...]
    sample_count: int
    error_count: int
    sum_value: Decimal

    @property
    def is_empty(self) -> bool:
        return self.sample_count == 0

    def count_at_or_below(self, value: float) -> Optional[int]:
        for index, bound in enumerate(self.bounds):
            if bound == value:
                return sum(self.counts[: index + 1])
        return None

    def percentile(self, quantile: float) -> Decimal:
        if self.is_empty or not self.bounds:
            return Decimal("0")

        target_rank = quantile * self.sample_count
        cumulative = 0
        lower = 0.0

        for index, bound in enumerate(self.bounds):
            count = self.counts[index] if index < len(self.counts) else 0
            if cumulative + count >= target_rank:
                if count == 0:
                    return Decimal(str(round(bound, 4)))
                within = (target_rank - cumulative) / count
                value = lower + (bound - lower) * within
                return Decimal(str(round(value, 4)))
            cumulative += count
            lower = bound

        return Decimal(str(round(self.bounds[-1], 4)))

    @property
    def success_ratio(self) -> Decimal:
        if self.is_empty:
            return Decimal("0")
        good = self.sample_count - self.error_count
        return Decimal(good) / Decimal(self.sample_count)


def _merge(observations: Sequence[SLOObservation]) -> Histogram:
    usable = [row for row in observations if row.sample_count]
    if not usable:
        return Histogram((), (), 0, 0, Decimal("0"))

    schedules: dict[tuple[float, ...], int] = {}
    for row in usable:
        schedules[tuple(row.bucket_bounds or [])] = (
            schedules.get(tuple(row.bucket_bounds or []), 0) + row.sample_count
        )
    dominant = max(schedules, key=lambda bounds: schedules[bounds])

    if len(schedules) > 1:
        logger.warning(
            "slo.mixed_bucket_schedules",
            extra={
                "schedules": len(schedules),
                "kept_samples": schedules[dominant],
                "dropped_samples": sum(schedules.values()) - schedules[dominant],
            },
        )

    counts = [0] * (len(dominant) + 1)
    samples = 0
    errors = 0
    total = Decimal("0")

    for row in usable:
        if tuple(row.bucket_bounds or []) != dominant:
            continue
        samples += row.sample_count
        errors += row.error_count
        total += Decimal(row.sum_value)
        for index, value in enumerate(row.bucket_counts or []):
            if index < len(counts):
                counts[index] += int(value)

    return Histogram(dominant, tuple(counts), samples, errors, total)


def load_histogram(
    db: Session,
    *,
    organization_id: uuid.UUID,
    slo_key: str,
    window_start: datetime,
    window_end: datetime,
) -> Histogram:
    rows = db.execute(
        select(SLOObservation).where(
            SLOObservation.organization_id == organization_id,
            SLOObservation.slo_key == slo_key,
            SLOObservation.window_start >= window_start,
            SLOObservation.window_start < window_end,
        )
    ).scalars().all()
    return _merge(rows)


def _ensure_definition(
    db: Session, *, organization_id: uuid.UUID, effective: EffectiveSLO
) -> SLODefinition:
    if effective.definition_id is not None:
        definition = db.get(SLODefinition, effective.definition_id)
        if definition is not None:
            return definition

    existing = db.execute(
        select(SLODefinition).where(
            SLODefinition.slo_key == effective.slo_key,
            SLODefinition.organization_id.is_(None),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    definition = SLODefinition(
        slo_key=effective.slo_key,
        organization_id=None,
        target_value=effective.target_value,
        unit=effective.unit,
        window_period=effective.window_period,
        is_contractual=False,
        display_name=effective.display_name,
        notes="Materialised from the registry default by the first measurement.",
    )
    db.add(definition)
    db.flush([definition])
    return definition


def record_measurement(
    db: Session,
    *,
    organization_id: uuid.UUID,
    slo_key: str,
    window_start: Optional[datetime] = None,
    at: Optional[datetime] = None,
    seal: bool = False,
) -> SLOMeasurement:
    moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    effective = resolve_one(db, organization_id=organization_id, slo_key=slo_key)

    start = window_start or window_start_for(effective.window_period, moment)
    end = window_end_for(effective.window_period, start)

    definition = _ensure_definition(
        db, organization_id=organization_id, effective=effective
    )

    existing = db.execute(
        select(SLOMeasurement).where(
            SLOMeasurement.slo_definition_id == definition.id,
            SLOMeasurement.organization_id == organization_id,
            SLOMeasurement.window_start == start,
        )
    ).scalar_one_or_none()

    if existing is not None and existing.sealed_at is not None:
        raise MeasurementSealedError(
            f"{slo_key} for {organization_id} at {start.isoformat()} was sealed "
            f"at {existing.sealed_at.isoformat()}. A sealed window is final; "
            f"compute a correction as a new window rather than rewriting it."
        )

    histogram = load_histogram(
        db,
        organization_id=organization_id,
        slo_key=slo_key,
        window_start=start,
        window_end=end,
    )

    details: dict[str, Any] = {
        "percentile": LATENCY_PERCENTILE,
        "bucket_bounds": list(histogram.bounds),
        "target_is_bucket_boundary": False,
    }

    if effective.unit is SLOUnit.RATIO:
        observed = histogram.success_ratio
        method = SLOMethod.EXACT
        breached = (not histogram.is_empty) and effective.is_breach(observed)
    else:
        observed = histogram.percentile(LATENCY_PERCENTILE)
        method = SLOMethod.HISTOGRAM_INTERPOLATED

        exact_bound = float(effective.target_value)
        at_or_below = histogram.count_at_or_below(exact_bound)
        details["target_is_bucket_boundary"] = at_or_below is not None

        if histogram.is_empty:
            breached = False
        elif at_or_below is not None:
            required = LATENCY_PERCENTILE * histogram.sample_count
            breached = at_or_below < required
            details["samples_at_or_below_target"] = at_or_below
            details["samples_required"] = round(required, 2)
        else:
            breached = effective.is_breach(observed)
            details["breach_from_interpolation"] = True

    if (
        breached
        and effective.is_contractual
        and histogram.sample_count < MIN_SAMPLES_FOR_CONTRACTUAL_BREACH
    ):
        breached = False
        details["suppressed_low_sample_breach"] = True
        details["min_samples"] = MIN_SAMPLES_FOR_CONTRACTUAL_BREACH

    if histogram.sample_count:
        details["mean_value"] = float(
            round(histogram.sum_value / histogram.sample_count, 4)
        )

    if existing is None:
        measurement = SLOMeasurement(
            slo_definition_id=definition.id,
            organization_id=organization_id,
            slo_key=slo_key,
            window_start=start,
            window_end=end,
        )
        db.add(measurement)
    else:
        measurement = existing

    measurement.observed_value = observed
    measurement.target_value = effective.target_value
    measurement.unit = effective.unit
    measurement.method = method
    measurement.sample_count = histogram.sample_count
    measurement.error_count = histogram.error_count
    measurement.breached = breached
    measurement.is_contractual = effective.is_contractual
    measurement.details = details
    if seal:
        measurement.sealed_at = datetime.now(timezone.utc)

    db.flush([measurement])

    logger.info(
        "slo.measured",
        extra={
            "organization_id": str(organization_id),
            "slo_key": slo_key,
            "window_start": start.isoformat(),
            "observed": str(observed),
            "target": str(effective.target_value),
            "samples": histogram.sample_count,
            "breached": breached,
            "sealed": seal,
            "method": method.value,
        },
    )
    return measurement


def seal_due(
    db: Session, *, at: Optional[datetime] = None, limit: int = 500
) -> int:
    moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)

    pending = db.execute(
        select(
            SLOMeasurement.organization_id,
            SLOMeasurement.slo_key,
            SLOMeasurement.window_start,
        )
        .where(
            SLOMeasurement.sealed_at.is_(None),
            SLOMeasurement.window_end <= moment,
        )
        .order_by(SLOMeasurement.window_start)
        .limit(limit)
    ).all()

    sealed = 0
    for organization_id, slo_key, window_start in pending:
        try:
            record_measurement(
                db,
                organization_id=organization_id,
                slo_key=slo_key,
                window_start=window_start,
                at=moment,
                seal=True,
            )
            sealed += 1
        except MeasurementSealedError:
            continue
        except Exception:  # noqa: BLE001
            logger.exception(
                "slo.seal_failed",
                extra={"organization_id": str(organization_id), "slo_key": slo_key},
            )
    return sealed


@dataclass(frozen=True)
class SLOSummaryEntry:
    effective: EffectiveSLO
    observed_value: Optional[Decimal]
    sample_count: int
    error_count: int
    breached: bool
    method: Optional[SLOMethod]
    window_start: datetime
    window_end: datetime
    sealed_at: Optional[datetime]
    breached_windows: int
    total_windows: int

    @property
    def compliance_ratio(self) -> Optional[Decimal]:
        if self.total_windows == 0:
            return None
        good = self.total_windows - self.breached_windows
        return Decimal(good) / Decimal(self.total_windows)


@dataclass(frozen=True)
class SLOSummary:
    organization_id: uuid.UUID
    as_of: datetime
    period: SLOWindow
    entries: list[SLOSummaryEntry]

    @property
    def contractual_breaches(self) -> int:
        return sum(
            1
            for entry in self.entries
            if entry.breached and entry.effective.is_contractual
        )


def _trailing_start(period: SLOWindow, moment: datetime) -> datetime:
    if period is SLOWindow.HOUR:
        return moment - timedelta(hours=24)
    if period is SLOWindow.DAY:
        return moment - timedelta(days=30)
    return moment - timedelta(days=365)


def get_tenant_slo_summary(
    db: Session,
    organization_id: uuid.UUID,
    period: SLOWindow = SLOWindow.DAY,
    *,
    at: Optional[datetime] = None,
) -> SLOSummary:
    moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    targets = resolve_slo_targets(db, organization_id)
    since = _trailing_start(period, moment)

    history = db.execute(
        select(
            SLOMeasurement.slo_key,
            SLOMeasurement.breached,
            SLOMeasurement.window_start,
        ).where(
            SLOMeasurement.organization_id == organization_id,
            SLOMeasurement.sealed_at.isnot(None),
            SLOMeasurement.window_start >= since,
        )
    ).all()

    windows: dict[str, list[bool]] = {}
    for slo_key, breached, _ in history:
        windows.setdefault(slo_key, []).append(bool(breached))

    entries: list[SLOSummaryEntry] = []
    for effective in targets:
        start = window_start_for(effective.window_period, moment)
        end = window_end_for(effective.window_period, start)

        histogram = load_histogram(
            db,
            organization_id=organization_id,
            slo_key=effective.slo_key,
            window_start=start,
            window_end=end,
        )

        if histogram.is_empty:
            observed: Optional[Decimal] = None
            method: Optional[SLOMethod] = None
            breached = False
        elif effective.unit is SLOUnit.RATIO:
            observed = histogram.success_ratio
            method = SLOMethod.EXACT
            breached = effective.is_breach(observed)
        else:
            observed = histogram.percentile(LATENCY_PERCENTILE)
            method = SLOMethod.HISTOGRAM_INTERPOLATED
            at_or_below = histogram.count_at_or_below(float(effective.target_value))
            if at_or_below is not None:
                breached = at_or_below < LATENCY_PERCENTILE * histogram.sample_count
            else:
                breached = effective.is_breach(observed)

        seen = windows.get(effective.slo_key, [])
        entries.append(
            SLOSummaryEntry(
                effective=effective,
                observed_value=observed,
                sample_count=histogram.sample_count,
                error_count=histogram.error_count,
                breached=breached,
                method=method,
                window_start=start,
                window_end=end,
                sealed_at=None,
                breached_windows=sum(1 for value in seen if value),
                total_windows=len(seen),
            )
        )

    return SLOSummary(
        organization_id=organization_id,
        as_of=moment,
        period=period,
        entries=entries,
    )


def set_target(
    db: Session,
    *,
    organization_id: uuid.UUID,
    slo_key: str,
    target_value: Decimal,
    window_period: Optional[SLOWindow] = None,
    is_contractual: bool = False,
    notes: Optional[str] = None,
) -> SLODefinition:
    spec = spec_for(slo_key)

    if spec.unit is SLOUnit.RATIO and not (
        Decimal("0") <= target_value <= Decimal("1")
    ):
        raise SLOServiceError(
            f"{slo_key} is a ratio; target must be between 0 and 1 "
            f"(got {target_value}). 99.9% is 0.999."
        )
    if target_value < 0:
        raise SLOServiceError("A target cannot be negative.")

    definition = db.execute(
        select(SLODefinition)
        .where(
            SLODefinition.slo_key == slo_key,
            SLODefinition.organization_id == organization_id,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if definition is None:
        definition = SLODefinition(
            slo_key=slo_key,
            organization_id=organization_id,
            unit=spec.unit,
            display_name=spec.display_name,
        )
        db.add(definition)

    definition.target_value = target_value
    definition.window_period = window_period or definition.window_period or spec.default_window
    definition.is_contractual = is_contractual
    definition.notes = notes
    db.flush([definition])

    logger.info(
        "slo.target_set",
        extra={
            "organization_id": str(organization_id),
            "slo_key": slo_key,
            "target": str(target_value),
            "contractual": is_contractual,
        },
    )
    return definition


def delete_target(
    db: Session, *, organization_id: uuid.UUID, slo_key: str
) -> bool:
    definition = db.execute(
        select(SLODefinition).where(
            SLODefinition.slo_key == slo_key,
            SLODefinition.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if definition is None:
        return False
    db.delete(definition)
    db.flush()
    return True


def seed_platform_defaults(db: Session) -> int:
    created = 0
    for spec in SLO_REGISTRY.values():
        exists = db.execute(
            select(SLODefinition.id).where(
                SLODefinition.slo_key == spec.key,
                SLODefinition.organization_id.is_(None),
            )
        ).first()
        if exists:
            continue
        db.add(
            SLODefinition(
                slo_key=spec.key,
                organization_id=None,
                target_value=Decimal(str(spec.default_target)),
                unit=spec.unit,
                window_period=spec.default_window,
                is_contractual=False,
                display_name=spec.display_name,
            )
        )
        created += 1
    db.flush()
    return created


__all__ = [
    "EffectiveSLO",
    "Histogram",
    "LATENCY_PERCENTILE",
    "MIN_SAMPLES_FOR_CONTRACTUAL_BREACH",
    "MeasurementSealedError",
    "SLOServiceError",
    "SLOSummary",
    "SLOSummaryEntry",
    "delete_target",
    "get_tenant_slo_summary",
    "load_histogram",
    "record_measurement",
    "resolve_one",
    "resolve_slo_targets",
    "seal_due",
    "seed_platform_defaults",
    "set_target",
    "window_end_for",
    "window_start_for",
]
