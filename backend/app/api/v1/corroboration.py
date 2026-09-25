"""ARCH45-S1:api — the Universal Document Corroborator & Discrepancy Matrix. Every
route is gated on capability.universal_corroborator (402 CAPABILITY_REQUIRED
without it); reads need VIEWER, comparisons and decisions CONTRIBUTOR."""

from __future__ import annotations

import io
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.corroboration import CorroborationDocument, CorroborationPair, CorroborationRun, Discrepancy
from app.models.work_item import WorkItem
from app.models.workspace import WorkspaceRole
from app.schemas.corroboration import (
    CorroborationRequest,
    DecisionRequest,
    DiscrepancyRow,
    DocumentComparisons,
    PageGeometry,
    PairRow,
    RequestResult,
    ReviewRunRequest,
    ReviewRunResult,
    RuleRow,
    RunDetail,
    RunDocument,
    RunDocumentBrief,
    RunList,
    RunSummary,
    WorkspaceRules,
)
from app.services import audit_service
from app.services.corroboration import loader, report, service
from app.services.corroboration import vocabulary as v

router = APIRouter(tags=["Document Corroborator"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
CAPABILITY = entitlements.UNIVERSAL_CORROBORATOR_CAPABILITY
RENDERABLE = ("application/pdf", "image/png", "image/jpeg", "image/tiff", "image/webp")


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(db, context=context, capability_key=CAPABILITY, operation=operation)


def _ws(context: TenantContext, workspace_id: uuid.UUID) -> None:
    if context.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")


def _run_or_404(db: Session, workspace_id: uuid.UUID, run_id: uuid.UUID) -> CorroborationRun:
    run = db.execute(select(CorroborationRun).where(CorroborationRun.id == run_id,
                                                    CorroborationRun.workspace_id == workspace_id)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comparison not found.")
    return run


def _error(exc: service.CorroborationError) -> HTTPException:
    codes = {"NOT_FOUND": status.HTTP_404_NOT_FOUND, "NOT_READY": status.HTTP_409_CONFLICT,
             "NOT_COMPLETED": status.HTTP_409_CONFLICT, "NOTHING_OPEN": status.HTTP_409_CONFLICT}
    return HTTPException(status_code=codes.get(exc.code, status.HTTP_422_UNPROCESSABLE_ENTITY),
                         detail={"code": exc.code, "message": str(exc)})


def _briefs(db: Session, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[RunDocumentBrief]]:
    out: dict[uuid.UUID, list[RunDocumentBrief]] = {rid: [] for rid in run_ids}
    if not run_ids:
        return out
    for row in db.execute(select(CorroborationDocument).where(CorroborationDocument.run_id.in_(run_ids))
                          .order_by(CorroborationDocument.run_id, CorroborationDocument.position)).scalars():
        out[row.run_id].append(RunDocumentBrief(work_item_id=row.work_item_id, position=row.position, label=row.label))
    return out


def _summary(run: CorroborationRun, briefs: list[RunDocumentBrief]) -> RunSummary:
    return RunSummary(id=run.id, status=run.status, document_count=run.document_count,
                      discrepancy_count=run.discrepancy_count, material_count=run.material_count,
                      open_material_count=run.open_material_count, max_materiality=float(run.max_materiality),
                      encoder=run.encoder, engine_version=run.engine_version, fingerprint=run.fingerprint,
                      set_hash=run.set_hash, error=run.error, created_by_user_id=run.created_by_user_id,
                      created_at=run.created_at, completed_at=run.completed_at, stale_at=run.stale_at,
                      reviewed_at=run.reviewed_at, documents=briefs)


def _one_summary(db: Session, run: CorroborationRun) -> RunSummary:
    return _summary(run, _briefs(db, [run.id])[run.id])


def _rule_rows(run: CorroborationRun) -> list[RuleRow]:
    return [RuleRow(key=r["key"], sentence=r.get("sentence", ""), source=r.get("source", ""), family=r.get("family", ""),
                    understood_as=r.get("understood_as", ""), digest=r.get("digest", ""),
                    definition_id=r.get("definition_id")) for r in run.rules or []]


def _detail(db: Session, run: CorroborationRun) -> RunDetail:
    docs = list(db.execute(select(CorroborationDocument).where(CorroborationDocument.run_id == run.id)
                           .order_by(CorroborationDocument.position)).scalars())
    items = {w.id: w for w in db.execute(select(WorkItem).where(
        WorkItem.id.in_([d.work_item_id for d in docs]))).scalars()}
    try:
        current = service.contents_of(loader.load(db, run.workspace_id, [d.work_item_id for d in docs],
                                                  build_inputs=False))
    except LookupError:
        current = {}
    stale = run.status == v.STATUS_STALE or (
        run.status == v.STATUS_COMPLETED and (service.fingerprint_of(run, current) != run.fingerprint
                                              or run.engine_version != v.ENGINE_VERSION))
    documents = []
    for d in docs:
        item = items.get(d.work_item_id)
        mime = ((item.file_type if item else "") or "").split(";")[0].strip().lower()
        documents.append(RunDocument(
            work_item_id=d.work_item_id, position=d.position, label=d.label, page_count=d.page_count,
            content_hash=d.content_hash, file_type=mime, renderable=mime in RENDERABLE,
            changed_since=run.status in v.RESULT_STATUSES and current.get(str(d.work_item_id)) not in (None, d.content_hash),
            pages=[PageGeometry(**g) for g in (loader.page_geometry(item) if item else [])]))
    rows = db.execute(select(Discrepancy).where(Discrepancy.run_id == run.id).order_by(Discrepancy.ordinal)).scalars()
    pairs = db.execute(select(CorroborationPair).where(CorroborationPair.run_id == run.id)
                       .order_by(CorroborationPair.left_work_item_id, CorroborationPair.right_work_item_id)).scalars()
    layers = {k: val for k, val in (run.layers or {}).items()}
    return RunDetail(
        run=_summary(run, [RunDocumentBrief(work_item_id=d.work_item_id, position=d.position, label=d.label)
                           for d in docs]),
        stale=bool(stale), options=dict(run.options or {}), layers=layers, stats=dict(run.stats or {}),
        anchors=list(run.anchors or []), documents=documents,
        discrepancies=[DiscrepancyRow(id=x.id, ordinal=x.ordinal, layer=x.layer, kind=x.kind, group_key=x.group_key,
                                      label=x.label, summary=x.summary, materiality=float(x.materiality),
                                      severity=x.severity, is_material=x.is_material, values=x.doc_values or {},
                                      evidence=list(x.evidence or []), detail=x.detail or {}, status=x.status,
                                      decided_at=x.decided_at, decided_by_user_id=x.decided_by_user_id, note=x.note)
                       for x in rows],
        pairs=[PairRow(left_work_item_id=p.left_work_item_id, right_work_item_id=p.right_work_item_id,
                       clauses_left=p.clauses_left, clauses_right=p.clauses_right, clauses_matched=p.clauses_matched,
                       clauses_identical=p.clauses_identical, clause_similarity=float(p.clause_similarity),
                       fields_compared=p.fields_compared, fields_agreeing=p.fields_agreeing, lines_left=p.lines_left,
                       lines_right=p.lines_right, lines_matched=p.lines_matched, lines_agreeing=p.lines_agreeing,
                       entities_compared=p.entities_compared, entities_agreeing=p.entities_agreeing,
                       discrepancy_count=p.discrepancy_count, material_count=p.material_count,
                       agreement=float(p.agreement), alignment=list(p.alignment or [])) for p in pairs],
        rules=_rule_rows(run))


def _audit_export(db: Session, context: TenantContext, **details: object) -> None:
    audit_service.record(db, organization_id=context.organization_id, workspace_id=context.workspace_id,
                         actor_id=context.user_id, resource_type=AuditResourceType.WORKSPACE,
                         resource_id=context.workspace_id, action=AuditAction.EXPORTED, outcome=AuditOutcome.ALLOWED,
                         details={"corroboration": {"operation": "export", **details}})


def _options(body: CorroborationRequest) -> dict:
    out: dict = {}
    if body.materiality_threshold is not None:
        out["materiality_threshold"] = str(body.materiality_threshold)
    if body.money_tolerance is not None:
        out["money_tolerance"] = str(body.money_tolerance)
    if body.relative_tolerance is not None:
        out["relative_tolerance"] = str(body.relative_tolerance)
    if body.layers is not None:
        out["layers"] = list(body.layers)
    return out


@router.get("/workspaces/{workspace_id}/corroboration/runs", response_model=RunList)
def list_runs(workspace_id: uuid.UUID, status_filter: Optional[str] = Query(default=None, alias="status"),
              limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
              db: Session = Depends(get_db), context: TenantContext = Depends(RequireViewer)) -> RunList:
    _ws(context, workspace_id)
    _gate(db, context, "corroboration.list")
    query = select(CorroborationRun).where(CorroborationRun.workspace_id == workspace_id)
    count = select(func.count()).select_from(CorroborationRun).where(CorroborationRun.workspace_id == workspace_id)
    if status_filter:
        wanted = status_filter.strip().upper()
        query, count = query.where(CorroborationRun.status == wanted), count.where(CorroborationRun.status == wanted)
    runs = list(db.execute(query.order_by(CorroborationRun.created_at.desc()).limit(limit).offset(offset)).scalars())
    briefs = _briefs(db, [r.id for r in runs])
    return RunList(items=[_summary(r, briefs[r.id]) for r in runs], total=int(db.execute(count).scalar_one()),
                   counts_by_status=service.workspace_counts(db, workspace_id))


@router.post("/workspaces/{workspace_id}/corroboration/runs", response_model=RequestResult)
def request_run(workspace_id: uuid.UUID, body: CorroborationRequest, response: Response, db: Session = Depends(get_db),
                context: TenantContext = Depends(RequireContributor)) -> RequestResult:
    _ws(context, workspace_id)
    _gate(db, context, "corroboration.request")
    try:
        run, cached = service.request(db, organization_id=context.organization_id, workspace_id=workspace_id,
                                      actor_user_id=context.user_id, work_item_ids=body.work_item_ids,
                                      options=_options(body), rules=body.rules,
                                      use_workspace_rules=body.use_workspace_rules, force=body.force)
    except service.CorroborationError as exc:
        raise _error(exc) from exc
    db.commit()
    response.status_code = status.HTTP_200_OK if cached else status.HTTP_202_ACCEPTED
    return RequestResult(run=_one_summary(db, run), cached=cached)


@router.get("/workspaces/{workspace_id}/corroboration/runs/{run_id}", response_model=RunDetail)
def get_run(workspace_id: uuid.UUID, run_id: uuid.UUID, db: Session = Depends(get_db),
            context: TenantContext = Depends(RequireViewer)) -> RunDetail:
    _ws(context, workspace_id)
    _gate(db, context, "corroboration.read")
    return _detail(db, _run_or_404(db, workspace_id, run_id))


@router.post("/workspaces/{workspace_id}/corroboration/runs/{run_id}/refresh", response_model=RequestResult)
def refresh_run(workspace_id: uuid.UUID, run_id: uuid.UUID, response: Response, db: Session = Depends(get_db),
                context: TenantContext = Depends(RequireContributor)) -> RequestResult:
    _ws(context, workspace_id)
    _gate(db, context, "corroboration.refresh")
    run = _run_or_404(db, workspace_id, run_id)
    try:
        fresh, cached = service.refresh(db, run=run, actor_user_id=context.user_id)
    except service.CorroborationError as exc:
        raise _error(exc) from exc
    db.commit()
    response.status_code = status.HTTP_200_OK if cached else status.HTTP_202_ACCEPTED
    return RequestResult(run=_one_summary(db, fresh), cached=cached)


@router.patch("/workspaces/{workspace_id}/corroboration/runs/{run_id}/discrepancies/{discrepancy_id}",
              response_model=DiscrepancyRow)
def decide_discrepancy(workspace_id: uuid.UUID, run_id: uuid.UUID, discrepancy_id: uuid.UUID, body: DecisionRequest,
                       db: Session = Depends(get_db), context: TenantContext = Depends(RequireContributor)) -> DiscrepancyRow:
    _ws(context, workspace_id)
    _gate(db, context, "corroboration.decide")
    run = _run_or_404(db, workspace_id, run_id)
    row = db.execute(select(Discrepancy).where(Discrepancy.id == discrepancy_id, Discrepancy.run_id == run.id)) \
        .scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Discrepancy not found.")
    try:
        service.decide(db, run=run, discrepancy=row, status=body.status, note=body.note, actor_user_id=context.user_id)
    except service.CorroborationError as exc:
        raise _error(exc) from exc
    db.commit()
    return DiscrepancyRow(id=row.id, ordinal=row.ordinal, layer=row.layer, kind=row.kind, group_key=row.group_key,
                          label=row.label, summary=row.summary, materiality=float(row.materiality), severity=row.severity,
                          is_material=row.is_material, values=row.doc_values or {}, evidence=list(row.evidence or []),
                          detail=row.detail or {}, status=row.status, decided_at=row.decided_at,
                          decided_by_user_id=row.decided_by_user_id, note=row.note)


@router.post("/workspaces/{workspace_id}/corroboration/runs/{run_id}/review", response_model=ReviewRunResult)
def review_run(workspace_id: uuid.UUID, run_id: uuid.UUID, body: ReviewRunRequest, db: Session = Depends(get_db),
               context: TenantContext = Depends(RequireContributor)) -> ReviewRunResult:
    _ws(context, workspace_id)
    _gate(db, context, "corroboration.review")
    run = _run_or_404(db, workspace_id, run_id)
    try:
        decided = service.review_run(db, run=run, verdict=body.verdict, actor_user_id=context.user_id)
    except service.CorroborationError as exc:
        raise _error(exc) from exc
    db.commit()
    return ReviewRunResult(decided=decided, run=_one_summary(db, run))


@router.get("/workspaces/{workspace_id}/corroboration/runs/{run_id}/export")
def export_run(workspace_id: uuid.UUID, run_id: uuid.UUID, fmt: str = Query(default="pdf", alias="format"),
               db: Session = Depends(get_db), context: TenantContext = Depends(RequireViewer)) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "corroboration.export")
    fmt = fmt.strip().lower()
    if fmt not in v.FORMATS:
        raise HTTPException(status_code=422, detail={"code": "UNKNOWN_FORMAT", "message": "format is pdf, json or csv."})
    run = _run_or_404(db, workspace_id, run_id)
    if run.status not in v.RESULT_STATUSES:
        raise HTTPException(status_code=409, detail={"code": "NOT_COMPLETED",
                                                     "message": "This comparison has no result to export yet."})
    bundle = _detail(db, run).model_dump(mode="json")
    bundle_for_report = {"run": {**bundle["run"], "options": bundle["options"], "layers": bundle["layers"],
                                 "stats": bundle["stats"], "rules": [r.model_dump(mode="json") for r in _rule_rows(run)]},
                         "stale": bundle["stale"], "documents": bundle["documents"],
                         "discrepancies": bundle["discrepancies"], "pairs": bundle["pairs"]}
    data = {v.FORMAT_PDF: lambda: report.to_pdf(bundle_for_report), v.FORMAT_JSON: lambda: report.to_json(bundle),
            v.FORMAT_CSV: lambda: report.to_csv(bundle)}[fmt]()
    _audit_export(db, context, run_id=str(run.id), format=fmt, bytes=len(data))
    db.commit()
    name = report.filename(bundle["documents"], fmt)
    return Response(content=data, media_type=report.CONTENT_TYPES[fmt],
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/workspaces/{workspace_id}/corroboration/runs/{run_id}/documents/{work_item_id}/pages/{page}/image")
def page_image(workspace_id: uuid.UUID, run_id: uuid.UUID, work_item_id: uuid.UUID, page: int,
               dpi: int = Query(default=100, ge=50, le=200), db: Session = Depends(get_db),
               context: TenantContext = Depends(RequireViewer)) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "corroboration.page")
    run = _run_or_404(db, workspace_id, run_id)
    member = db.execute(select(CorroborationDocument).where(CorroborationDocument.run_id == run.id,
                                                            CorroborationDocument.work_item_id == work_item_id)) \
        .scalar_one_or_none()
    item = db.execute(select(WorkItem).where(WorkItem.id == work_item_id, WorkItem.workspace_id == workspace_id)) \
        .scalar_one_or_none()
    if member is None or item is None:
        raise HTTPException(status_code=404, detail="Document not found in this comparison.")
    mime = (item.file_type or "").split(";")[0].strip().lower()
    if mime not in RENDERABLE:
        raise HTTPException(status_code=404, detail={"code": "NOT_RENDERABLE", "message": "This document has no page images."})
    from app.core.storage import get_storage_driver

    raw = get_storage_driver().get(item.stored_filename)
    buffer = io.BytesIO()
    if mime == "application/pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(raw)
        try:
            if not 1 <= page <= len(pdf):
                raise HTTPException(status_code=404, detail={"code": "NO_PAGE", "message": "Page out of range."})
            pdf[page - 1].render(scale=dpi / 72.0).to_pil().save(buffer, format="PNG", optimize=True)
        finally:
            pdf.close()
    else:
        from PIL import Image

        image = Image.open(io.BytesIO(raw))
        frames = getattr(image, "n_frames", 1)
        if not 1 <= page <= frames:
            raise HTTPException(status_code=404, detail={"code": "NO_PAGE", "message": "Page out of range."})
        image.seek(page - 1)
        frame = image.convert("RGB")
        frame.thumbnail((int(8.5 * dpi * 2), int(11 * dpi * 2)))
        frame.save(buffer, format="PNG", optimize=True)
    # A rendering of the document itself: it must not outlive the request.
    return Response(content=buffer.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store, private", "Pragma": "no-cache"})


@router.delete("/workspaces/{workspace_id}/corroboration/runs/{run_id}", status_code=204)
def delete_run(workspace_id: uuid.UUID, run_id: uuid.UUID, db: Session = Depends(get_db),
               context: TenantContext = Depends(RequireContributor)) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "corroboration.delete")
    run = _run_or_404(db, workspace_id, run_id)
    service.delete_run(db, run=run, actor_user_id=context.user_id)
    db.commit()
    return Response(status_code=204)


@router.get("/workspaces/{workspace_id}/corroboration/rules", response_model=WorkspaceRules)
def workspace_rules(workspace_id: uuid.UUID, db: Session = Depends(get_db),
                    context: TenantContext = Depends(RequireViewer)) -> WorkspaceRules:
    _ws(context, workspace_id)
    _gate(db, context, "corroboration.rules")
    specs, skipped = service.workspace_rules(db, workspace_id)
    rows = [spec.as_json() for spec in specs]
    return WorkspaceRules(rules=[RuleRow(key=r["key"], sentence=r["sentence"], source=r["source"], family=r["family"],
                                         understood_as=r["understood_as"], digest=r["digest"],
                                         definition_id=r["definition_id"]) for r in rows], skipped=skipped)


@router.get("/workspaces/{workspace_id}/work-items/{work_item_id}/corroboration", response_model=DocumentComparisons)
def document_runs(workspace_id: uuid.UUID, work_item_id: uuid.UUID, db: Session = Depends(get_db),
                  context: TenantContext = Depends(RequireViewer)) -> DocumentComparisons:
    _ws(context, workspace_id)
    _gate(db, context, "corroboration.document")
    item = db.execute(select(WorkItem).where(WorkItem.id == work_item_id, WorkItem.workspace_id == workspace_id)) \
        .scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    runs = sorted(service.runs_with(db, [item.id]), key=lambda r: r.created_at, reverse=True)[:50]
    briefs = _briefs(db, [r.id for r in runs])
    return DocumentComparisons(work_item_id=item.id, original_filename=item.original_filename,
                               runs=[_summary(r, briefs[r.id]) for r in runs])


__all__ = ["router"]
