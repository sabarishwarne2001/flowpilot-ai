"""ARCH43-S1:service — split plans in the database.

detect()   read the pages OCR stored, plan, and record a PROPOSED plan (or
           SINGLE when the packet is one document). No PDF engine.
replan()   the reviewer's correction: a new boundary list replaces the
           segments (validated here; the exclusion constraint and the range
           trigger refuse a bad plan independently).
approve()  optionally with corrected boundaries; enqueues packets.apply_split.
reject()   keep the packet as one document.
apply()    the job body (OCR worker profile): pikepdf writes one PDF per
           segment, each goes through the SAME validation and intake as an
           upload (tenant-scoped storage key, uploaded_files row, audit,
           trigger.work_item.created), the parent's per-page OCR is COPIED to
           the child (no page is OCR'd or billed twice), the child moves to
           EXTRACTED and is enriched like any document, and the parent's
           entity mentions are superseded. The original is retained.
"""

from __future__ import annotations

import io
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import PurePath
from typing import Any, Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.packets import PacketSplit, PacketSplitSegment
from app.models.work_item import WorkItem
from app.services import audit_service
from app.services.packets import gate, lineage, planner
from app.services.packets import model as boundary_model
from app.services.packets import vocabulary as v

logger = logging.getLogger("app.services.packets.service")


class PacketError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def now() -> datetime:
    return datetime.now(timezone.utc)


def page_texts(work_item: WorkItem) -> list[str]:
    meta = work_item.extraction_metadata or {}
    pages = meta.get("pages") if isinstance(meta, dict) else None
    if isinstance(pages, list) and pages and isinstance(pages[0], dict):
        ordered = sorted(pages, key=lambda p: int(p.get("page_number") or 0))
        return [str(p.get("text") or "") for p in ordered]
    text = work_item.extracted_text or ""
    return text.split("\f") if "\f" in text else ([text] if text else [])


def _tenant_hints(db: Session, organization_id: uuid.UUID) -> dict[str, list[str]]:
    from app.models.ingestion import DocumentSchemaPreset

    rows = db.execute(select(DocumentSchemaPreset.document_type, DocumentSchemaPreset.classifier_hints).where(
        DocumentSchemaPreset.organization_id == organization_id)).all()
    return {doc_type: [str(h) for h in (hints or []) if isinstance(h, str)] for doc_type, hints in rows if hints}


def _audit(db: Session, split: PacketSplit, operation: str, actor: Optional[uuid.UUID], **extra: Any) -> None:
    audit_service.record(
        db, organization_id=split.organization_id, workspace_id=split.workspace_id, actor_id=actor,
        resource_type=AuditResourceType.WORKSPACE, resource_id=split.workspace_id, action=AuditAction.UPDATED,
        outcome=AuditOutcome.ALLOWED,
        details={"packet_split": {"operation": operation, "split_id": str(split.id),
                                  "work_item_id": str(split.work_item_id), "status": split.status, **extra}},
    )


def _write_segments(db: Session, split: PacketSplit, segments: Sequence[planner.Segment]) -> None:
    db.execute(delete(PacketSplitSegment).where(PacketSplitSegment.split_id == split.id))
    db.flush()
    for s in segments:
        db.add(PacketSplitSegment(id=uuid.uuid4(), split_id=split.id, workspace_id=split.workspace_id, ordinal=s.ordinal,
                                  page_start=s.page_start, page_end=s.page_end, document_type=s.document_type,
                                  confidence=Decimal(str(round(min(1.0, max(0.0, s.confidence)), 4))), title=s.title))
    db.flush()


def live_split(db: Session, work_item_id: uuid.UUID) -> Optional[PacketSplit]:
    return db.execute(select(PacketSplit).where(PacketSplit.work_item_id == work_item_id,
                                                PacketSplit.status.in_(v.LIVE_STATUSES))).scalar_one_or_none()


def segments_of(db: Session, split_id: uuid.UUID) -> list[PacketSplitSegment]:
    return list(db.execute(select(PacketSplitSegment).where(PacketSplitSegment.split_id == split_id)
                           .order_by(PacketSplitSegment.ordinal)).scalars())


