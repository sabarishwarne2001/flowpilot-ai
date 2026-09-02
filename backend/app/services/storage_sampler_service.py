"""ARCH-10 Step 4 — the storage sampler."""

from __future__ import annotations

import calendar
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.principal import system_principal
from app.core.storage import StorageNamespace, tenant_prefix
from app.models.uploaded_file import UploadedFile
from app.models.usage_event import UsageEvent
from app.services import usage_service

logger = logging.getLogger("app.services.storage_sampler")

BYTES_PER_GB = Decimal(10**9)
USAGE_TYPE = "storage.gb_month"

MAX_FIRST_SAMPLE_HOURS = 24


@dataclass(frozen=True)
class TenantSample:
    organization_id: uuid.UUID
    live_bytes: int
    file_count: int
    elapsed_hours: Decimal
    gb_months: Decimal
    recorded: bool
    reason: str = ""


@dataclass(frozen=True)
class SampleRun:
    sampled_at: datetime
    tenants: list[TenantSample]

    @property
    def recorded_count(self) -> int:
        return sum(1 for t in self.tenants if t.recorded)

    @property
    def total_bytes(self) -> int:
        return sum(t.live_bytes for t in self.tenants)

    def as_result(self) -> dict[str, Any]:
        return {
            "sampled_at": self.sampled_at.isoformat(),
            "tenants_seen": len(self.tenants),
            "tenants_recorded": self.recorded_count,
            "total_live_bytes": self.total_bytes,
            "skipped": [
                {"organization_id": str(t.organization_id), "reason": t.reason}
                for t in self.tenants
                if not t.recorded
            ],
        }


def _hours_in_month(moment: datetime) -> Decimal:
    return Decimal(calendar.monthrange(moment.year, moment.month)[1]) * Decimal(24)


def _interval_bucket(moment: datetime, interval_minutes: int) -> str:
    minutes = (moment.hour * 60 + moment.minute) // interval_minutes * interval_minutes
    return f"{moment:%Y%m%d}T{minutes // 60:02d}{minutes % 60:02d}"


def _live_bytes_per_tenant(db: Session) -> list[tuple[uuid.UUID, int, int]]:
    stmt = (
        select(
            UploadedFile.organization_id,
            func.coalesce(func.sum(UploadedFile.file_size), 0),
            func.count(),
        )
        .where(
            UploadedFile.organization_id.is_not(None),
            UploadedFile.deleted_at.is_(None),
        )
        .group_by(UploadedFile.organization_id)
    )
    return [(row[0], int(row[1]), int(row[2])) for row in db.execute(stmt).all()]


def _last_sample_at(db: Session, organization_id: uuid.UUID) -> Optional[datetime]:
    stmt = select(func.max(UsageEvent.occurred_at)).where(
        UsageEvent.organization_id == organization_id,
        UsageEvent.event_type == USAGE_TYPE,
    )
    return db.execute(stmt).scalar_one_or_none()


def sample_once(
    db: Session,
    *,
    now: Optional[datetime] = None,
    interval_minutes: Optional[int] = None,
) -> SampleRun:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    interval = interval_minutes or settings.STORAGE_SAMPLE_INTERVAL_MINUTES
    bucket = _interval_bucket(moment, interval)
    hours_in_month = _hours_in_month(moment)

    samples: list[TenantSample] = []

    for organization_id, live_bytes, file_count in _live_bytes_per_tenant(db):
        if live_bytes <= 0:
            samples.append(
                TenantSample(
                    organization_id=organization_id,
                    live_bytes=0,
                    file_count=file_count,
                    elapsed_hours=Decimal(0),
                    gb_months=Decimal(0),
                    recorded=False,
                    reason="no live bytes",
                )
            )
            continue

        last = _last_sample_at(db, organization_id)
        if last is None:
            elapsed_hours = Decimal(min(interval / 60, MAX_FIRST_SAMPLE_HOURS))
        else:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed_hours = Decimal(
                (moment - last).total_seconds()
            ) / Decimal(3600)

        if elapsed_hours <= 0:
            samples.append(
                TenantSample(
                    organization_id=organization_id,
                    live_bytes=live_bytes,
                    file_count=file_count,
                    elapsed_hours=Decimal(0),
                    gb_months=Decimal(0),
                    recorded=False,
                    reason="already sampled at or after this instant",
                )
            )
            continue

        gb = Decimal(live_bytes) / BYTES_PER_GB
        gb_months = (gb * (elapsed_hours / hours_in_month)).quantize(
            Decimal("0.000001")
        )

        if gb_months <= 0:
            samples.append(
                TenantSample(
                    organization_id=organization_id,
                    live_bytes=live_bytes,
                    file_count=file_count,
                    elapsed_hours=elapsed_hours,
                    gb_months=Decimal(0),
                    recorded=False,
                    reason="rounds to zero at 6dp",
                )
            )
            continue

        key = f"storage:{organization_id}:{bucket}"
        savepoint = db.begin_nested()
        try:
            usage_service.record_usage(
                db,
                organization_id=organization_id,
                event_type=USAGE_TYPE,
                quantity=gb_months,
                provider="internal",
                idempotency_key=key,
                occurred_at=moment,
                allow_sampled=True,
                details={
                    "live_bytes": live_bytes,
                    "file_count": file_count,
                    "elapsed_hours": str(elapsed_hours),
                    "hours_in_month": str(hours_in_month),
                    "interval_bucket": bucket,
                },
            )
            savepoint.commit()
            recorded, reason = True, ""
        except IntegrityError:
            savepoint.rollback()
            recorded, reason = False, "duplicate sample for this interval"
            logger.info(
                "storage_sampler.duplicate",
                extra={"organization_id": str(organization_id), "bucket": bucket},
            )

        samples.append(
            TenantSample(
                organization_id=organization_id,
                live_bytes=live_bytes,
                file_count=file_count,
                elapsed_hours=elapsed_hours,
                gb_months=gb_months,
                recorded=recorded,
                reason=reason,
            )
        )

    run = SampleRun(sampled_at=moment, tenants=samples)
    logger.info("storage_sampler.run", extra=run.as_result())
    return run


def reconcile(
    db: Session, *, organization_id: uuid.UUID, tolerance_ratio: float = 0.02
) -> dict[str, Any]:
    from app.core.storage import get_storage_driver

    stmt = select(
        func.coalesce(func.sum(UploadedFile.file_size), 0), func.count()
    ).where(
        UploadedFile.organization_id == organization_id,
        UploadedFile.deleted_at.is_(None),
    )
    db_bytes, db_count = db.execute(stmt).one()
    db_bytes, db_count = int(db_bytes), int(db_count)

    driver = get_storage_driver()
    bucket_bytes, bucket_count = driver.usage_bytes(
        tenant_prefix(
            organization_id=organization_id, namespace=StorageNamespace.DOCUMENTS
        )
    )

    delta = bucket_bytes - db_bytes
    within = abs(delta) <= max(1, int(db_bytes * tolerance_ratio))
    report = {
        "organization_id": str(organization_id),
        "db_bytes": db_bytes,
        "db_objects": db_count,
        "bucket_bytes": bucket_bytes,
        "bucket_objects": bucket_count,
        "delta_bytes": delta,
        "within_tolerance": within,
    }
    if not within:
        logger.warning("storage_sampler.drift", extra=report)
    return report


def handle_storage_sample(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal

    interval = payload.get("interval_minutes")
    with SessionLocal() as db:
        with system_principal(job_name="jobs.storage.sample"):
            run = sample_once(
                db, interval_minutes=int(interval) if interval else None
            )
        db.commit()
    return run.as_result()
