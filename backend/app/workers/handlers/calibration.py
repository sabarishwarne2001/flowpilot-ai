"""ARCH-35 §6.6 — the `calibration.harvest` (hourly) and `calibration.refit`
(nightly) jobs.

WHY BOTH ARE ON THE LIGHT PROFILE
=================================

A fit is at most 20,000 labels through scikit-learn's isotonic regression (a
single O(n log n) pass) or a two-parameter Newton iteration, then a sorted
sweep for the threshold and one SciPy quantile. Milliseconds. Nothing here
imports SentenceTransformers, PaddleOCR or pypdfium.

The three-place rule applies exactly as it does to every job since ARCH-16:
both job types are in the handler map, in the LIGHT profile, and in
DEFAULT_SCHEDULE. `assert_imports_match_profile()` raises `ProfileError` at
every worker's startup on a handler no profile claims.

WHAT EACH JOB DOES
==================

`calibration.harvest` — hourly, per entitled organization: copy new reviewer
outcomes into `calibration_labels` (a full backfill the first time), then fit
a FIRST version for any decision type that has labels and no model. The first
fit is here rather than waiting for the nightly job so a newly entitled tenant
waits at most an hour for its first promise, not a day.

`calibration.refit` — nightly, per entitled organization, per decision type:
refit when the input digest moved, then run the drift monitor on the model in
force. The monitor runs every night even when nothing was refitted: that is
what keeps `last_checked_at` fresh, and a model that stops being checked stops
being trusted (`STALE_AFTER_HOURS`).

ONE TENANT CANNOT STOP THE PASS
===============================

Each organization is committed on its own, and a failure rolls back that
organization only — the same arrangement ARCH-34's nightly sweep uses.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import select

from app.models.organization import Organization, OrganizationStatus
from app.services.calibration import apply as apply_module
from app.services.calibration import labels as labels_module
from app.services.calibration import refit as refit_module
from app.services.calibration import vocabulary as vocab

logger = logging.getLogger("app.workers.handlers.calibration")

__all__ = [
    "handle_calibration_harvest",
    "handle_calibration_refit",
    "ORGANIZATION_PAGE",
]

#: Organizations read per page. The pass walks every active organization by
#: keyset, so a large estate is covered completely rather than the same first
#: page every night.
ORGANIZATION_PAGE: int = 200


def _organizations(db: Any, payload: dict[str, Any]) -> Iterator[uuid.UUID]:
    only = payload.get("organization_id")
    if only:
        yield uuid.UUID(str(only))
        return
    after: uuid.UUID | None = None
    while True:
        stmt = (
            select(Organization.id)
            .where(Organization.status == OrganizationStatus.ACTIVE)
            .order_by(Organization.id)
            .limit(ORGANIZATION_PAGE)
        )
        if after is not None:
            stmt = stmt.where(Organization.id > after)
        page = list(db.execute(stmt).scalars().all())
        if not page:
            return
        yield from page
        after = page[-1]


def handle_calibration_harvest(db: Any, payload: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    totals = {"organizations": 0, "entitled": 0, "labels": 0, "first_fits": 0, "failed": 0}
    for organization_id in _organizations(db, payload):
        totals["organizations"] += 1
        try:
            if not apply_module.autonomy_enabled(db, organization_id=organization_id):
                db.rollback()
                continue
            totals["entitled"] += 1
            result = labels_module.harvest(
                db,
                organization_id=organization_id,
                now=now,
                full=bool(payload.get("full")),
            )
            totals["labels"] += result.total_inserted
            for decision_type in vocab.DECISION_TYPES:
                if refit_module.latest_model(
                    db, organization_id=organization_id, decision_type=decision_type
                ) is not None:
                    continue
                if labels_module.label_count(
                    db, organization_id=organization_id, decision_type=decision_type
                ) == 0:
                    continue
                refit_module.refit(
                    db,
                    organization_id=organization_id,
                    decision_type=decision_type,
                    now=now,
                )
                totals["first_fits"] += 1
            db.commit()
        except Exception:  # noqa: BLE001 - one tenant must not stop the pass
            db.rollback()
            totals["failed"] += 1
            logger.exception(
                "calibration.harvest_failed",
                extra={"organization_id": str(organization_id)},
            )
    logger.info("calibration.harvest_pass", extra=totals)
    return {"status": "OK", **totals}


def handle_calibration_refit(db: Any, payload: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    totals = {
        "organizations": 0,
        "entitled": 0,
        "fitted": 0,
        "unchanged": 0,
        "rejected": 0,
        "held": 0,
        "checked": 0,
        "suspended": 0,
        "failed": 0,
    }
    for organization_id in _organizations(db, payload):
        totals["organizations"] += 1
        try:
            if not apply_module.autonomy_enabled(db, organization_id=organization_id):
                db.rollback()
                continue
            totals["entitled"] += 1
            for decision_type in vocab.DECISION_TYPES:
                has_labels = labels_module.label_count(
                    db, organization_id=organization_id, decision_type=decision_type
                ) > 0
                has_model = refit_module.latest_model(
                    db, organization_id=organization_id, decision_type=decision_type
                ) is not None
                if not (has_labels or has_model):
                    continue
                result = refit_module.refit(
                    db,
                    organization_id=organization_id,
                    decision_type=decision_type,
                    now=now,
                )
                totals[result.outcome] = totals.get(result.outcome, 0) + 1
                live = refit_module.live_model(
                    db, organization_id=organization_id, decision_type=decision_type
                )
                if live is None:
                    continue
                verdict = refit_module.check(db, model=live, now=now)
                totals["checked"] += 1
                if verdict.suspend:
                    totals["suspended"] += 1
            db.commit()
        except Exception:  # noqa: BLE001 - one tenant must not stop the pass
            db.rollback()
            totals["failed"] += 1
            logger.exception(
                "calibration.refit_failed",
                extra={"organization_id": str(organization_id)},
            )
    logger.info("calibration.refit_pass", extra=totals)
    return {"status": "OK", **totals}