def detect(db: Session, *, work_item: WorkItem, actor_user_id: Optional[uuid.UUID] = None,
           force: bool = False) -> PacketSplit:
    organization_id = gate.organization_of(db, work_item)
    existing = live_split(db, work_item.id)
    if existing is not None:
        if existing.status in v.DECIDED_STATUSES:
            return existing  # a reviewer decided; detection never overrides a person
        if not force and existing.model_version == boundary_model.MODEL_VERSION and existing.source == v.SOURCE_MODEL:
            return existing  # idempotent: the same pages give the same plan
        existing.status = v.STATUS_SUPERSEDED
        existing.updated_at = now()
        db.flush()
    texts = page_texts(work_item)
    if not texts:
        raise PacketError("NO_PAGES", "This document has no extracted pages to split yet.")
    try:
        result = planner.plan(texts, extra_hints=_tenant_hints(db, organization_id))
    except planner.PlanError as exc:
        raise PacketError("PLAN_REFUSED", str(exc)) from exc
    split = PacketSplit(
        id=uuid.uuid4(), organization_id=organization_id, workspace_id=work_item.workspace_id, work_item_id=work_item.id,
        status=v.STATUS_PROPOSED if len(result.segments) > 1 else v.STATUS_SINGLE, source=v.SOURCE_MODEL,
        page_count=result.page_count, model_version=boundary_model.MODEL_VERSION,
        threshold=Decimal(str(result.threshold)), certainty=Decimal(str(result.certainty)),
        page_scores=result.page_scores(), created_by_user_id=actor_user_id,
    )
    db.add(split)
    db.flush()
    _write_segments(db, split, result.segments)
    _audit(db, split, "detect", actor_user_id, segments=len(result.segments), certainty=float(result.certainty))
    return split


def _stored_facts(split: PacketSplit) -> tuple[list, list[float]]:
    """Rebuild just enough per-page facts from page_scores to name segments."""
    from types import SimpleNamespace

    scores = sorted(split.page_scores or [], key=lambda s: s["page"])
    facts = [SimpleNamespace(cls=SimpleNamespace(doc_type=s.get("doc_type") or "other"), text="") for s in scores]
    return facts, [float(s.get("p") or 0.0) for s in scores]


def replan(db: Session, *, split: PacketSplit, boundaries: Sequence[int], actor_user_id: uuid.UUID) -> PacketSplit:
    if split.status not in (v.STATUS_PROPOSED, v.STATUS_SINGLE):
        raise PacketError("NOT_EDITABLE", f"A {split.status.lower()} plan cannot be changed.")
    try:
        values = planner.validate_boundaries(boundaries, split.page_count)
    except planner.PlanError as exc:
        raise PacketError("INVALID_BOUNDARIES", str(exc)) from exc
    facts, probs = _stored_facts(split)
    if len(facts) != split.page_count:
        facts = [type("F", (), {"cls": type("C", (), {"doc_type": "other"})(), "text": ""})() for _ in range(split.page_count)]
        probs = [1.0] + [0.0] * (split.page_count - 1)
    old_titles = {s.page_start: s.title for s in segments_of(db, split.id)}
    segments = planner.segments_for(values, split.page_count, facts, probs)
    segments = [planner.Segment(s.ordinal, s.page_start, s.page_end, None if s.document_type == "other" else s.document_type,
                                s.confidence, old_titles.get(s.page_start)) for s in segments]
    _write_segments(db, split, segments)
    split.source = v.SOURCE_REVIEWER
    split.status = v.STATUS_PROPOSED if len(segments) > 1 else v.STATUS_SINGLE
    split.updated_at = now()
    db.flush()
    _audit(db, split, "replan", actor_user_id, boundaries=values)
    return split


def approve(db: Session, *, split: PacketSplit, actor_user_id: uuid.UUID,
            boundaries: Optional[Sequence[int]] = None) -> PacketSplit:
    from app.services import job_service

    if boundaries is not None:
        replan(db, split=split, boundaries=boundaries, actor_user_id=actor_user_id)
    if split.status != v.STATUS_PROPOSED:
        raise PacketError("NOT_APPROVABLE", "Only a proposed plan with at least two documents can be approved.")
    split.status, split.decided_at, split.decided_by_user_id, split.updated_at = v.STATUS_APPROVED, now(), actor_user_id, now()
    db.flush()
    job_service.enqueue(db, job_type=v.JOB_APPLY, organization_id=split.organization_id,
                        payload={"split_id": str(split.id), "workspace_id": str(split.workspace_id)},
                        idempotency_key=f"{v.JOB_APPLY}:{split.id}")
    _audit(db, split, "approve", actor_user_id, segments=len(segments_of(db, split.id)))
    return split


