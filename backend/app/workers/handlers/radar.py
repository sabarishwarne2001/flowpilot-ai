"""ARCH-34 §5.5 — the `anomaly.scan_document` and `anomaly.nightly` jobs.

WHY THESE ARE ON THE LIGHT PROFILE
==================================

The whole detector set is integer arithmetic, a fixed 128-permutation MinHash
over `hashlib`, and dot products over vectors ARCH-11 already computed and
stored. Nothing here imports SentenceTransformers, PaddleOCR or pypdfium. The
drift detector reaches ARCH-33's family parsers, which are regular expressions
over `Decimal`.

This is not a convenience. `assert_imports_match_profile()` raises
`ProfileError` at every worker's startup on a handler no profile claims, so
both job types are registered in THREE places — here, in
`app/workers/profiles.py` under LIGHT, and (for the nightly one) in
`DEFAULT_SCHEDULE`. A handler registered here without the profile entry stops
the entire fleet booting.

WHY THE SCAN IS PER-DOCUMENT AND THE BATCH IS NIGHTLY
=====================================================

Duplicate detection has to be immediate: the value of telling somebody an
invoice is a duplicate collapses the moment it is paid, and payment runs
happen the same day documents arrive.

Price surge and contract drift are statistical. A surge needs a series and a
series does not change between two documents arriving; running it per document
would recompute the same median dozens of times a day to reach the same
answer. §5.3 puts them in a nightly batch for exactly that reason.

WHY THE NIGHTLY JOB ALSO SWEEPS DUPLICATES
==========================================

Because `anomaly.scan_document` can be missed. A worker that was down when a
document finished ingestion leaves that document never compared, and nothing
in the arrival path of the NEXT document knows to go back for it. The nightly
pass picks up any fingerprint with no finding and no recent comparison, which
is the same late-arrival argument `procurement.score`'s sweep makes.

IDEMPOTENCE MAKES THE NIGHTLY PASS FREE ON A SETTLED WORKSPACE
==============================================================

`findings.upsert` writes nothing when `input_digest` is unchanged, and
`sweep.meter` emits nothing when nothing was written. A workspace where
nothing moved costs one query pass and bills zero.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.radar import DocumentFingerprint
from app.models.work_item import WorkItem
from app.models.workspace import Workspace
from app.services.radar import fingerprint as fp
from app.services.radar import sweep as sweep_module
from app.services.radar import vocabulary as vocab

logger = logging.getLogger("app.workers.handlers.radar")

__all__ = [
    "handle_anomaly_scan_document",
    "handle_anomaly_nightly",
    "NIGHTLY_WORKSPACE_BATCH",
    "NIGHTLY_DOCUMENT_BATCH",
]

#: Workspaces one nightly tick will process. A ceiling rather than a full
#: scan, for the reason `procurement.score` gives: an unbounded pass on a
#: large estate holds a LIGHT worker for the whole interval and starves every
#: other job type on the queue.
NIGHTLY_WORKSPACE_BATCH: int = 50

#: Documents per workspace the nightly duplicate catch-up will compare.
NIGHTLY_DOCUMENT_BATCH: int = 100


def handle_anomaly_scan_document(db: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Fingerprint one document if needed, then compare it to its workspace.

    `payload.work_item_id` is required. A scan with no document is not a
    workspace sweep by another name — that is `anomaly.nightly` — and treating
    an empty payload as "do everything" is how a malformed enqueue turns into
    an unbounded pass.
    """
    raw = payload.get("work_item_id")
    if not raw:
        raise ValueError(
            "anomaly.scan_document requires work_item_id. A workspace-wide "
            "pass is anomaly.nightly; running one here would let a malformed "
            "enqueue become an unbounded sweep."
        )
    work_item_id = uuid.UUID(str(raw))

    work_item = db.execute(
        select(WorkItem).where(WorkItem.id == work_item_id)
    ).scalar_one_or_none()
    if work_item is None:
        return {"status": "SKIPPED", "reason": "work_item_missing"}

    row = sweep_module.fingerprint_document(db, work_item=work_item)
    if row is None:
        return {"status": "SKIPPED", "reason": "no_chunks"}

    outcome = sweep_module.sweep_document(db, work_item_id=work_item_id)

    digest = fp.input_digest(
        {
            "job": vocab.JOB_ANOMALY_SCAN_DOCUMENT,
            "work_item_id": str(work_item_id),
            "content_sha256": row.content_sha256,
            "minhash": list(row.minhash),
        }
    )
    sweep_module.meter(
        db,
        organization_id=row.organization_id,
        outcome=outcome,
        input_digest=digest,
    )

    # HARDENING-T1:D34. Nested under one key: the payload has a `created`
    # count, which collides with LogRecord.created and raised KeyError.
    logger.info("radar.scan_document", extra={"radar_outcome": outcome.as_payload()})
    return {"status": "OK", **outcome.as_payload()}


