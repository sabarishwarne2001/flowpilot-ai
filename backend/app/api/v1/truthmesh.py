"""Phase 2 — TruthMesh, the cross-document digital twin API.

Every route is gated on capability.truthmesh (402 CAPABILITY_REQUIRED without it). Reads need
VIEWER; deciding a conflict or a link, running a simulation and asking for a rebuild need
CONTRIBUTOR. Decisions, rebuild requests and simulations are audited.

    /workspaces/{ws}/truthmesh/overview                      the cockpit's numbers
    /workspaces/{ws}/truthmesh/graph                         nodes and links (optionally around one document)
    /workspaces/{ws}/truthmesh/documents/{work_item_id}      one document's twin: facts, terms, links, conflicts
    /workspaces/{ws}/truthmesh/conflicts                     list; /{id} decide; /export.csv audit export
    /workspaces/{ws}/truthmesh/matrix                        the discrepancy matrix
    /workspaces/{ws}/truthmesh/links/{id}/decision           confirm or reject a link
    /workspaces/{ws}/truthmesh/rebuild                       rebuild the workspace's mesh (a LIGHT job)
    /workspaces/{ws}/truthmesh/simulations                   run a what-if; list; /{id} one
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.truthmesh import MeshConflict, MeshLink, MeshSimulation
from app.models.workspace import WorkspaceRole
from app.schemas.truthmesh import (
    ConflictDecisionIn,
    LinkDecisionIn,
    MeshConflictList,
    MeshConflictOut,
    MeshDocumentOut,
    MeshGraphOut,
    MeshLinkOut,
    MeshMatrixOut,
    MeshOverviewOut,
    RebuildOut,
    SimulationIn,
    SimulationOut,
    SimulationSummaryOut,
)
from app.services import audit_service, job_service
from app.services.truthmesh import service
from app.services.truthmesh import vocabulary as v
from app.services.truthmesh.service import MeshError

router = APIRouter(tags=["TruthMesh"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
CAPABILITY = entitlements.TRUTHMESH_CAPABILITY
SEVERITY_FILTER = "^(CRITICAL|HIGH|MEDIUM|LOW)$"
STATUS_FILTER = "^(OPEN|ACKNOWLEDGED|RESOLVED|DISMISSED|ACTIVE)$"


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(db, context=context, capability_key=CAPABILITY, operation=operation)


def _ws(context: TenantContext, workspace_id: uuid.UUID) -> None:
    if context.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")


def _error(exc: MeshError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


def _audit(db: Session, context: TenantContext, *, resource_id: Optional[uuid.UUID], action: AuditAction,
           details: dict[str, Any]) -> None:
    audit_service.record(
        db, organization_id=context.organization_id, workspace_id=context.workspace_id, actor_id=context.user_id,
        resource_type=AuditResourceType.TRUTH_MESH, resource_id=resource_id, action=action,
        outcome=AuditOutcome.ALLOWED, details=details,
    )


def _enqueue_rebuild(db: Session, context: TenantContext, *, reason: str) -> uuid.UUID:
    service.mark_building(db, workspace_id=context.workspace_id, organization_id=context.organization_id)
    job = job_service.enqueue(
        db, job_type=v.JOB_REBUILD_WORKSPACE, organization_id=context.organization_id,
        payload={"workspace_id": str(context.workspace_id), "reason": reason},
        idempotency_key=f"{v.JOB_REBUILD_WORKSPACE}:{context.workspace_id}:{uuid.uuid4()}",
    )
    return job.id


# ------------------------------------------------------------------ cockpit

@router.get("/workspaces/{workspace_id}/truthmesh/overview", response_model=MeshOverviewOut)
def get_overview(
    workspace_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> Any:
    _ws(context, workspace_id)
    _gate(db, context, "truthmesh.overview")
    return service.overview(db, workspace_id)


@router.get("/workspaces/{workspace_id}/truthmesh/graph", response_model=MeshGraphOut)
def get_graph(
    workspace_id: uuid.UUID,
    focus: Optional[uuid.UUID] = Query(default=None),
    depth: int = Query(default=2, ge=1, le=4),
    min_strength: float = Query(default=v.MIN_LINK_STRENGTH, ge=0, le=1),
    limit: int = Query(default=v.GRAPH_MAX_NODES, ge=10, le=v.GRAPH_MAX_NODES),
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> Any:
    _ws(context, workspace_id)
    _gate(db, context, "truthmesh.graph")
    return service.graph(db, workspace_id, focus=focus, depth=depth, min_strength=min_strength, limit=limit)


@router.get("/workspaces/{workspace_id}/truthmesh/documents/{work_item_id}", response_model=MeshDocumentOut)
def get_document(
    workspace_id: uuid.UUID,
    work_item_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> Any:
    _ws(context, workspace_id)
    _gate(db, context, "truthmesh.document")
    try:
        return service.document(db, workspace_id, work_item_id)
    except MeshError as exc:
        raise _error(exc) from exc


@router.get("/workspaces/{workspace_id}/truthmesh/matrix", response_model=MeshMatrixOut)
def get_matrix(
    workspace_id: uuid.UUID,
    documents: int = Query(default=12, ge=2, le=30),
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> Any:
    _ws(context, workspace_id)
    _gate(db, context, "truthmesh.matrix")
    return service.matrix(db, workspace_id, limit_documents=documents)


# ------------------------------------------------------------------ conflicts

@router.get("/workspaces/{workspace_id}/truthmesh/conflicts", response_model=MeshConflictList)
def list_conflicts(
    workspace_id: uuid.UUID,
    conflict_status: Optional[str] = Query(default="ACTIVE", alias="status", pattern=STATUS_FILTER),
    severity: Optional[str] = Query(default=None, pattern=SEVERITY_FILTER),
    kind: Optional[str] = Query(default=None, max_length=32),
    work_item_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> Any:
    _ws(context, workspace_id)
    _gate(db, context, "truthmesh.conflicts.list")
    query = select(MeshConflict).where(MeshConflict.workspace_id == workspace_id)
    if conflict_status == "ACTIVE":
        query = query.where(MeshConflict.status.in_(("OPEN", "ACKNOWLEDGED")))
    elif conflict_status:
        query = query.where(MeshConflict.status == conflict_status)
    if severity:
        query = query.where(MeshConflict.severity == severity)
    if kind:
        query = query.where(MeshConflict.kind == kind)
    if work_item_id:
        query = query.where(MeshConflict.work_item_ids.overlap([work_item_id]))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    order = func.array_position(["CRITICAL", "HIGH", "MEDIUM", "LOW"], MeshConflict.severity)
    rows = db.execute(query.order_by(order, MeshConflict.exposure_micros.desc().nulls_last(),
                                     MeshConflict.first_seen_at.desc(), MeshConflict.id)
                      .limit(limit).offset(offset)).scalars().all()
    return {"items": [service.conflict_out(c) for c in rows], "total": int(total)}


@router.get("/workspaces/{workspace_id}/truthmesh/conflicts/export.csv")
def export_conflicts(
    workspace_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "truthmesh.conflicts.export")
    body = service.export_csv(db, workspace_id)
    _audit(db, context, resource_id=None, action=AuditAction.EXPORTED, details={"what": "truthmesh_conflicts"})
    db.commit()
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="truthmesh-conflicts.csv"'})


@router.patch("/workspaces/{workspace_id}/truthmesh/conflicts/{conflict_id}", response_model=MeshConflictOut)
def decide_conflict(
    workspace_id: uuid.UUID,
    conflict_id: uuid.UUID,
    payload: ConflictDecisionIn,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> Any:
    _ws(context, workspace_id)
    _gate(db, context, "truthmesh.conflicts.decide")
    conflict = db.execute(select(MeshConflict).where(MeshConflict.id == conflict_id,
                                                     MeshConflict.workspace_id == workspace_id)).scalar_one_or_none()
    if conflict is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conflict not found.")
    previous = conflict.status
    try:
        service.decide_conflict(db, conflict=conflict, status=payload.status, note=payload.note,
                                actor_id=context.user_id)
    except MeshError as exc:
        db.rollback()
        raise _error(exc) from exc
    _audit(db, context, resource_id=conflict.id, action=AuditAction.UPDATED,
           details={"conflict": conflict.kind, "from": previous, "to": payload.status})
    db.commit()
    return service.conflict_out(conflict)


@router.post("/workspaces/{workspace_id}/truthmesh/links/{link_id}/decision", response_model=MeshLinkOut)
def decide_link(
    workspace_id: uuid.UUID,
    link_id: uuid.UUID,
    payload: LinkDecisionIn,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> Any:
    _ws(context, workspace_id)
    _gate(db, context, "truthmesh.links.decide")
    link = db.execute(select(MeshLink).where(MeshLink.id == link_id, MeshLink.workspace_id == workspace_id)
                      ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found.")
    try:
        service.decide_link(db, link=link, decision=payload.decision, actor_id=context.user_id)
    except MeshError as exc:
        db.rollback()
        raise _error(exc) from exc
    _audit(db, context, resource_id=link.id, action=AuditAction.UPDATED,
           details={"link": link.relation, "decision": payload.decision})
    db.commit()
    return service._link_out(link)


# ------------------------------------------------------------------ builds

@router.post("/workspaces/{workspace_id}/truthmesh/rebuild", response_model=RebuildOut,
             status_code=status.HTTP_202_ACCEPTED)
def rebuild(
    workspace_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> Any:
    _ws(context, workspace_id)
    _gate(db, context, "truthmesh.rebuild")
    job_id = _enqueue_rebuild(db, context, reason="requested")
    _audit(db, context, resource_id=None, action=AuditAction.SYNC_TRIGGERED, details={"what": "truthmesh_rebuild"})
    db.commit()
    return {"job_id": job_id, "status": "BUILDING"}


# ------------------------------------------------------------------ simulations

@router.post("/workspaces/{workspace_id}/truthmesh/simulations", response_model=SimulationOut,
             status_code=status.HTTP_201_CREATED)
def run_simulation(
    workspace_id: uuid.UUID,
    payload: SimulationIn,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireContributor),
) -> Any:
    _ws(context, workspace_id)
    _gate(db, context, "truthmesh.simulate")
    try:
        parameters = payload.parameters()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_PARAMETERS", "message": str(exc)}) from exc
    try:
        row = service.simulate(db, workspace_id=workspace_id, organization_id=context.organization_id,
                               actor_id=context.user_id, work_item_id=payload.work_item_id,
                               scenario=payload.scenario, parameters=parameters)
    except MeshError as exc:
        db.rollback()
        raise _error(exc) from exc
    _audit(db, context, resource_id=row.id, action=AuditAction.GENERATED,
           details={"simulation": payload.scenario, "origin": str(payload.work_item_id),
                    "documents_affected": row.documents_affected})
    db.commit()
    return SimulationOut.model_validate(row, from_attributes=True)


@router.get("/workspaces/{workspace_id}/truthmesh/simulations", response_model=list[SimulationSummaryOut])
def list_simulations(
    workspace_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> Any:
    _ws(context, workspace_id)
    _gate(db, context, "truthmesh.simulations.list")
    rows = db.execute(select(MeshSimulation).where(MeshSimulation.workspace_id == workspace_id)
                      .order_by(MeshSimulation.created_at.desc(), MeshSimulation.id).limit(limit)).scalars().all()
    return [SimulationSummaryOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/workspaces/{workspace_id}/truthmesh/simulations/{simulation_id}", response_model=SimulationOut)
def get_simulation(
    workspace_id: uuid.UUID,
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: TenantContext = Depends(RequireViewer),
) -> Any:
    _ws(context, workspace_id)
    _gate(db, context, "truthmesh.simulations.read")
    row = db.execute(select(MeshSimulation).where(MeshSimulation.id == simulation_id,
                                                  MeshSimulation.workspace_id == workspace_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found.")
    return SimulationOut.model_validate(row, from_attributes=True)
