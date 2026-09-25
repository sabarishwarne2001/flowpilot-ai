"""HARDENING-T1:D18/D19/D20 — what happens after a document is enriched.

Enrichment finished, and before this module nothing downstream was told:

* D18  no `document_roles` row was ever written, so `procurement.score`
       (three-way matching) had nothing to sweep;
* D19  `anomaly.scan_document` was never enqueued, so no fingerprint was
       ever computed and every radar detector ran over an empty table;
* D20  `document.verify` was never enqueued, so multi-agent extraction
       verification never ran even with `verification_enabled` on.

`dispatch` runs once per successful enrichment, in the worker, after the
document is COMPLETED. It writes the role row and enqueues the jobs in ONE
transaction, so either every engine hears about the document or none does
and the error is logged with the document id.

WHY ENQUEUE AND NOT RUN INLINE
==============================

Scoring and radar comparison are workspace-wide reads whose cost grows with
the tenant; verification makes several LLM calls. Running them inside the
enrichment job would couple the latency and failure of four engines to the
one step the user is watching. Each already has its own handler, retry
policy and idempotence; this module only makes sure they are called.

IDEMPOTENCE
===========

Every key carries an extraction marker (the enrichment job id, else the
stage timestamp), so a retry of the same enrichment enqueues nothing new and a
reprocess of the document enqueues a fresh round.

ENTITLEMENT
===========

Roles are written for every tenant: they are cheap, deterministic, and the
radar's contract-drift detector reads them. Scoring is enqueued only when the
organization's plan carries the reconciliation capability — a case scored for
a tenant who has not bought matching would still meter a usage event.
`procurement.score` re-checks per invoice, because its five-minute sweep does
not pass through here.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select

logger = logging.getLogger("app.services.post_enrichment")

ROLE_INVOICE = "INVOICE"
COUNTERPART_ROLES = frozenset({"PURCHASE_ORDER", "GOODS_RECEIPT"})


def _marker(work_item: Any, job_id: Optional[uuid.UUID]) -> str:
    if job_id is not None:
        return str(job_id)
    stamp = getattr(work_item, "stage_updated_at", None)
    return str(int(stamp.timestamp())) if stamp is not None else "0"


def _reconciliation_enabled(db: Any, organization_id: uuid.UUID) -> bool:
    from app.api import capability_gate
    from app.core.entitlements import RECONCILIATION_CAPABILITY

    try:
        return bool(
            capability_gate.has_capability(
                db,
                organization_id=organization_id,
                capability_key=RECONCILIATION_CAPABILITY,
            )
        )
    except Exception:  # noqa: BLE001 - an unreadable plan is "not entitled"
        logger.exception(
            "post_enrichment.capability_check_failed",
            extra={"organization_id": str(organization_id)},
        )
        return False


def _verification_enabled(db: Any, workspace_id: uuid.UUID) -> bool:
    from app.crud.document_settings import get_document_settings
    from app.services import document_verification_service as dv

    return dv.is_enabled(get_document_settings(db, workspace_id=workspace_id))


def dispatch_in_session(
    db: Any,
    *,
    work_item_id: uuid.UUID,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    job_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """Write the role row and enqueue downstream engines. Caller commits."""
    from app.models.work_item import WorkItem
    from app.services import job_service
    from app.services.procurement_matching.role_service import classify_and_store

    work_item = db.execute(
        select(WorkItem).where(
            WorkItem.id == work_item_id, WorkItem.workspace_id == workspace_id
        )
    ).scalar_one_or_none()
    if work_item is None:
        return {"dispatched": False, "reason": "work item not found in workspace"}

    marker = _marker(work_item, job_id)
    role = classify_and_store(db, work_item=work_item, organization_id=organization_id)
    enqueued: list[str] = []

    # D19 — every completed document is fingerprinted and compared. The
    # handler honours the tenant's radar capability and layer settings.
    job_service.enqueue(
        db,
        job_type="anomaly.scan_document",
        organization_id=organization_id,
        payload={"work_item_id": str(work_item_id)},
        idempotency_key=f"anomaly.scan_document:{work_item_id}:{marker}",
    )
    enqueued.append("anomaly.scan_document")

    # D18 — three-way matching.
    if role.role == ROLE_INVOICE or role.role in COUNTERPART_ROLES:
        if _reconciliation_enabled(db, organization_id):
            if role.role == ROLE_INVOICE:
                payload = {"invoice_work_item_id": str(work_item_id)}
                key = f"procurement.score:arrival:{work_item_id}:{marker}"
            else:
                # A PO or goods receipt can complete an invoice that is
                # already waiting; sweep this workspace's open invoices.
                payload = {"workspace_id": str(workspace_id)}
                key = f"procurement.score:counterpart:{work_item_id}:{marker}"
            job_service.enqueue(
                db,
                job_type="procurement.score",
                organization_id=organization_id,
                payload=payload,
                idempotency_key=key,
            )
            enqueued.append("procurement.score")

    # D20 — multi-agent extraction verification, when the workspace has it on.
    if _verification_enabled(db, workspace_id):
        job_service.enqueue(
            db,
            job_type="document.verify",
            organization_id=organization_id,
            payload={"work_item_id": str(work_item_id)},
            idempotency_key=f"document.verify:{work_item_id}:{marker}",
        )
        enqueued.append("document.verify")

    # ARCH42-S1:entity-dispatch. The incremental half of entity resolution,
    # on the heels of `work_item.enriched`; the nightly sweep is the other.
    from app.services.entities import gate as entity_gate

    if entity_gate.capability_held(db, organization_id):
        job_service.enqueue(
            db,
            job_type="entities.resolve_document",
            organization_id=organization_id,
            payload={"work_item_id": str(work_item_id)},
            idempotency_key=f"entities.resolve_document:{work_item_id}:{marker}",
        )
        enqueued.append("entities.resolve_document")

    # ARCH43-S1:packet-dispatch. A multi-page PDF that is not itself a child
    # of a split is scored for document boundaries (LIGHT profile), for
    # organizations whose plan carries capability.case_intelligence.
    from app.services.packets import gate as packet_gate
    from app.services.packets import vocabulary as packet_vocab

    if (
        work_item.parent_work_item_id is None
        and (work_item.page_count or 0) >= packet_vocab.MIN_PAGES_AUTO
        and (work_item.file_type or "").split(";")[0].strip().lower() == packet_vocab.PDF_MIME
        and packet_gate.capability_held(db, organization_id)
    ):
        job_service.enqueue(
            db,
            job_type=packet_vocab.JOB_DETECT,
            organization_id=organization_id,
            payload={"work_item_id": str(work_item_id)},
            idempotency_key=f"{packet_vocab.JOB_DETECT}:{work_item_id}:{marker}",
        )
        enqueued.append(packet_vocab.JOB_DETECT)

    # ARCH43-S1:case-dispatch. Every enriched document is offered to the
    # workspace's published case templates (entity- or batch-anchored).
    if packet_gate.capability_held(db, organization_id):
        job_service.enqueue(
            db,
            job_type="cases.assemble_document",
            organization_id=organization_id,
            payload={"work_item_id": str(work_item_id)},
            idempotency_key=f"cases.assemble_document:{work_item_id}:{marker}",
        )
        enqueued.append("cases.assemble_document")
    # ARCH44-S1:table-dispatch. Every extractable document (PDF or image) that is
    # not itself a split packet is scanned for tables, on the OCR profile, for
    # organizations whose plan carries capability.table_intelligence.
    from app.services.tables import gate as table_gate
    from app.services.tables import vocabulary as table_vocab
    if (
        (work_item.file_type or "").split(";")[0].strip().lower() in table_vocab.EXTRACTABLE_MIME
        and table_gate.capability_held(db, organization_id)
    ):
        job_service.enqueue(
            db,
            job_type=table_vocab.JOB_EXTRACT,
            organization_id=organization_id,
            payload={"work_item_id": str(work_item_id)},
            idempotency_key=f"{table_vocab.JOB_EXTRACT}:{work_item_id}:{marker}",
        )
        enqueued.append(table_vocab.JOB_EXTRACT)

    # ARCH45-S1:corroboration-invalidate. A reprocessed document changes the
    # answer of every comparison it is in: those runs become STALE (their
    # fingerprint no longer matches), and the next request recomputes. Never
    # allowed to fail the dispatch.
    stale = 0
    try:
        from app.services.corroboration import service as corroboration_service

        with db.begin_nested():
            stale = corroboration_service.invalidate_for_work_items(db, [work_item_id])
    except Exception:  # noqa: BLE001
        logger.exception("post_enrichment.corroboration_invalidate_failed", extra={"work_item_id": str(work_item_id)})

    # ARCH46-S1:obligation-dispatch. Every enriched document is read for
    # obligations (LIGHT profile), for organizations whose plan carries
    # capability.obligations, a little after ARCH-42 resolves its parties.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    from app.services.obligations import gate as obligation_gate
    from app.services.obligations import vocabulary as obligation_vocab

    if obligation_gate.capability_held(db, organization_id):
        job_service.enqueue(
            db,
            job_type=obligation_vocab.JOB_EXTRACT,
            organization_id=organization_id,
            payload={"work_item_id": str(work_item_id)},
            idempotency_key=f"{obligation_vocab.JOB_EXTRACT}:{work_item_id}:{marker}",
            available_at=_dt.now(_tz.utc) + _td(seconds=obligation_vocab.EXTRACT_DELAY_SECONDS),
        )
        enqueued.append(obligation_vocab.JOB_EXTRACT)

    return {"dispatched": True, "role": role.as_details(), "enqueued": enqueued, "stale_comparisons": stale}


def dispatch(
    *,
    work_item_id: uuid.UUID,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    job_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """Own-session wrapper for the enrichment handler. Never raises: the
    document is already COMPLETED, and an engine that could not be told must
    not turn a finished document into a failed one."""
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            with db.begin():
                result = dispatch_in_session(
                    db,
                    work_item_id=work_item_id,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    job_id=job_id,
                )
        logger.info(
            "post_enrichment.dispatched",
            extra={"work_item_id": str(work_item_id), "enqueued": result.get("enqueued")},
        )
        return result
    except Exception:  # noqa: BLE001
        logger.exception(
            "post_enrichment.dispatch_failed",
            extra={"work_item_id": str(work_item_id)},
        )
        return {"dispatched": False, "reason": "error; see logs"}


__all__ = ["dispatch", "dispatch_in_session"]
