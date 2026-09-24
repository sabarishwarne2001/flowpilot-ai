"""ARCH43-S1:api-cases — Case Intelligence. Every workspace route is gated on
capability.case_intelligence; the recipient-facing upload lives in
public_document_requests.py and is authorised by its single-use token only."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.cases import Case, CaseDocument, CaseRuleResult, CaseTemplate, DocumentRequest
from app.models.work_item import WorkItem
from app.models.workspace import WorkspaceRole
from app.schemas.cases import (
    CaseCreate,
    CaseDetail,
    CaseDocumentAdd,
    CaseDocumentRow,
    CaseList,
    CaseRow,
    ChecklistSlot,
    RequestCreate,
    RequestCreated,
    RequestRow,
    RuleResultRow,
    TemplateRow,
    TemplateWrite,
)
from app.services.cases import assembly, doc_types, requests, templates
from app.services.cases import vocabulary as v

router = APIRouter(tags=["Case Intelligence"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
RequireAdmin = RequireWorkspaceRole(WorkspaceRole.ADMIN)
CAPABILITY = entitlements.CASE_INTELLIGENCE_CAPABILITY


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(db, context=context, capability_key=CAPABILITY, operation=operation)


def _ws(context: TenantContext, workspace_id: uuid.UUID) -> None:
    if context.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")


def _422(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                         detail={"code": getattr(exc, "code", "INVALID"), "message": str(exc)})


def _template(db: Session, workspace_id: uuid.UUID, template_id: uuid.UUID) -> CaseTemplate:
    row = db.execute(select(CaseTemplate).where(CaseTemplate.id == template_id, CaseTemplate.workspace_id == workspace_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case template not found.")
    return row


def _case(db: Session, workspace_id: uuid.UUID, case_id: uuid.UUID) -> Case:
    row = db.execute(select(Case).where(Case.id == case_id, Case.workspace_id == workspace_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found.")
    return row


def _template_row(t: CaseTemplate) -> TemplateRow:
    return TemplateRow(id=t.id, key=t.key, version=t.version, name=t.name, description=t.description, status=t.status,
                       assembly_key=t.assembly_key, entity_kind=t.entity_kind, entity_role=t.entity_role,
                       required_documents=list(t.required_documents or []), rules=list(t.rules or []),
                       request_ttl_hours=t.request_ttl_hours, published_at=t.published_at)


def _case_row(db: Session, case: Case, template_key: Optional[str] = None) -> CaseRow:
    documents = db.execute(select(func.count()).select_from(CaseDocument).where(CaseDocument.case_id == case.id)).scalar_one()
    failed = db.execute(select(func.count()).select_from(CaseRuleResult).where(
        CaseRuleResult.case_id == case.id, CaseRuleResult.outcome == "FAIL")).scalar_one()
    if template_key is None:
        template_key = db.execute(select(CaseTemplate.key).where(CaseTemplate.id == case.template_id)).scalar_one()
    return CaseRow(id=case.id, title=case.title, status=case.status, completeness=float(case.completeness),
                   template_id=case.template_id, template_key=template_key, anchor_kind=case.anchor_kind,
                   anchor_entity_id=case.anchor_entity_id, anchor_batch_id=case.anchor_batch_id, documents=int(documents),
                   failed_rules=int(failed), created_at=case.created_at, evaluated_at=case.evaluated_at)


def _request_row(r: DocumentRequest) -> RequestRow:
    return RequestRow(id=r.id, document_type=r.document_type, recipient_label=r.recipient_label, status=r.status,
                      expires_at=r.expires_at, used_at=r.used_at, fulfilled_work_item_id=r.fulfilled_work_item_id)


def _detail(db: Session, case: Case) -> CaseDetail:
    template = db.get(CaseTemplate, case.template_id)
    labels = {r["id"]: r.get("label") or r["id"] for r in template.rules or []}
    docs = db.execute(select(CaseDocument, WorkItem.original_filename).join(WorkItem, WorkItem.id == CaseDocument.work_item_id)
                      .where(CaseDocument.case_id == case.id).order_by(CaseDocument.added_at)).all()
    results = db.execute(select(CaseRuleResult).where(CaseRuleResult.case_id == case.id)).scalars()
    reqs = db.execute(select(DocumentRequest).where(DocumentRequest.case_id == case.id).order_by(DocumentRequest.created_at.desc())).scalars()
    return CaseDetail(
        case=_case_row(db, case, template.key), template=_template_row(template),
        checklist=[ChecklistSlot(**s) for s in assembly.checklist(db, case, template)],
        documents=[CaseDocumentRow(work_item_id=d.work_item_id, original_filename=name, document_type=d.document_type,
                                   source=d.source, added_at=d.added_at) for d, name in docs],
        rules=[RuleResultRow(rule_id=r.rule_id, label=labels.get(r.rule_id, r.rule_id), op=r.op, outcome=r.outcome,
                             left_value=r.left_value, right_value=r.right_value, detail=r.detail or {}) for r in results],
        requests=[_request_row(r) for r in reqs])


@router.get("/workspaces/{workspace_id}/case-templates", response_model=list[TemplateRow])
def list_case_templates(workspace_id: uuid.UUID, db: Session = Depends(get_db), context: TenantContext = Depends(RequireViewer)) -> list[TemplateRow]:
    _ws(context, workspace_id)
    _gate(db, context, "cases.templates.list")
    rows = db.execute(select(CaseTemplate).where(CaseTemplate.workspace_id == workspace_id)
                      .order_by(CaseTemplate.key, CaseTemplate.version.desc())).scalars()
    return [_template_row(t) for t in rows]


@router.post("/workspaces/{workspace_id}/case-templates", response_model=TemplateRow, status_code=201)
def create_case_template(workspace_id: uuid.UUID, body: TemplateWrite, db: Session = Depends(get_db),
                         context: TenantContext = Depends(RequireAdmin)) -> TemplateRow:
    _ws(context, workspace_id)
    _gate(db, context, "cases.templates.create")
    try:
        template = templates.create(db, organization_id=context.organization_id, workspace_id=workspace_id,
                                    payload=body.model_dump(), actor_user_id=context.user_id)
    except templates.TemplateError as exc:
        raise _422(exc) from exc
    db.commit()
    return _template_row(template)


@router.put("/workspaces/{workspace_id}/case-templates/{template_id}", response_model=TemplateRow)
def update_case_template(workspace_id: uuid.UUID, template_id: uuid.UUID, body: TemplateWrite, db: Session = Depends(get_db),
                         context: TenantContext = Depends(RequireAdmin)) -> TemplateRow:
    _ws(context, workspace_id)
    _gate(db, context, "cases.templates.update")
    template = _template(db, workspace_id, template_id)
    try:
        templates.update_draft(db, template=template, payload=body.model_dump())
    except templates.TemplateError as exc:
        if exc.code == "IMMUTABLE":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": exc.code, "message": str(exc)}) from exc
        raise _422(exc) from exc
    db.commit()
    return _template_row(template)


@router.post("/workspaces/{workspace_id}/case-templates/{template_id}/publish", response_model=TemplateRow)
def publish_case_template(workspace_id: uuid.UUID, template_id: uuid.UUID, db: Session = Depends(get_db),
                          context: TenantContext = Depends(RequireAdmin)) -> TemplateRow:
    _ws(context, workspace_id)
    _gate(db, context, "cases.templates.publish")
    template = _template(db, workspace_id, template_id)
    try:
        templates.publish(db, template=template)
    except templates.TemplateError as exc:
        raise _422(exc) from exc
    db.commit()
    return _template_row(template)


@router.post("/workspaces/{workspace_id}/case-templates/{template_id}/retire", response_model=TemplateRow)
def retire_case_template(workspace_id: uuid.UUID, template_id: uuid.UUID, db: Session = Depends(get_db),
                         context: TenantContext = Depends(RequireAdmin)) -> TemplateRow:
    _ws(context, workspace_id)
    _gate(db, context, "cases.templates.retire")
    template = _template(db, workspace_id, template_id)
    try:
        templates.retire(db, template=template)
    except templates.TemplateError as exc:
        raise _422(exc) from exc
    db.commit()
    return _template_row(template)


@router.get("/workspaces/{workspace_id}/cases", response_model=CaseList)
def list_cases(workspace_id: uuid.UUID, status_filter: Optional[str] = Query(default=None, alias="status"),
               limit: int = Query(default=200, ge=1, le=500), db: Session = Depends(get_db),
               context: TenantContext = Depends(RequireViewer)) -> CaseList:
    _ws(context, workspace_id)
    _gate(db, context, "cases.list")
    query = select(Case, CaseTemplate.key).join(CaseTemplate, CaseTemplate.id == Case.template_id).where(Case.workspace_id == workspace_id)
    if status_filter:
        query = query.where(Case.status == status_filter.strip().upper())
    rows = db.execute(query.order_by(Case.created_at.desc()).limit(limit)).all()
    counts = dict(db.execute(select(Case.status, func.count()).where(Case.workspace_id == workspace_id).group_by(Case.status)).all())
    return CaseList(items=[_case_row(db, c, key) for c, key in rows], counts_by_status={s: int(counts.get(s, 0)) for s in v.CASE_STATUSES})


@router.post("/workspaces/{workspace_id}/cases", response_model=CaseDetail, status_code=201)
def create_case(workspace_id: uuid.UUID, body: CaseCreate, db: Session = Depends(get_db),
                context: TenantContext = Depends(RequireContributor)) -> CaseDetail:
    _ws(context, workspace_id)
    _gate(db, context, "cases.create")
    template = _template(db, workspace_id, body.template_id)
    if template.status != v.TEMPLATE_PUBLISHED:
        raise HTTPException(status_code=422, detail={"code": "NOT_PUBLISHED", "message": "Cases use a published template."})
    case = assembly.open_case(db, template, anchor_kind=v.ASSEMBLY_MANUAL, title=body.title.strip() or "Case",
                              actor_user_id=context.user_id)
    assembly.evaluate(db, case=case)
    db.commit()
    return _detail(db, case)


@router.get("/workspaces/{workspace_id}/cases/{case_id}", response_model=CaseDetail)
def get_case(workspace_id: uuid.UUID, case_id: uuid.UUID, db: Session = Depends(get_db),
             context: TenantContext = Depends(RequireViewer)) -> CaseDetail:
    _ws(context, workspace_id)
    _gate(db, context, "cases.read")
    return _detail(db, _case(db, workspace_id, case_id))


@router.post("/workspaces/{workspace_id}/cases/{case_id}/documents", response_model=CaseDetail)
def add_case_document(workspace_id: uuid.UUID, case_id: uuid.UUID, body: CaseDocumentAdd, db: Session = Depends(get_db),
                      context: TenantContext = Depends(RequireContributor)) -> CaseDetail:
    _ws(context, workspace_id)
    _gate(db, context, "cases.documents.add")
    case = _case(db, workspace_id, case_id)
    item = db.execute(select(WorkItem).where(WorkItem.id == body.work_item_id, WorkItem.workspace_id == workspace_id)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    doc_type = doc_types.normalise(body.document_type) if body.document_type else doc_types.document_type_of(db, item)
    if not doc_type:
        raise HTTPException(status_code=422, detail={"code": "DOC_TYPE_UNKNOWN", "message": "Say which document type this is."})
    assembly.add_document(db, case=case, work_item_id=item.id, document_type=doc_type, source=v.SOURCE_MANUAL,
                          actor_user_id=context.user_id)
    assembly.evaluate(db, case=case)
    db.commit()
    return _detail(db, case)


@router.delete("/workspaces/{workspace_id}/cases/{case_id}/documents/{work_item_id}", response_model=CaseDetail)
def remove_case_document(workspace_id: uuid.UUID, case_id: uuid.UUID, work_item_id: uuid.UUID, db: Session = Depends(get_db),
                         context: TenantContext = Depends(RequireContributor)) -> CaseDetail:
    _ws(context, workspace_id)
    _gate(db, context, "cases.documents.remove")
    case = _case(db, workspace_id, case_id)
    db.execute(CaseDocument.__table__.delete().where(CaseDocument.case_id == case.id, CaseDocument.work_item_id == work_item_id))
    assembly.evaluate(db, case=case)
    db.commit()
    return _detail(db, case)


@router.post("/workspaces/{workspace_id}/cases/{case_id}/evaluate", response_model=CaseDetail)
def evaluate_case(workspace_id: uuid.UUID, case_id: uuid.UUID, db: Session = Depends(get_db),
                  context: TenantContext = Depends(RequireContributor)) -> CaseDetail:
    _ws(context, workspace_id)
    _gate(db, context, "cases.evaluate")
    case = _case(db, workspace_id, case_id)
    assembly.evaluate(db, case=case)
    db.commit()
    return _detail(db, case)


@router.post("/workspaces/{workspace_id}/cases/{case_id}/close", response_model=CaseDetail)
def close_case(workspace_id: uuid.UUID, case_id: uuid.UUID, db: Session = Depends(get_db),
               context: TenantContext = Depends(RequireContributor)) -> CaseDetail:
    _ws(context, workspace_id)
    _gate(db, context, "cases.close")
    case = assembly.close(db, case=_case(db, workspace_id, case_id))
    db.commit()
    return _detail(db, case)


@router.post("/workspaces/{workspace_id}/cases/{case_id}/requests", response_model=RequestCreated, status_code=201)
def create_document_request(workspace_id: uuid.UUID, case_id: uuid.UUID, body: RequestCreate, db: Session = Depends(get_db),
                            context: TenantContext = Depends(RequireContributor)) -> RequestCreated:
    _ws(context, workspace_id)
    _gate(db, context, "cases.requests.create")
    case = _case(db, workspace_id, case_id)
    doc_type = doc_types.normalise(body.document_type)
    if not doc_type:
        raise HTTPException(status_code=422, detail={"code": "DOC_TYPE_UNKNOWN", "message": "document_type is required."})
    try:
        request, token = requests.create(db, case=case, document_type=doc_type, recipient_label=body.recipient_label,
                                         actor_user_id=context.user_id, ttl_hours=body.ttl_hours)
    except requests.RequestError as exc:
        raise _422(exc) from exc
    db.commit()
    return RequestCreated(request=_request_row(request), token=token, upload_path=f"/request/{token}")


@router.post("/workspaces/{workspace_id}/cases/{case_id}/requests/{request_id}/revoke", response_model=RequestRow)
def revoke_document_request(workspace_id: uuid.UUID, case_id: uuid.UUID, request_id: uuid.UUID, db: Session = Depends(get_db),
                            context: TenantContext = Depends(RequireContributor)) -> RequestRow:
    _ws(context, workspace_id)
    _gate(db, context, "cases.requests.revoke")
    request = db.execute(select(DocumentRequest).where(DocumentRequest.id == request_id, DocumentRequest.case_id == case_id,
                                                       DocumentRequest.workspace_id == workspace_id)).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="Request not found.")
    try:
        requests.revoke(db, request=request)
    except requests.RequestError as exc:
        raise _422(exc) from exc
    db.commit()
    return _request_row(request)


__all__ = ["router"]
