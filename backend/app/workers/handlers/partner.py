"""ARCH-27 — scheduled revenue-share computation and sealing.

TWO JOB TYPES, AND WHY THEY ARE NOT ONE
=======================================

    partner.rev_share_compute   Rebuild the DRAFT statement for a closed
                                calendar month across every ACTIVE partner.
                                Idempotent, cheap to re-run, and safe to run
                                while numbers are still settling because a
                                DRAFT is not a promise.

    partner.rev_share_seal      Seal DRAFT statements whose month closed more
                                than SEAL_GRACE_DAYS ago, writing the digest
                                and freezing the row.

Folding them together would mean a single failure mid-sweep leaves some
partners sealed and some not, with no way to tell which without reading
statuses — and re-running would seal statements that were computed before the
failure rather than after it. Split, the compute pass is always safe to
repeat and the seal pass only ever acts on figures a previous pass produced.

WHY SEALING IS A JOB AND NOT A CONSEQUENCE OF COMPUTING
=======================================================

A grace period exists because `usage_rollups` absorbs late events until its
own MONTH bucket seals. `rev_share_service._refuse_unsealed()` already refuses
to compute over an unsealed bucket, so the compute pass simply skips a partner
whose month is not closed. Sealing then happens on a later tick, once every
input has settled — and a partner reading a DRAFT in the meantime is reading
something explicitly labelled as not final.

WHY ONE PARTNER'S FAILURE DOES NOT STOP THE SWEEP
=================================================

Each partner is committed independently. A single partner with a missing
agreement, an unsealed month or an unknown cost basis under a FAIL policy is
recorded in the result and skipped. The alternative — one transaction over
every partner — means the first misconfigured reseller blocks payouts for all
of them, which is the failure mode most likely to go unnoticed until somebody
asks where their money is.
"""

from __future__ import annotations

import calendar
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.partner import Partner, PartnerPayoutPeriod
from app.services.partner import rev_share_service
from app.services.partner.tenancy_service import PartnerError

logger = logging.getLogger("app.workers.handlers.partner")

#: Days after a month ends before its statement is sealed automatically. Long
#: enough for late usage events to land and for ARCH-14 to seal the MONTH
#: rollup; short enough that a reseller is not waiting on a quarter boundary.
SEAL_GRACE_DAYS: int = 5


def _previous_month(today: date) -> tuple[date, date]:
    """Inclusive bounds of the calendar month before `today`."""
    first_of_this = today.replace(day=1)
    last_of_previous = first_of_this - timedelta(days=1)
    start = last_of_previous.replace(day=1)
    return start, last_of_previous


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _resolve_window(payload: dict[str, Any]) -> tuple[date, date]:
    """Explicit `year`/`month` in the payload, else the previous month.

    An operator re-running a specific month is the ordinary reason to invoke
    this by hand, and making them compute the inclusive end date themselves is
    how the last day of a month goes missing from a statement.
    """
    year = payload.get("year")
    month = payload.get("month")
    if year and month:
        return _month_bounds(int(year), int(month))
    return _previous_month(datetime.now(timezone.utc).date())


def _active_partners(db: Session) -> list[Partner]:
    return list(
        db.execute(
            select(Partner)
            .where(Partner.status == "ACTIVE")
            .order_by(Partner.slug)
        )
        .scalars()
        .all()
    )


def handle_rev_share_compute(
    db: Session, payload: dict[str, Any]
) -> dict[str, Any]:
    """Rebuild DRAFT statements for one closed month across all partners."""
    period_start, period_end = _resolve_window(payload)
    only_partner_id: Optional[uuid.UUID] = None
    if payload.get("partner_id"):
        only_partner_id = uuid.UUID(str(payload["partner_id"]))

    computed: list[str] = []
    skipped: list[dict[str, str]] = []

    for partner in _active_partners(db):
        if only_partner_id is not None and partner.id != only_partner_id:
            continue
        try:
            period = rev_share_service.compute_period(
                db,
                partner=partner,
                period_start=period_start,
                period_end=period_end,
            )
            db.commit()
            computed.append(str(period.id))
        except PartnerError as exc:
            # Expected refusals: no agreement, unsealed month, FAIL policy on
            # an unknown cost basis. Recorded, not raised — see the module
            # docstring on why one partner must not stop the sweep.
            db.rollback()
            skipped.append({"partner": partner.slug, "reason": str(exc)})
            logger.info(
                "partner.rev_share_compute_skipped",
                extra={"partner_id": str(partner.id), "reason": str(exc)},
            )
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception(
                "partner.rev_share_compute_failed",
                extra={"partner_id": str(partner.id)},
            )
            skipped.append(
                {"partner": partner.slug, "reason": "UNEXPECTED_ERROR"}
            )

    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "computed": len(computed),
        "skipped": skipped,
    }


def handle_rev_share_seal(
    db: Session, payload: dict[str, Any]
) -> dict[str, Any]:
    """Seal DRAFT statements whose month closed longer than the grace period."""
    grace_days = int(payload.get("grace_days") or SEAL_GRACE_DAYS)
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=grace_days)

    drafts = list(
        db.execute(
            select(PartnerPayoutPeriod, Partner)
            .join(Partner, Partner.id == PartnerPayoutPeriod.partner_id)
            .where(
                PartnerPayoutPeriod.status == "DRAFT",
                PartnerPayoutPeriod.period_end < cutoff,
                Partner.status == "ACTIVE",
            )
            .order_by(PartnerPayoutPeriod.period_start)
        ).all()
    )

    sealed: list[str] = []
    failed: list[dict[str, str]] = []

    for period, partner in drafts:
        try:
            rev_share_service.seal_period(
                db, partner=partner, period=period
            )
            db.commit()
            sealed.append(str(period.id))
            logger.info(
                "partner.rev_share_sealed",
                extra={
                    "partner_id": str(partner.id),
                    "period_id": str(period.id),
                    "digest": period.content_digest,
                },
            )
        except PartnerError as exc:
            db.rollback()
            failed.append({"period_id": str(period.id), "reason": str(exc)})
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception(
                "partner.rev_share_seal_failed",
                extra={"period_id": str(period.id)},
            )
            failed.append(
                {"period_id": str(period.id), "reason": "UNEXPECTED_ERROR"}
            )

    return {
        "cutoff": cutoff.isoformat(),
        "grace_days": grace_days,
        "sealed": len(sealed),
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# Session-owning entry points, matching the ARCH-25 branding handlers.
# ---------------------------------------------------------------------------


def rev_share_compute(payload: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        return handle_rev_share_compute(db, payload)


def rev_share_seal(payload: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        return handle_rev_share_seal(db, payload)


__all__ = [
    "SEAL_GRACE_DAYS",
    "handle_rev_share_compute",
    "handle_rev_share_seal",
    "rev_share_compute",
    "rev_share_seal",
]