def reject(db: Session, *, split: PacketSplit, actor_user_id: uuid.UUID) -> PacketSplit:
    if split.status not in (v.STATUS_PROPOSED, v.STATUS_SINGLE):
        raise PacketError("NOT_REJECTABLE", f"A {split.status.lower()} plan cannot be rejected.")
    split.status, split.decided_at, split.decided_by_user_id, split.updated_at = v.STATUS_REJECTED, now(), actor_user_id, now()
    db.flush()
    _audit(db, split, "reject", actor_user_id)
    return split


def _child_pdf(source: Any, start: int, end: int) -> bytes:
    import pikepdf

    out = pikepdf.Pdf.new()
    for index in range(start - 1, end):
        out.pages.append(source.pages[index])
    buffer = io.BytesIO()
    out.save(buffer)
    return buffer.getvalue()


def _child_name(parent: WorkItem, segment: PacketSplitSegment) -> str:
    stem = PurePath(parent.original_filename or "packet.pdf").stem[:120]
    kind = f" {segment.document_type}" if segment.document_type else ""
    return f"{stem} - part {segment.ordinal + 1}{kind} (pages {segment.page_start}-{segment.page_end}).pdf"[:255]


def _copy_ocr(parent: WorkItem, child: WorkItem, split: PacketSplit, segment: PacketSplitSegment) -> None:
    meta = parent.extraction_metadata if isinstance(parent.extraction_metadata, dict) else {}
    pages = sorted([p for p in (meta.get("pages") or []) if isinstance(p, dict)], key=lambda p: int(p.get("page_number") or 0))
    sliced = []
    for new_number, page in enumerate(pages[segment.page_start - 1:segment.page_end], start=1):
        sliced.append({**page, "page_number": new_number, "source_page_number": page.get("page_number")})
    child.extracted_text = "\n".join(str(p.get("text") or "") for p in sliced if p.get("text"))
    child.page_count = segment.page_end - segment.page_start + 1
    child.extraction_metadata = {
        "provider": meta.get("provider"), "model": meta.get("model"), "pages": sliced,
        "billable_pages": 0, "mean_confidence": meta.get("mean_confidence"),
        "packet_split": {"split_id": str(split.id), "parent_work_item_id": str(parent.id),
                         "page_start": segment.page_start, "page_end": segment.page_end},
    }


