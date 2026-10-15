"""The Promise ledger's database side: read the facts, write the diff.

`load_inputs` reads exactly what `ledger.build` needs, scoped by `workspace_id` in every query.
`rebuild_workspace` recomputes one workspace's promises from scratch and writes only what changed
(insert new source keys, update changed hashes, delete source keys no longer produced). The same
function serves the incremental update after a document changes and the nightly full rebuild, so
the two cannot disagree; a test proves it on the database.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.document_role import DocumentRole
from app.models.helm import HelmBook, HelmPromise, HelmSettings, HelmSettlement
from app.models.obligations import Obligation
from app.models.procurement import ProcurementCase, ProcurementCaseLine, ProcurementTolerancePolicy
from app.models.verification import DocumentVerification
from app.models.work_item import WorkItem
from app.models.work_item_field_correction import WorkItemFieldCorrection
from app.models.workspace import Workspace
from app.services.helm import identity as ident
from app.services.helm import ledger
from app.services.helm import vocabulary as v

logger = logging.getLogger("app.services.helm.ledger_store")

TEXT_HEAD_CHARS = 4000


@dataclass(frozen=True)
class RebuildResult:
    workspace_id: uuid.UUID
    inserted: int
    updated: int
    deleted: int
    unchanged: int
    ms: int

    @property
    def changed(self) -> bool:
        return bool(self.inserted or self.updated or self.deleted)

    def as_dict(self) -> dict[str, Any]:
        return {"workspace_id": str(self.workspace_id), "inserted": self.inserted, "updated": self.updated,
                "deleted": self.deleted, "unchanged": self.unchanged, "ms": self.ms}


def identities_for_workspace(db: Session, workspace_id: uuid.UUID) -> ident.Identities:
    """Our identities for one workspace's ledger: its own book's, else the all-workspaces book's."""
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        return ident.Identities(frozenset(), frozenset())
    books = db.execute(
        select(HelmBook.id, HelmBook.kind).where(
            HelmBook.organization_id == workspace.organization_id,
            HelmBook.status == v.BOOK_ACTIVE,
            ((HelmBook.workspace_id == workspace_id) | (HelmBook.kind == v.BOOK_ORGANIZATION)),
        )
    ).all()
    ordered = sorted(books, key=lambda b: 0 if b.kind != v.BOOK_ORGANIZATION else 1)
    for book in ordered:
        settings = db.get(HelmSettings, book.id)
        if settings is not None and settings.identities:
            return ident.Identities.of(settings.identities)
    return ident.Identities(frozenset(), frozenset())


def _confidence(status: Optional[str], corrected: bool) -> str:
    if corrected or status == "REVIEWED":
        return v.CONFIDENCE_VERIFIED
    if status in ("AGREED", "AUTO_APPROVED"):
        return v.CONFIDENCE_CALIBRATED
    return v.CONFIDENCE_EXTRACTED