def handle_anomaly_nightly(db: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Price surge and contract drift per workspace, plus a duplicate catch-up.

    Each workspace is committed on its own. A tenant with a malformed
    extraction records a skip and the pass continues; the alternative is one
    bad document stopping anomaly detection for every tenant, which is the
    failure mode least likely to be noticed until somebody asks why nothing
    has been flagged in a month.
    """
    workspace_filter = payload.get("workspace_id")
    stmt = select(Workspace.id, Workspace.organization_id)
    if workspace_filter:
        stmt = stmt.where(Workspace.id == uuid.UUID(str(workspace_filter)))
    else:
        stmt = stmt.limit(NIGHTLY_WORKSPACE_BATCH)

    rows = db.execute(stmt).all()

    totals = {
        "workspaces": 0,
        "surge_created": 0,
        "drift_created": 0,
        "duplicate_created": 0,
        "unchanged": 0,
        "skipped": 0,
    }

    for workspace_id, organization_id in rows:
        totals["workspaces"] += 1
        try:
            settings = sweep_module.settings_for(
                db, organization_id=organization_id
            )
            if not settings.enabled_layers:
                totals["skipped"] += 1
                continue

            surge = sweep_module.sweep_workspace_prices(
                db, workspace_id=workspace_id, organization_id=organization_id
            )
            drift = sweep_module.sweep_workspace_drift(
                db, workspace_id=workspace_id, organization_id=organization_id
            )
            duplicates = _catch_up_duplicates(
                db, workspace_id=workspace_id, settings=settings
            )

            totals["surge_created"] += surge.created
            totals["drift_created"] += drift.created
            totals["duplicate_created"] += duplicates.created
            totals["unchanged"] += (
                surge.unchanged + drift.unchanged + duplicates.unchanged
            )

            for outcome, tag in (
                (surge, "prices"),
                (drift, "drift"),
                (duplicates, "duplicates"),
            ):
                sweep_module.meter(
                    db,
                    organization_id=organization_id,
                    outcome=outcome,
                    input_digest=fp.input_digest(
                        {
                            "job": vocab.JOB_ANOMALY_NIGHTLY,
                            "detector": tag,
                            "workspace_id": str(workspace_id),
                            "day": datetime.now(timezone.utc).date(),
                            "counts": outcome.as_payload(),
                        }
                    ),
                )

            db.commit()
        except Exception:  # noqa: BLE001 - one tenant must not stop the pass
            db.rollback()
            totals["skipped"] += 1
            logger.exception(
                "radar.nightly_workspace_failed",
                extra={"workspace_id": str(workspace_id)},
            )

    logger.info("radar.nightly", extra=totals)
    return {"status": "OK", **totals}


def _catch_up_duplicates(
    db: Any, *, workspace_id: uuid.UUID, settings: Any
) -> sweep_module.SweepOutcome:
    """Compare fingerprints the per-document scan never reached.

    "Never reached" is read as "has no finding naming it as subject". That is
    deliberately cheap and deliberately re-entrant: a document whose scan ran
    and found nothing has no row, so it is re-compared each night — one index
    pass against stored integers, writing nothing, billing nothing.
    """
    from app.models.radar import AnomalyFinding

    outcome = sweep_module.SweepOutcome(workspace_id=workspace_id)

    subquery = select(AnomalyFinding.subject_work_item_id).where(
        AnomalyFinding.workspace_id == workspace_id
    )
    pending = db.execute(
        select(DocumentFingerprint.work_item_id)
        .where(
            DocumentFingerprint.workspace_id == workspace_id,
            DocumentFingerprint.work_item_id.notin_(subquery),
        )
        .order_by(DocumentFingerprint.computed_at.desc())
        .limit(NIGHTLY_DOCUMENT_BATCH)
    ).scalars().all()

    for work_item_id in pending:
        one = sweep_module.sweep_document(
            db, work_item_id=work_item_id, settings=settings
        )
        outcome.compared += one.compared
        outcome.created += one.created
        outcome.updated += one.updated
        outcome.suppressed += one.suppressed
        outcome.unchanged += one.unchanged

    return outcome
