"""ARCH-31 Step 3 — the `procurement.score` job.

WHY THIS IS A SWEEP AND NOT ONLY AN ARRIVAL TRIGGER
===================================================

The obvious design enqueues a score when an invoice finishes extraction. That
design misses the most common real sequence outright:

    Monday    invoice arrives, no goods receipt yet  -> NOT_RECEIVED
    Thursday  goods receipt arrives                  -> nothing re-runs

Nothing in the arrival path of a GOODS RECEIPT knows which invoices it might
now complete, so the Monday case sits flagged forever and a reviewer chases an
exception that resolved itself three days ago. The sweep is what makes a
late-arriving counterpart matter.

Both paths exist. `payload` with `invoice_work_item_id` scores one set (the
arrival and `rematch` paths); an empty payload sweeps.

WHY IT IS ON THE LIGHT PROFILE
==============================

The scoring work is integer arithmetic, a Hungarian assignment over a matrix
that is single-digit by single-digit on real documents, and a lexical cosine
that imports nothing. `similarity.LexicalBackend` is the default precisely so
this holds; see `similarity.py` for why SentenceTransformers cannot live here.

IDEMPOTENCE
===========

`case_service.score_case` writes nothing when the digest is unchanged, so the
sweep is free on a settled workspace. That is the property that makes a
five-minute interval affordable rather than a background write amplifier.

ONE WORKSPACE'S FAILURE DOES NOT STOP THE SWEEP
===============================================

Each candidate set is committed on its own. A workspace with a malformed
extraction records a skip and the sweep continues — the alternative is one
bad document blocking matching for every tenant, which is the failure mode
least likely to be noticed until somebody asks why nothing has matched.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.document_role import DocumentRole
from app.models.procurement import ProcurementCase
from app.services.procurement_matching import candidates as candidates_module
from app.services.procurement_matching import case_service, line_extraction
from app.services.procurement_matching import policy as policy_module
from app.services.procurement_matching.role_classifier import ROLE_INVOICE
from app.services.procurement_matching.vocabulary import CASE_STATUS_SUPERSEDED

logger = logging.getLogger("app.workers.handlers.procurement")

#: How many invoices one sweep tick will look at. A ceiling rather than a
#: full scan: the sweep runs every few minutes, so an unbounded pass on a
#: tenant with a large backlog would hold a worker for the whole interval and
#: starve every other job type on the LIGHT queue.
SWEEP_BATCH = 200

__all__ = ["handle_procurement_score", "SWEEP_BATCH"]


def _score_one(
    db: Any,
    *,
    role_row: DocumentRole,
    actor_id: Optional[uuid.UUID] = None,
    forced_po_work_item_id: Optional[uuid.UUID] = None,
    forced_receipt_work_item_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """Discover counterparts for one invoice and score the set."""
    from app.models.work_item import WorkItem

    policy = policy_module.resolve_policy(db, workspace_id=role_row.workspace_id)

    po_reference: Optional[str] = None
    work_item = db.execute(
        select(WorkItem).where(WorkItem.id == role_row.work_item_id)
    ).scalar_one_or_none()
    if work_item is not None:
        # The PO reference is read through the SAME extraction the matcher
        # will use, not re-derived here, so the number searched on is the
        # number the digest records.
        header = line_extraction.extract_lines(
            work_item.extracted_entities
        ).header
        po_reference = header.po_reference

    if forced_po_work_item_id is not None or forced_receipt_work_item_id is not None:
        # rematch: a human named the counterpart. Candidate discovery is
        # skipped entirely rather than run and overridden, so that an
        # operator's choice cannot lose to an inference.
        po_id = forced_po_work_item_id
        receipt_id = forced_receipt_work_item_id
        strategy = "USER"
        findings: tuple[dict[str, Any], ...] = (
            {
                "code": "CANDIDATE_SOURCE_USER",
                "detail": "a person chose these documents; discovery was not run",
            },
        )
    else:
        found = candidates_module.find_candidates(
            db,
            workspace_id=role_row.workspace_id,
            invoice_role_row=role_row,
            po_reference=po_reference,
            window_days=policy.candidate_window_days,
        )
        if not found.is_caseable:
            return {
                "work_item_id": str(role_row.work_item_id),
                "skipped": "no_counterparts",
                "findings": [f["code"] for f in found.findings],
            }
        po_id = found.purchase_order.work_item_id if found.purchase_order else None
        receipt_id = found.goods_receipt.work_item_id if found.goods_receipt else None
        strategy = found.strategy
        findings = found.findings

    outcome = case_service.score_case(
        db,
        organization_id=role_row.organization_id,
        workspace_id=role_row.workspace_id,
        invoice_work_item_id=role_row.work_item_id,
        po_work_item_id=po_id,
        receipt_work_item_id=receipt_id,
        vendor_key=role_row.vendor_key,
        actor_id=actor_id,
    )
    result = outcome.as_dict()
    result["strategy"] = strategy
    result["discovery_findings"] = [f["code"] for f in findings]
    return result


def _sweep_targets(db: Any, *, workspace_id: Optional[uuid.UUID]) -> list[DocumentRole]:
    """Invoices worth looking at: every invoice without a live case, plus
    every invoice whose live case is still unresolved.

    An APPROVED or DISPUTED case is NOT re-swept. A person has decided it,
    and the only thing that should disturb that decision is an explicit
    rematch — not a goods receipt arriving late and silently superseding a
    signature.
    """
    live_invoices = select(ProcurementCase.invoice_work_item_id).where(
        ProcurementCase.status.notin_(
            [CASE_STATUS_SUPERSEDED, "APPROVED", "DISPUTED"]
        ),
        ProcurementCase.invoice_work_item_id.is_not(None),
    )
    decided_invoices = select(ProcurementCase.invoice_work_item_id).where(
        ProcurementCase.status.in_(["APPROVED", "DISPUTED"]),
        ProcurementCase.invoice_work_item_id.is_not(None),
    )

    statement = select(DocumentRole).where(
        DocumentRole.role == ROLE_INVOICE,
        DocumentRole.work_item_id.notin_(decided_invoices),
    )
    if workspace_id is not None:
        statement = statement.where(DocumentRole.workspace_id == workspace_id)

    # Deterministic order so a repeated tick walks the same backlog in the
    # same sequence rather than re-shuffling what it gets to.
    statement = statement.order_by(
        DocumentRole.created_at.desc(), DocumentRole.work_item_id.asc()
    ).limit(SWEEP_BATCH)

    _ = live_invoices  # documented above; the notin_ on decided is the filter
    return list(db.execute(statement).scalars().all())


def _reconciliation_enabled(db: Any, organization_id: uuid.UUID) -> bool:
    from app.services.post_enrichment import _reconciliation_enabled as check

    return check(db, organization_id)


def handle_procurement_score(payload: dict[str, Any]) -> dict[str, Any]:
    """Score one document set, or sweep a workspace, or sweep everything."""
    payload = payload or {}
    invoice_id = payload.get("invoice_work_item_id")
    workspace_id = payload.get("workspace_id")

    scored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    with SessionLocal() as db:
        if invoice_id:
            role_row = db.execute(
                select(DocumentRole).where(
                    DocumentRole.work_item_id == uuid.UUID(str(invoice_id))
                )
            ).scalar_one_or_none()
            if role_row is None:
                return {
                    "scored": 0,
                    "skipped": [
                        {
                            "work_item_id": str(invoice_id),
                            "reason": "no document_roles row; classify it first",
                        }
                    ],
                }
            targets = [role_row]
        else:
            targets = _sweep_targets(
                db,
                workspace_id=uuid.UUID(str(workspace_id)) if workspace_id else None,
            )

        forced_po = payload.get("po_work_item_id")
        forced_receipt = payload.get("receipt_work_item_id")
        actor = payload.get("actor_id")

        # HARDENING-T1:D18. With roles now written for every tenant, the
        # five-minute sweep would otherwise score (and meter) invoices for
        # organizations whose plan lacks reconciliation.
        entitled: dict[uuid.UUID, bool] = {}

        for role_row in targets:
            org_id = role_row.organization_id
            if org_id not in entitled:
                entitled[org_id] = _reconciliation_enabled(db, org_id)
            if not entitled[org_id]:
                skipped.append(
                    {
                        "work_item_id": str(role_row.work_item_id),
                        "skipped": "capability_absent",
                    }
                )
                continue
            try:
                # HARDENING-T1:D30. Was `with db.begin():`. Reading the role
                # rows above autobegins the session's transaction, so
                # SQLAlchemy 2.0 refused every begin() with "A transaction is
                # already begun" and every invoice was recorded as skipped:
                # the handler had never scored anything. A savepoint per
                # invoice keeps the one-bad-document-does-not-stop-the-sweep
                # property; the commit makes each case durable on its own.
                with db.begin_nested():
                    result = _score_one(
                        db,
                        role_row=role_row,
                        actor_id=uuid.UUID(str(actor)) if actor else None,
                        forced_po_work_item_id=(
                            uuid.UUID(str(forced_po)) if forced_po else None
                        ),
                        forced_receipt_work_item_id=(
                            uuid.UUID(str(forced_receipt)) if forced_receipt else None
                        ),
                    )
                db.commit()
                if result.get("skipped"):
                    skipped.append(result)
                else:
                    scored.append(result)
            except Exception as exc:  # noqa: BLE001
                # One workspace's malformed extraction must not stop the
                # sweep for every other tenant.
                db.rollback()
                logger.warning(
                    "procurement.score_failed",
                    extra={
                        "work_item_id": str(role_row.work_item_id),
                        "workspace_id": str(role_row.workspace_id),
                        "error": str(exc),
                    },
                )
                skipped.append(
                    {
                        "work_item_id": str(role_row.work_item_id),
                        "skipped": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

    return {
        "scored": len(scored),
        "skipped": len(skipped),
        "results": scored[:50],
        "skips": skipped[:50],
    }