def load_inputs(db: Session, *, workspace_id: uuid.UUID, as_of: date,
                identities: Optional[ident.Identities] = None) -> ledger.LedgerInputs:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise ValueError("workspace not found")
    identities = identities if identities is not None else identities_for_workspace(db, workspace_id)

    verification = {
        row.work_item_id: getattr(row.status, "value", row.status)
        for row in db.execute(
            select(DocumentVerification.work_item_id, DocumentVerification.status)
            .where(DocumentVerification.workspace_id == workspace_id)
        )
    }
    corrected = set(
        db.execute(
            select(WorkItemFieldCorrection.work_item_id)
            .join(WorkItem, WorkItem.id == WorkItemFieldCorrection.work_item_id)
            .where(WorkItem.workspace_id == workspace_id)
            .distinct()
        ).scalars()
    )

    documents: list[ledger.DocumentFacts] = []
    rows = db.execute(
        select(
            WorkItem.id, WorkItem.original_filename, WorkItem.created_at, WorkItem.extracted_entities,
            func.substring(WorkItem.extracted_text, 1, TEXT_HEAD_CHARS).label("text_head"),
            DocumentRole.role, DocumentRole.vendor_key, DocumentRole.document_number, DocumentRole.document_date,
            DocumentRole.currency, DocumentRole.total_micros, DocumentRole.role_source,
        )
        .join(DocumentRole, DocumentRole.work_item_id == WorkItem.id)
        .where(WorkItem.workspace_id == workspace_id, DocumentRole.workspace_id == workspace_id)
        .where(WorkItem.pipeline_stage != "FAILED")
    ).all()
    from app.services.procurement_matching.line_extraction import po_reference_of

    for row in rows:
        entities = row.extracted_entities if isinstance(row.extracted_entities, dict) else {}
        classification = entities.get("document_classification") or entities.get("document_type")
        documents.append(ledger.DocumentFacts(
            work_item_id=row.id, workspace_id=workspace_id, organization_id=workspace.organization_id,
            filename=row.original_filename, role=row.role,
            received_on=(row.created_at or datetime.now(timezone.utc)).date(), entities=entities,
            vendor_key=row.vendor_key, document_number=row.document_number, document_date=row.document_date,
            currency=row.currency, total_micros=row.total_micros, po_reference=po_reference_of(entities),
            confidence=_confidence(verification.get(row.id), row.id in corrected),
            text_head=row.text_head or "", classification=str(classification) if classification else None,
        ))

    policy = db.execute(
        select(ProcurementTolerancePolicy.price_tolerance_bps)
        .where(ProcurementTolerancePolicy.workspace_id == workspace_id,
               ProcurementTolerancePolicy.status == "PUBLISHED")
        .order_by(ProcurementTolerancePolicy.version.desc()).limit(1)
    ).scalar_one_or_none()
    match_lines = [
        ledger.MatchLineFacts(
            case_id=line.case_id, workspace_id=workspace_id, po_work_item_id=case.po_work_item_id,
            invoice_work_item_id=case.invoice_work_item_id, receipt_work_item_id=case.receipt_work_item_id,
            line_number=line.line_number, description=line.description, po_line_index=line.po_line_index,
            po_unit_price_micros=line.po_unit_price_micros, invoice_unit_price_micros=line.invoice_unit_price_micros,
            po_quantity=line.po_quantity, receipt_quantity=line.receipt_quantity,
            invoice_quantity=line.invoice_quantity, outcome=line.outcome, tolerance_bps=int(policy or 0),
        )
        for line, case in db.execute(
            select(ProcurementCaseLine, ProcurementCase)
            .join(ProcurementCase, ProcurementCase.id == ProcurementCaseLine.case_id)
            .where(ProcurementCase.workspace_id == workspace_id, ProcurementCaseLine.workspace_id == workspace_id,
                   ProcurementCase.status != "SUPERSEDED")
        ).all()
    ]

    obligations = []
    for ob in db.execute(
        select(Obligation).where(Obligation.workspace_id == workspace_id, Obligation.superseded_at.is_(None),
                                 Obligation.review != "REJECTED")
    ).scalars():
        detail = ob.detail if isinstance(ob.detail, dict) else {}
        obligations.append(ledger.ObligationFacts(
            obligation_id=ob.id, workspace_id=workspace_id, work_item_id=ob.work_item_id, kind=ob.kind,
            title=ob.title, state=ob.state, due_date=ob.due_date, done_on=ob.done_at.date() if ob.done_at else None,
            counterparty_name=ob.counterparty_name,
            amount_micros=int(ob.amount * 1_000_000) if ob.amount is not None else None, currency=ob.currency,
            quote=ob.quote,
            confidence=(v.CONFIDENCE_VERIFIED if ob.review == "CONFIRMED" else
                        v.CONFIDENCE_CALIBRATED if ob.review == "AUTO" else v.CONFIDENCE_EXTRACTED),
            decision=(detail.get("helm_decision") or {}).get("decision") if isinstance(detail.get("helm_decision"),
                                                                                         dict) else None,
        ))

    settlements = {
        s.work_item_id: ledger.SettlementFacts(
            work_item_id=s.work_item_id, state=s.state, source=s.source, run_date=s.run_date, paid_on=s.paid_on,
            paid_amount_micros=s.paid_amount_micros, paid_reference=s.paid_reference, hold_reason=s.hold_reason,
            version=s.version)
        for s in db.execute(select(HelmSettlement).where(HelmSettlement.workspace_id == workspace_id)).scalars()
    }
    return ledger.LedgerInputs(
        as_of=as_of, documents=documents, match_lines=match_lines, obligations=obligations, settlements=settlements,
        identities=identities, date_format=workspace.date_format, workspace_currency=workspace.currency,
    )


