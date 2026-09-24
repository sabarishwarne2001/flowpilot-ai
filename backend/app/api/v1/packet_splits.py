"""ARCH43-S1:api — the packet dicer. Every route is gated on
capability.case_intelligence (402 CAPABILITY_REQUIRED without it)."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.packets import PacketSplit, PacketSplitSegment
from app.models.work_item import WorkItem
from app.models.workspace import WorkspaceRole
from app.schemas.packets import (
    ApproveRequest,
    BoundariesRequest,
    PacketSplitDetail,
    PacketSplitList,
    PacketSplitRow,
    PageScore,
    SegmentRow,
    WorkItemLineage,
)
from app.services.packets import lineage, service
from app.services.packets import vocabulary as v

router = APIRouter(tags=["Packet Dicer"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
CAPABILITY = entitlements.CASE_INTELLIGENCE_CAPABILITY


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(db, context=context, capability_key=CAPABILITY, operation=operation)


def _assert_workspace(context: TenantContext, workspace_id: uuid.UUID) -> None:
    if context.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")


def _split_or_404(db: Session, workspace_id: uuid.UUID, split_id: uuid.UUID) -> PacketSplit:
    split = db.execute(select(PacketSplit).where(PacketSplit.id == split_id, PacketSplit.workspace_id == workspace_id)).scalar_one_or_none()
    if split is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Split plan not found.")
    return split


def _item_or_404(db: Session, workspace_id: uuid.UUID, work_item_id: uuid.UUID) -> WorkItem:
    item = db.execute(select(WorkItem).where(WorkItem.id == work_item_id, WorkItem.workspace_id == workspace_id)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return item


def _unprocessable(exc: service.PacketError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": exc.code, "message": str(exc)})


def _row(db: Session, split: PacketSplit, filename: Optional[str] = None) -> PacketSplitRow:
    count = db.execute(select(func.count()).select_from(PacketSplitSegment).where(PacketSplitSegment.split_id == split.id)).scalar_one()
    if filename is None:
        filename = db.execute(select(WorkItem.original_filename).where(WorkItem.id == split.work_item_id)).scalar_one()
    return PacketSplitRow(id=split.id, work_item_id=split.work_item_id, original_filename=filename, status=split.status,
                          source=split.source, page_count=split.page_count, segment_count=int(count),
                          certainty=float(split.certainty) if split.certainty is not None else None,
                          model_version=split.model_version, proposed_at=split.proposed_at, decided_at=split.decided_at,
                          applied_at=split.applied_at)


def _detail(db: Session, split: PacketSplit) -> PacketSplitDetail:
    segments = [SegmentRow(id=s.id, ordinal=s.ordinal, page_start=s.page_start, page_end=s.page_end,
                           document_type=s.document_type, confidence=float(s.confidence) if s.confidence is not None else None,
                           title=s.title, child_work_item_id=s.child_work_item_id) for s in service.segments_of(db, split.id)]
    pages = [PageScore(page=int(p["page"]), p=float(p["p"]), doc_type=str(p.get("doc_type") or "other"),
                       features=p.get("features")) for p in (split.page_scores or [])]
    return PacketSplitDetail(split=_row(db, split), threshold=float(split.threshold), segments=segments, pages=pages,
                             failure_reason=split.failure_reason)


@router.get("/workspaces/{workspace_id}/packet-splits", response_model=PacketSplitList)
def list_packet_splits(
    workspace_id: uuid.UUID,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> PacketSplitList:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "packets.list")
    query = select(PacketSplit, WorkItem.original_filename).join(WorkItem, WorkItem.id == PacketSplit.work_item_id).where(
        PacketSplit.workspace_id == workspace_id, PacketSplit.status != v.STATUS_SUPERSEDED)
    if status_filter:
        query = query.where(PacketSplit.status == status_filter.strip().upper())
    rows = db.execute(query.order_by(PacketSplit.proposed_at.desc()).limit(limit)).all()
    return PacketSplitList(items=[_row(db, s, name) for s, name in rows], total=len(rows))


@router.get("/workspaces/{workspace_id}/packet-splits/{split_id}", response_model=PacketSplitDetail)
def get_packet_split(
    workspace_id: uuid.UUID,
    split_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> PacketSplitDetail:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "packets.read")
    return _detail(db, _split_or_404(db, workspace_id, split_id))


@router.post("/workspaces/{workspace_id}/work-items/{work_item_id}/packet-split", response_model=PacketSplitDetail)
def detect_packet_split(
    workspace_id: uuid.UUID,
    work_item_id: uuid.UUID,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> PacketSplitDetail:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "packets.detect")
    item = _item_or_404(db, workspace_id, work_item_id)
    try:
        split = service.detect(db, work_item=item, actor_user_id=context.user_id, force=force)
    except service.PacketError as exc:
        raise _unprocessable(exc) from exc
    db.commit()
    return _detail(db, split)


@router.put("/workspaces/{workspace_id}/packet-splits/{split_id}/boundaries", response_model=PacketSplitDetail)
def correct_packet_split(
    workspace_id: uuid.UUID,
    split_id: uuid.UUID,
    body: BoundariesRequest,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> PacketSplitDetail:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "packets.correct")
    split = _split_or_404(db, workspace_id, split_id)
    try:
        service.replan(db, split=split, boundaries=body.boundaries, actor_user_id=context.user_id)
    except service.PacketError as exc:
        raise _unprocessable(exc) from exc
    db.commit()
    return _detail(db, split)


@router.post("/workspaces/{workspace_id}/packet-splits/{split_id}/approve", response_model=PacketSplitDetail)
def approve_packet_split(
    workspace_id: uuid.UUID,
    split_id: uuid.UUID,
    body: Optional[ApproveRequest] = None,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> PacketSplitDetail:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "packets.approve")
    split = _split_or_404(db, workspace_id, split_id)
    try:
        service.approve(db, split=split, actor_user_id=context.user_id, boundaries=body.boundaries if body else None)
    except service.PacketError as exc:
        raise _unprocessable(exc) from exc
    db.commit()
    return _detail(db, split)


@router.post("/workspaces/{workspace_id}/packet-splits/{split_id}/reject", response_model=PacketSplitDetail)
def reject_packet_split(
    workspace_id: uuid.UUID,
    split_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> PacketSplitDetail:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "packets.reject")
    split = _split_or_404(db, workspace_id, split_id)
    try:
        service.reject(db, split=split, actor_user_id=context.user_id)
    except service.PacketError as exc:
        raise _unprocessable(exc) from exc
    db.commit()
    return _detail(db, split)


@router.get("/workspaces/{workspace_id}/packet-splits/{split_id}/pages/{page}/thumbnail")
def packet_page_thumbnail(
    workspace_id: uuid.UUID,
    split_id: uuid.UUID,
    page: int,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> Response:
    """ARCH43-S1:thumbnail. One page of the packet as a small PNG for the split
    review screen (pypdfium2, 0.35 scale). Private cache: the page is tenant data."""
    _assert_workspace(context, workspace_id)
    _gate(db, context, "packets.thumbnail")
    split = _split_or_404(db, workspace_id, split_id)
    if not 1 <= page <= split.page_count:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found.")
    item = _item_or_404(db, workspace_id, split.work_item_id)
    try:
        png = service.render_thumbnail(item, page)
    except service.PacketError as exc:
        raise _unprocessable(exc) from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.get("/workspaces/{workspace_id}/work-items/{work_item_id}/lineage", response_model=WorkItemLineage)
def get_work_item_lineage(
    workspace_id: uuid.UUID,
    work_item_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> WorkItemLineage:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "packets.lineage")
    try:
        return WorkItemLineage(**lineage.lineage_of(db, workspace_id=workspace_id, work_item_id=work_item_id))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.") from exc


__all__ = ["router"]