def apply(db: Session, *, split_id: uuid.UUID) -> dict[str, Any]:
    """The packets.apply_split job body. The caller commits."""
    import pikepdf

    from app.core.config import settings
    from app.core.storage import get_storage_driver
    from app.services import document_intake_service, file_validation_service, job_service
    from app.services.pipeline_state import PipelineStage, transition_by_id

    split = db.execute(select(PacketSplit).where(PacketSplit.id == split_id).with_for_update()).scalar_one_or_none()
    if split is None:
        return {"applied": False, "reason": "plan no longer exists"}
    if split.status == v.STATUS_APPLIED:
        return {"applied": True, "children": 0, "reason": "already applied"}
    if split.status not in (v.STATUS_APPROVED, v.STATUS_APPLYING):
        return {"applied": False, "reason": f"plan is {split.status}"}
    parent = db.execute(select(WorkItem).where(WorkItem.id == split.work_item_id,
                                               WorkItem.workspace_id == split.workspace_id)).scalar_one()
    if (parent.file_type or "").split(";")[0].strip().lower() != v.PDF_MIME:
        raise PacketError("NOT_PDF", "Only PDF packets can be split.")
    split.status = v.STATUS_APPLYING
    db.flush()
    blob = get_storage_driver().get(parent.stored_filename)
    created: list[uuid.UUID] = []
    with pikepdf.open(io.BytesIO(blob)) as source:
        if len(source.pages) != split.page_count:
            raise PacketError("PAGE_MISMATCH", f"the PDF has {len(source.pages)} pages; the plan has {split.page_count}")
        for segment in segments_of(db, split.id):
            if segment.child_work_item_id is not None:
                continue  # a retry after a partial apply
            data = _child_pdf(source, segment.page_start, segment.page_end)
            validated = file_validation_service.validate_spooled(
                io.BytesIO(data), len(data), declared_mime=v.PDF_MIME, original_filename=_child_name(parent, segment),
                allowed_mimes=[v.PDF_MIME], max_pages=settings.MAX_DOCUMENT_PAGES,
                scrub_metadata=settings.SCRUB_UPLOAD_METADATA)
            with validated:
                result = document_intake_service.ingest_validated(
                    db, validated, organization_id=split.organization_id, workspace_id=split.workspace_id,
                    uploader_id=split.decided_by_user_id or parent.created_by_user_id, enqueue_extraction=False)
            child = result.work_item
            child.parent_work_item_id = parent.id
            child.parent_page_start, child.parent_page_end = segment.page_start, segment.page_end
            _copy_ocr(parent, child, split, segment)
            db.flush([child])
            for stage in (PipelineStage.EXTRACTING, PipelineStage.EXTRACTED):
                transition_by_id(db, work_item_id=child.id, to_stage=stage, organization_id=split.organization_id)
            job_service.enqueue(db, job_type="document.enrich", organization_id=split.organization_id,
                                payload={"work_item_id": str(child.id), "organization_id": str(split.organization_id),
                                         "workspace_id": str(split.workspace_id)},
                                idempotency_key=f"document.enrich:{child.id}")
            segment.child_work_item_id = child.id
            db.flush([segment])
            created.append(child.id)
    superseded = lineage.supersede_parent_mentions(db, split=split)
    split.status, split.applied_at, split.updated_at = v.STATUS_APPLIED, now(), now()
    db.flush()
    _audit(db, split, "apply", split.decided_by_user_id, children=[str(c) for c in created], superseded_mentions=superseded)
    # ARCH43-S1:emitter-packet-split
    from app.services import outbox_service
    from app.services.cases.vocabulary import EVENT_PACKET_SPLIT

    outbox_service.emit_trigger(
        db, organization_id=split.organization_id, workspace_id=split.workspace_id, event_type=EVENT_PACKET_SPLIT,
        resource_id=parent.id, idempotency_key=f"{EVENT_PACKET_SPLIT}:{split.id}",
        payload={"work_item_id": str(parent.id), "split_id": str(split.id), "original_filename": parent.original_filename,
                 "page_count": split.page_count, "child_count": len(created), "children": [str(c) for c in created]})
    logger.info("packets.applied", extra={"split_id": str(split.id), "children": len(created)})
    return {"applied": True, "children": len(created), "superseded_mentions": superseded}


def render_thumbnail(work_item: WorkItem, page: int, scale: float = 0.35) -> bytes:
    """ARCH43-S1:thumbnail. Render one page of a PDF packet to PNG bytes."""
    import pypdfium2 as pdfium

    from app.core.storage import get_storage_driver

    if (work_item.file_type or "").split(";")[0].strip().lower() != v.PDF_MIME:
        raise PacketError("NOT_PDF", "Only PDF packets have page thumbnails.")
    pdf = pdfium.PdfDocument(get_storage_driver().get(work_item.stored_filename))
    try:
        if not 1 <= page <= len(pdf):
            raise PacketError("NO_PAGE", "page out of range")
        image = pdf[page - 1].render(scale=scale).to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    finally:
        pdf.close()


def mark_failed(db: Session, *, split_id: uuid.UUID, reason: str) -> None:
    split = db.get(PacketSplit, split_id)
    if split is not None and split.status in (v.STATUS_APPROVED, v.STATUS_APPLYING):
        split.status, split.failure_reason, split.updated_at = v.STATUS_FAILED, reason[:2000], now()
        db.flush()


__all__ = ["PacketError", "apply", "approve", "detect", "live_split", "mark_failed", "page_texts", "reject",
           "replan", "segments_of"]