def rebuild_workspace(db: Session, *, workspace_id: uuid.UUID, as_of: Optional[date] = None) -> RebuildResult:
    """Recompute one workspace's promises and write the difference. Flushes; the caller commits.

    Serialised per workspace (a transaction-scoped advisory lock), so two ledger jobs for the same
    workspace never interleave their diffs."""
    started = time.monotonic()
    as_of = as_of or datetime.now(timezone.utc).date()
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"helm-ledger:{workspace_id}", 0))))
    inputs = load_inputs(db, workspace_id=workspace_id, as_of=as_of)
    rows = ledger.build(inputs)
    wanted = {row.source_key: row for row in rows}
    existing = {
        key: digest for key, digest in db.execute(
            select(HelmPromise.source_key, HelmPromise.content_hash).where(HelmPromise.workspace_id == workspace_id)
        ).all()
    }
    inserted = updated = unchanged = 0
    for key, row in wanted.items():
        values = row.as_values()
        if key not in existing:
            inserted += 1
        elif existing[key] != values["content_hash"]:
            updated += 1
        else:
            unchanged += 1
            continue
        statement = pg_insert(HelmPromise).values(**values)
        update_values = {k: statement.excluded[k] for k in values if k not in ("source_key",)}
        update_values["updated_at"] = func.now()
        db.execute(statement.on_conflict_do_update(constraint="uq_helm_promises_source_key", set_=update_values))
    stale = [key for key in existing if key not in wanted]
    if stale:
        db.execute(delete(HelmPromise).where(HelmPromise.workspace_id == workspace_id,
                                             HelmPromise.source_key.in_(stale)))
    db.flush()
    result = RebuildResult(workspace_id=workspace_id, inserted=inserted, updated=updated, deleted=len(stale),
                           unchanged=unchanged, ms=int((time.monotonic() - started) * 1000))
    logger.info("helm.ledger_rebuilt", extra=result.as_dict())
    return result


def workspace_ids_with_books(db: Session, organization_id: Optional[uuid.UUID] = None) -> list[uuid.UUID]:
    """Every workspace some active book reads (a WORKSPACE or SAMPLE book, or an ORGANIZATION book's)."""
    query = select(HelmBook.organization_id, HelmBook.workspace_id, HelmBook.kind).where(
        HelmBook.status == v.BOOK_ACTIVE)
    if organization_id is not None:
        query = query.where(HelmBook.organization_id == organization_id)
    out: set[uuid.UUID] = set()
    for org_id, workspace_id, kind in db.execute(query).all():
        if workspace_id is not None:
            out.add(workspace_id)
        else:
            out.update(db.execute(select(Workspace.id).where(Workspace.organization_id == org_id,
                                                             Workspace.is_sample.is_(False))).scalars())
    return sorted(out, key=str)


def book_workspace_ids(db: Session, book: HelmBook) -> list[uuid.UUID]:
    if book.workspace_id is not None:
        return [book.workspace_id]
    from app.models.workspace import WorkspaceStatus

    return sorted(db.execute(
        select(Workspace.id).where(Workspace.organization_id == book.organization_id,
                                   Workspace.is_sample.is_(False), Workspace.status == WorkspaceStatus.ACTIVE)
    ).scalars(), key=str)


def enqueue_rebuild(db: Session, *, workspace_id: uuid.UUID, organization_id: uuid.UUID, reason: str) -> bool:
    """Ask for this workspace's ledger to be rebuilt soon (debounced). Only workspaces some Helm book
    reads are rebuilt; returns False (nothing enqueued) for the rest. Never raises into the caller's
    document path."""
    try:
        if workspace_id not in set(workspace_ids_with_books(db, organization_id)):
            return False
        from datetime import timedelta

        from app.services import job_service

        now = datetime.now(timezone.utc)
        bucket = int(now.timestamp()) // 10
        job_service.enqueue(
            db,
            job_type=v.JOB_LEDGER_BUILD,
            organization_id=organization_id,
            payload={"workspace_id": str(workspace_id), "organization_id": str(organization_id), "reason": reason},
            idempotency_key=f"{v.JOB_LEDGER_BUILD}:{workspace_id}:{bucket}",
            available_at=datetime.fromtimestamp((bucket + 1) * 10, tz=timezone.utc) + timedelta(seconds=1),
        )
        return True
    except Exception:  # noqa: BLE001 - the ledger catches up in the nightly rebuild
        logger.exception("helm.ledger_enqueue_failed", extra={"workspace_id": str(workspace_id)})
        return False


__all__ = ["RebuildResult", "book_workspace_ids", "enqueue_rebuild", "identities_for_workspace", "load_inputs",
           "rebuild_workspace", "workspace_ids_with_books"]
