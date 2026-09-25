"""ARCH44-S1:api — the Complex Table & Hierarchical Grid Extractor. Every route
is gated on capability.table_intelligence (402 CAPABILITY_REQUIRED without it);
reads need VIEWER, corrections and extraction CONTRIBUTOR."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.tables import ExtractedTable, ExtractedTableCell, TableValidation
from app.models.work_item import WorkItem
from app.models.workspace import WorkspaceRole
from app.schemas.tables import (
    CellCorrection,
    ColumnRoleRequest,
    DocumentTables,
    ExtractResult,
    LearnedMapping,
    TableCell,
    TableColumn,
    TableDetail,
    TableList,
    TableReviewRequest,
    TableRowInfo,
    TableSummary,
    TableValidationRow,
)
from app.services import audit_service, job_service
from app.services.tables import export, memory, service
from app.services.tables import vocabulary as v
from app.services.tables.values import format_number

router = APIRouter(tags=["Table Intelligence"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
CAPABILITY = entitlements.TABLE_INTELLIGENCE_CAPABILITY


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(db, context=context, capability_key=CAPABILITY, operation=operation)


def _ws(context: TenantContext, workspace_id: uuid.UUID) -> None:
    if context.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")


def _table_or_404(db: Session, workspace_id: uuid.UUID, table_id: uuid.UUID) -> ExtractedTable:
    table = db.execute(select(ExtractedTable).where(ExtractedTable.id == table_id,
                                                    ExtractedTable.workspace_id == workspace_id)).scalar_one_or_none()
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found.")
    return table


def _item_or_404(db: Session, workspace_id: uuid.UUID, work_item_id: uuid.UUID) -> WorkItem:
    item = db.execute(select(WorkItem).where(WorkItem.id == work_item_id,
                                             WorkItem.workspace_id == workspace_id)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return item


def _unprocessable(exc: service.TableError) -> HTTPException:
    code = status.HTTP_409_CONFLICT if exc.code in ("KEPT_CORRECTIONS", "ALREADY_DECIDED") else \
        status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(status_code=code, detail={"code": exc.code, "message": str(exc)})


def _summary(t: ExtractedTable, filename: str) -> TableSummary:
    return TableSummary(id=t.id, work_item_id=t.work_item_id, original_filename=filename, ordinal=t.ordinal,
                        title=t.title, page_start=t.page_start, page_end=t.page_end, n_rows=t.n_rows, n_cols=t.n_cols,
                        header_rows=t.header_rows, method=t.method, rotation=t.rotation,
                        skew_degrees=float(t.skew_degrees), confidence=float(t.confidence), status=t.status,
                        failed_checks=t.failed_checks, checked_relations=t.checked_relations, layout_key=t.layout_key,
                        revision=t.revision, created_at=t.created_at, corrected_at=t.corrected_at,
                        reviewed_at=t.reviewed_at)


def _plain(value: Optional[object]) -> Optional[str]:
    """A stored numeric(24,6) as its shortest exact decimal text."""
    if value is None:
        return None
    from decimal import Decimal

    d = Decimal(str(value)).normalize()
    return format_number(d.quantize(Decimal(1)) if d == d.to_integral() else d)


def _filename(db: Session, work_item_id: uuid.UUID) -> str:
    return db.execute(select(WorkItem.original_filename).where(WorkItem.id == work_item_id)).scalar_one()


def _detail(db: Session, t: ExtractedTable) -> TableDetail:
    cells = db.execute(select(ExtractedTableCell).where(ExtractedTableCell.table_id == t.id)
                       .order_by(ExtractedTableCell.row_index, ExtractedTableCell.col_index)).scalars()
    checks = db.execute(select(TableValidation).where(TableValidation.table_id == t.id)
                        .order_by(TableValidation.scope.desc(), TableValidation.row_index)).scalars()
    applied = memory.learned_for(db, t.workspace_id, t.layout_key)
    return TableDetail(
        table=_summary(t, _filename(db, t.work_item_id)),
        columns=[TableColumn(index=c["index"], path=c.get("path") or [], key=c["key"], header_key=c.get("header_key", ""),
                             value_type=c["type"], date_order=c.get("date_order", "DMY"), role=c["role"],
                             role_source=c.get("role_source", v.ROLE_SOURCE_INFERRED)) for c in t.columns],
        rows=[TableRowInfo(index=r["index"], kind=r["kind"], level=r.get("level", 0), page=r.get("page", t.page_start),
                           parent=r.get("parent")) for r in t.rows],
        cells=[TableCell(row=c.row_index, col=c.col_index, row_span=c.row_span, col_span=c.col_span, page=c.page,
                         text=c.text, value_type=c.value_type,
                         value_number=_plain(c.value_number),
                         value_date=c.value_date, confidence=float(c.confidence), base_confidence=float(c.base_confidence),
                         flags=list(c.flags or []), original_text=c.original_text, bbox=c.bbox) for c in cells],
        validations=[TableValidationRow(kind=x.kind, scope=x.scope, outcome=x.outcome, row_index=x.row_index,
                                        col_index=x.col_index,
                                        expected=_plain(x.expected), actual=_plain(x.actual),
                                        checked=x.checked, failed=x.failed, message=x.message, detail=x.detail or {})
                     for x in checks],
        learned_mappings=[LearnedMapping(header_key=m.header_key, role=m.role, confirmations=m.confirmations,
                                         contradictions=m.contradictions, applied=applied.get(m.header_key) == m.role)
                          for m in memory.mappings_for(db, t.workspace_id, t.layout_key)])


def _export_response(data: bytes, fmt: str, name: str) -> Response:
    return Response(content=data, media_type=export.CONTENT_TYPES[fmt],
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


def _audit_export(db: Session, context: TenantContext, **details: object) -> None:
    audit_service.record(db, organization_id=context.organization_id, workspace_id=context.workspace_id,
                         actor_id=context.user_id, resource_type=AuditResourceType.WORKSPACE,
                         resource_id=context.workspace_id, action=AuditAction.EXPORTED, outcome=AuditOutcome.ALLOWED,
                         details={"table_intelligence": {"operation": "export", **details}})


@router.get("/workspaces/{workspace_id}/tables", response_model=TableList)
def list_tables(workspace_id: uuid.UUID, status_filter: Optional[str] = Query(default=None, alias="status"),
                limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
                db: Session = Depends(get_db), context: TenantContext = Depends(RequireViewer)) -> TableList:
    _ws(context, workspace_id)
    _gate(db, context, "tables.list")
    query = select(ExtractedTable, WorkItem.original_filename).join(WorkItem, WorkItem.id == ExtractedTable.work_item_id) \
        .where(ExtractedTable.workspace_id == workspace_id)
    count = select(func.count()).select_from(ExtractedTable).where(ExtractedTable.workspace_id == workspace_id)
    if status_filter:
        wanted = status_filter.strip().upper()
        query, count = query.where(ExtractedTable.status == wanted), count.where(ExtractedTable.status == wanted)
    rows = db.execute(query.order_by(ExtractedTable.created_at.desc(), ExtractedTable.ordinal).limit(limit).offset(offset)).all()
    return TableList(items=[_summary(t, name) for t, name in rows], total=int(db.execute(count).scalar_one()),
                     counts_by_status=service.workspace_counts(db, workspace_id))


@router.get("/workspaces/{workspace_id}/tables/{table_id}", response_model=TableDetail)
def get_table(workspace_id: uuid.UUID, table_id: uuid.UUID, db: Session = Depends(get_db),
              context: TenantContext = Depends(RequireViewer)) -> TableDetail:
    _ws(context, workspace_id)
    _gate(db, context, "tables.read")
    return _detail(db, _table_or_404(db, workspace_id, table_id))


@router.get("/workspaces/{workspace_id}/tables/{table_id}/export")
def export_table(workspace_id: uuid.UUID, table_id: uuid.UUID, fmt: str = Query(default="csv", alias="format"),
                 db: Session = Depends(get_db), context: TenantContext = Depends(RequireViewer)) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "tables.export")
    fmt = fmt.strip().lower()
    if fmt not in export.FORMATS:
        raise HTTPException(status_code=422, detail={"code": "UNKNOWN_FORMAT", "message": "format is csv, xlsx or json."})
    table = _table_or_404(db, workspace_id, table_id)
    out = service.load(db, table)
    name = _filename(db, table.work_item_id)
    data = {"csv": lambda: export.to_csv(out), "xlsx": lambda: export.to_xlsx([out]),
            "json": lambda: export.to_json(out, meta={"table_id": str(table.id), "work_item_id": str(table.work_item_id),
                                                       "original_filename": name})}[fmt]()
    _audit_export(db, context, table_id=str(table.id), format=fmt, bytes=len(data))
    db.commit()
    return _export_response(data, fmt, export.filename(name, out, fmt))


@router.patch("/workspaces/{workspace_id}/tables/{table_id}/cells", response_model=TableDetail)
def correct_table_cells(workspace_id: uuid.UUID, table_id: uuid.UUID, body: CellCorrection, db: Session = Depends(get_db),
                        context: TenantContext = Depends(RequireContributor)) -> TableDetail:
    _ws(context, workspace_id)
    _gate(db, context, "tables.correct")
    table = _table_or_404(db, workspace_id, table_id)
    try:
        service.correct_cells(db, table=table, edits=[e.model_dump() for e in body.cells], actor_user_id=context.user_id)
    except service.TableError as exc:
        raise _unprocessable(exc) from exc
    db.commit()
    return _detail(db, table)


@router.put("/workspaces/{workspace_id}/tables/{table_id}/columns/{col}/role", response_model=TableDetail)
def set_table_column_role(workspace_id: uuid.UUID, table_id: uuid.UUID, col: int, body: ColumnRoleRequest,
                          db: Session = Depends(get_db), context: TenantContext = Depends(RequireContributor)) -> TableDetail:
    _ws(context, workspace_id)
    _gate(db, context, "tables.column_role")
    table = _table_or_404(db, workspace_id, table_id)
    try:
        service.set_column_role(db, table=table, col=col, role=body.role, actor_user_id=context.user_id)
    except service.TableError as exc:
        raise _unprocessable(exc) from exc
    db.commit()
    return _detail(db, table)


@router.post("/workspaces/{workspace_id}/tables/{table_id}/review", response_model=TableDetail)
def review_table(workspace_id: uuid.UUID, table_id: uuid.UUID, body: TableReviewRequest, db: Session = Depends(get_db),
                 context: TenantContext = Depends(RequireContributor)) -> TableDetail:
    _ws(context, workspace_id)
    _gate(db, context, "tables.review")
    table = _table_or_404(db, workspace_id, table_id)
    try:
        service.review(db, table=table, verdict=body.verdict, actor_user_id=context.user_id)
    except service.TableError as exc:
        raise _unprocessable(exc) from exc
    db.commit()
    return _detail(db, table)


@router.get("/workspaces/{workspace_id}/work-items/{work_item_id}/tables", response_model=DocumentTables)
def document_tables(workspace_id: uuid.UUID, work_item_id: uuid.UUID, db: Session = Depends(get_db),
                    context: TenantContext = Depends(RequireViewer)) -> DocumentTables:
    _ws(context, workspace_id)
    _gate(db, context, "tables.document")
    item = _item_or_404(db, workspace_id, work_item_id)
    mime = (item.file_type or "").split(";")[0].strip().lower()
    return DocumentTables(work_item_id=item.id, original_filename=item.original_filename, page_count=item.page_count,
                          extractable=mime in v.EXTRACTABLE_MIME,
                          tables=[_summary(t, item.original_filename) for t in service.tables_of(db, item.id)])


@router.post("/workspaces/{workspace_id}/work-items/{work_item_id}/tables/extract", response_model=ExtractResult)
def extract_document_tables(workspace_id: uuid.UUID, work_item_id: uuid.UUID, response: Response,
                            force: bool = Query(default=False), db: Session = Depends(get_db),
                            context: TenantContext = Depends(RequireContributor)) -> ExtractResult:
    _ws(context, workspace_id)
    _gate(db, context, "tables.extract")
    item = _item_or_404(db, workspace_id, work_item_id)
    if (item.page_count or 0) > v.MAX_SYNC_PAGES:
        job_service.enqueue(db, job_type=v.JOB_EXTRACT, organization_id=context.organization_id,
                            payload={"work_item_id": str(item.id), "force": force},
                            idempotency_key=f"{v.JOB_EXTRACT}:{item.id}:manual:{uuid.uuid4().hex[:12]}")
        db.commit()
        response.status_code = status.HTTP_202_ACCEPTED
        return ExtractResult(ran="QUEUED", tables=[_summary(t, item.original_filename) for t in service.tables_of(db, item.id)])
    try:
        result = service.extract_document(db, work_item=item, force=force, actor_user_id=context.user_id)
    except service.TableError as exc:
        raise _unprocessable(exc) from exc
    db.commit()
    return ExtractResult(ran="SYNC" if result.get("extracted") else "SKIPPED", reason=result.get("reason"),
                         tables=[_summary(t, item.original_filename) for t in service.tables_of(db, item.id)])


@router.get("/workspaces/{workspace_id}/work-items/{work_item_id}/tables/export")
def export_document_tables(workspace_id: uuid.UUID, work_item_id: uuid.UUID,
                           fmt: str = Query(default="xlsx", alias="format"), db: Session = Depends(get_db),
                           context: TenantContext = Depends(RequireViewer)) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "tables.export_document")
    fmt = fmt.strip().lower()
    if fmt not in ("xlsx", "json"):
        raise HTTPException(status_code=422, detail={"code": "UNKNOWN_FORMAT", "message": "format is xlsx or json."})
    item = _item_or_404(db, workspace_id, work_item_id)
    tables = service.tables_of(db, item.id)
    if not tables:
        raise HTTPException(status_code=404, detail={"code": "NO_TABLES", "message": "No tables were found in this document."})
    outs = [service.load(db, t) for t in tables]
    if fmt == "xlsx":
        data = export.to_xlsx(outs)
    else:
        import json as _json

        data = _json.dumps({"work_item_id": str(item.id), "original_filename": item.original_filename,
                            "tables": [export.to_dict(o, meta={"table_id": str(t.id)}) for o, t in zip(outs, tables)]},
                           ensure_ascii=False, indent=2).encode("utf-8")
    _audit_export(db, context, work_item_id=str(item.id), format=fmt, tables=len(tables), bytes=len(data))
    db.commit()
    return _export_response(data, fmt, export.filename(item.original_filename, None, fmt))


__all__ = ["router"]
