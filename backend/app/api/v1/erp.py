"""ARCH47-S1:api — ERP & System-of-Record Posting. Every route is gated on capability.erp_posting as its second
line (402 CAPABILITY_REQUIRED without it). Reads need VIEWER; posting, retrying, accepting, cancelling,
acknowledging, previewing and downloading a posting's file CONTRIBUTOR; targets, credentials, mappings and lookup
tables ADMIN. Credentials are write-only: no route returns one (only its fingerprint)."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.audit_log import AuditAction
from app.models.erp import ErpLookupTable, ErpMapping, ErpPosting, ErpPostingAttempt, ErpTarget
from app.models.work_item import WorkItem
from app.models.workspace import WorkspaceRole
from app.schemas.erp import (
    AcknowledgeRequest,
    AttemptRow,
    CredentialSet,
    ErpCatalog,
    FormatInfo,
    LookupCreate,
    LookupList,
    LookupRow,
    LookupUpdate,
    MappingCheck,
    MappingRow,
    MappingSave,
    MappingVersions,
    OutcomeList,
    OutcomeRow,
    PostingDetail,
    PostingList,
    PostingRow,
    PostRequest,
    PostResponse,
    PostResult,
    PresetInfo,
    PreviewRequest,
    PreviewResponse,
    ReviewAction,
    TargetCreate,
    TargetDetail,
    TargetList,
    TargetRow,
    TargetUpdate,
    TestResult,
)
from app.services.erp import canonical as C
from app.services.erp import mapping as M
from app.services.erp import presets as PR
from app.services.erp import render as R
from app.services.erp import service
from app.services.erp import sources as S
from app.services.erp import vocabulary as v
from app.services.erp.formats import jsonapi

router = APIRouter(tags=["ERP posting"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
RequireAdmin = RequireWorkspaceRole(WorkspaceRole.ADMIN)
CAPABILITY = entitlements.ERP_POSTING_CAPABILITY
_TEXT_TYPES = ("text/csv", "application/edi-x12", "application/xml", "application/json")
_PREVIEW_BYTES = 65536


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(db, context=context, capability_key=CAPABILITY, operation=operation)


def _ws(context: TenantContext, workspace_id: uuid.UUID) -> None:
    if context.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")


_CODES = {"NOT_FOUND": 404, "NAME_TAKEN": 409, "HAS_POSTINGS": 409, "IN_USE": 409, "NOT_DELIVERED": 409,
          "NOT_IN_EXCEPTION": 409, "NOT_CANCELLABLE": 409, "TARGET_DISABLED": 409, "NOT_APPROVED": 409,
          "ERASED": 409, "NOT_OURS": 409, "TOO_MANY_TARGETS": 409, "TOO_MANY": 409, "URL_REFUSED": 422}


def _error(exc: service.ErpError) -> HTTPException:
    return HTTPException(status_code=_CODES.get(exc.code, status.HTTP_422_UNPROCESSABLE_ENTITY),
                         detail={"code": exc.code, "message": str(exc), "problems": exc.problems[:50]})


def _target(db: Session, workspace_id: uuid.UUID, target_id: uuid.UUID) -> ErpTarget:
    row = db.execute(select(ErpTarget).where(ErpTarget.id == target_id,
                                             ErpTarget.workspace_id == workspace_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ERP target not found.")
    return row


def _posting(db: Session, workspace_id: uuid.UUID, posting_id: uuid.UUID) -> ErpPosting:
    row = db.execute(select(ErpPosting).where(ErpPosting.id == posting_id,
                                              ErpPosting.workspace_id == workspace_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Posting not found.")
    return row


def _lookup(db: Session, workspace_id: uuid.UUID, table_id: uuid.UUID) -> ErpLookupTable:
    row = db.execute(select(ErpLookupTable).where(ErpLookupTable.id == table_id,
                                                  ErpLookupTable.workspace_id == workspace_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lookup table not found.")
    return row


def _kind(value: str, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise HTTPException(status_code=422, detail={"code": "INVALID", "message": f"unknown {what} {value!r}"})
    return value


def _target_row(db: Session, t: ErpTarget) -> TargetRow:
    from app.services.erp.service import _host

    counts = dict(db.execute(select(ErpPosting.state, func.count()).where(ErpPosting.target_id == t.id)
                             .group_by(ErpPosting.state)).all())
    return TargetRow(id=t.id, name=t.name, format=t.format, transport=t.transport, preset=t.preset, ack_mode=t.ack_mode,
                     auth_mode=t.auth_mode, status=t.status, host=_host(t.config or {}, t.transport),
                     credential_set=t.credential_ciphertext is not None, credential_fingerprint=t.credential_fingerprint,
                     credential_updated_at=t.credential_updated_at, auto_post=t.auto_post,
                     auto_post_since=t.auto_post_since, auto_sources=list(t.auto_sources or []),
                     auto_objects=list(t.auto_objects or []), max_attempts=int(t.max_attempts),
                     ack_timeout_hours=int(t.ack_timeout_hours), objects=list(PR.supported_objects(t.format, t.preset)),
                     lookup_prefix=(t.config or {}).get("lookup_prefix"), postings=sum(counts.values()),
                     open_exceptions=sum(n for s_, n in counts.items() if s_ in v.EXCEPTION_STATES),
                     created_at=t.created_at, updated_at=t.updated_at)


def _mapping_row(m: ErpMapping) -> MappingRow:
    return MappingRow(id=m.id, object_kind=m.object_kind, version=int(m.version), status=m.status, spec=dict(m.spec),
                      spec_sha=m.spec_sha, note=m.note, created_at=m.created_at, retired_at=m.retired_at)


def _posting_rows(db: Session, rows: list[ErpPosting]) -> list[PostingRow]:
    targets = {t.id: t for t in db.execute(select(ErpTarget).where(
        ErpTarget.id.in_({p.target_id for p in rows}))).scalars()} if rows else {}
    docs = {p.work_item_id for p in rows if p.work_item_id}
    names = dict(db.execute(select(WorkItem.id, WorkItem.original_filename).where(WorkItem.id.in_(docs))).all()) \
        if docs else {}
    out = []
    for p in rows:
        t = targets.get(p.target_id)
        out.append(PostingRow(
            id=p.id, target_id=p.target_id, target_name=t.name if t else "", target_format=t.format if t else "",
            target_preset=t.preset if t else "", object_kind=p.object_kind, source_kind=p.source_kind,
            source_id=p.source_id, work_item_id=p.work_item_id, work_item_filename=names.get(p.work_item_id),
            origin=p.origin, state=p.state, document_number=p.document_number,
            amount=None if p.amount is None else format(C.money(p.amount, p.currency), "f"), currency=p.currency,
            attempts=int(p.attempts), max_attempts=int(p.max_attempts), next_attempt_at=p.next_attempt_at,
            external_id=p.external_id, last_error=p.last_error, mapping_version=p.mapping_version,
            rendered_filename=p.rendered_filename, content_sha=p.content_sha, delivered_at=p.delivered_at,
            acknowledged_at=p.acknowledged_at, reviewed_at=p.reviewed_at, erased=p.erased_at is not None,
            created_at=p.created_at, updated_at=p.updated_at))
    return out


def _preview(data: Optional[bytes], media_type: Optional[str]) -> Optional[str]:
    if not data or media_type not in _TEXT_TYPES:
        return None
    return data[:_PREVIEW_BYTES].decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/erp/catalog", response_model=ErpCatalog)
def erp_catalog(workspace_id: uuid.UUID, db: Session = Depends(get_db),
                context: TenantContext = Depends(RequireViewer)) -> ErpCatalog:
    _ws(context, workspace_id)
    _gate(db, context, "erp.catalog")
    formats = [FormatInfo(key=f, label=v.FORMAT_LABELS[f],
                          transports=[v.TRANSPORT_HTTP] if f == v.FORMAT_JSON else [v.TRANSPORT_DOWNLOAD, v.TRANSPORT_SFTP],
                          objects=list(PR.supported_objects(f, v.PRESET_NONE)) if f != v.FORMAT_JSON else [])
               for f in v.FORMATS]
    presets = []
    for key in v.JSON_PRESETS:
        p = jsonapi.preset(key)
        presets.append(PresetInfo(key=key, label=p.label, auth_modes=list(p.auth), config_keys=list(p.config_keys),
                                  objects=list(p.endpoints),
                                  idempotency=p.idempotency_query or p.idempotency_header,
                                  probe_objects=[k for k, ep in p.endpoints.items() if ep.probe]))
    return ErpCatalog(formats=formats, transports=list(v.TRANSPORTS), presets=presets,
                      ack_modes={k: list(val) for k, val in v.ACK_BY_TRANSPORT.items()},
                      auth_modes={k: list(val) for k, val in v.AUTH_BY_TRANSPORT.items()},
                      object_kinds=list(v.OBJECT_KINDS), object_labels=dict(v.OBJECT_LABELS),
                      source_kinds=list(v.SOURCE_KINDS), source_labels=dict(v.SOURCE_LABELS), states=list(v.STATES),
                      state_labels=dict(v.STATE_LABELS), transforms=list(M.TRANSFORMS), header_paths=dict(C.HEADER_PATHS),
                      line_paths=dict(C.LINE_PATHS), language=M.LANGUAGE)


# ---------------------------------------------------------------------------
# targets
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/erp/targets", response_model=TargetList)
def list_targets(workspace_id: uuid.UUID, db: Session = Depends(get_db),
                 context: TenantContext = Depends(RequireViewer)) -> TargetList:
    _ws(context, workspace_id)
    _gate(db, context, "erp.targets.list")
    rows = db.execute(select(ErpTarget).where(ErpTarget.workspace_id == workspace_id).order_by(ErpTarget.name)).scalars()
    return TargetList(items=[_target_row(db, t) for t in rows])


@router.post("/workspaces/{workspace_id}/erp/targets", response_model=TargetDetail, status_code=201)
def create_target(workspace_id: uuid.UUID, body: TargetCreate, db: Session = Depends(get_db),
                  context: TenantContext = Depends(RequireAdmin)) -> TargetDetail:
    _ws(context, workspace_id)
    _gate(db, context, "erp.targets.create")
    try:
        t = service.create_target(
            db, organization_id=context.organization_id, workspace_id=workspace_id, actor_user_id=context.user_id,
            name=body.name, format=body.format, transport=body.transport, preset=body.preset, ack_mode=body.ack_mode,
            auth_mode=body.auth_mode, config=body.config, credential=body.credential, lookup_prefix=body.lookup_prefix,
            auto_post=body.auto_post, auto_sources=body.auto_sources, auto_objects=body.auto_objects,
            max_attempts=body.max_attempts, ack_timeout_hours=body.ack_timeout_hours)
    except service.ErpError as exc:
        raise _error(exc) from exc
    db.commit()
    return _detail(db, t)


def _detail(db: Session, t: ErpTarget) -> TargetDetail:
    mappings = db.execute(select(ErpMapping).where(ErpMapping.target_id == t.id, ErpMapping.status == v.MAPPING_ACTIVE)
                          .order_by(ErpMapping.object_kind)).scalars()
    return TargetDetail(target=_target_row(db, t), config=dict(t.config or {}),
                        mappings=[_mapping_row(m) for m in mappings],
                        # the TARGET's own tables (<prefix>_vendors, _accounts, _items, _settings)
                        lookup_tables=sorted(PR.table_names((t.config or {}).get("lookup_prefix") or "erp").values()))


@router.get("/workspaces/{workspace_id}/erp/targets/{target_id}", response_model=TargetDetail)
def get_target(workspace_id: uuid.UUID, target_id: uuid.UUID, db: Session = Depends(get_db),
               context: TenantContext = Depends(RequireViewer)) -> TargetDetail:
    _ws(context, workspace_id)
    _gate(db, context, "erp.targets.get")
    return _detail(db, _target(db, workspace_id, target_id))


@router.patch("/workspaces/{workspace_id}/erp/targets/{target_id}", response_model=TargetDetail)
def update_target(workspace_id: uuid.UUID, target_id: uuid.UUID, body: TargetUpdate, db: Session = Depends(get_db),
                  context: TenantContext = Depends(RequireAdmin)) -> TargetDetail:
    _ws(context, workspace_id)
    _gate(db, context, "erp.targets.update")
    t = _target(db, workspace_id, target_id)
    try:
        service.update_target(db, target=t, actor_user_id=context.user_id,
                              changes=body.model_dump(exclude_unset=True))
    except service.ErpError as exc:
        raise _error(exc) from exc
    db.commit()
    return _detail(db, t)


@router.delete("/workspaces/{workspace_id}/erp/targets/{target_id}", status_code=204)
def delete_target(workspace_id: uuid.UUID, target_id: uuid.UUID, db: Session = Depends(get_db),
                  context: TenantContext = Depends(RequireAdmin)) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "erp.targets.delete")
    t = _target(db, workspace_id, target_id)
    try:
        service.delete_target(db, target=t, actor_user_id=context.user_id)
    except service.ErpError as exc:
        raise _error(exc) from exc
    db.commit()
    return Response(status_code=204)


@router.put("/workspaces/{workspace_id}/erp/targets/{target_id}/credential", response_model=TargetRow)
def set_credential(workspace_id: uuid.UUID, target_id: uuid.UUID, body: CredentialSet, db: Session = Depends(get_db),
                   context: TenantContext = Depends(RequireAdmin)) -> TargetRow:
    _ws(context, workspace_id)
    _gate(db, context, "erp.targets.credential")
    t = _target(db, workspace_id, target_id)
    try:
        service.set_credential(db, target=t, credential=body.credential, actor_user_id=context.user_id)
    except service.ErpError as exc:
        raise _error(exc) from exc
    db.commit()
    return _target_row(db, t)


@router.post("/workspaces/{workspace_id}/erp/targets/{target_id}/test", response_model=TestResult)
def test_target(workspace_id: uuid.UUID, target_id: uuid.UUID, db: Session = Depends(get_db),
                context: TenantContext = Depends(RequireAdmin)) -> TestResult:
    _ws(context, workspace_id)
    _gate(db, context, "erp.targets.test")
    t = _target(db, workspace_id, target_id)
    result = service.test_target(db, target=t, actor_user_id=context.user_id)
    db.commit()
    return TestResult(**result)


# ---------------------------------------------------------------------------
# mappings
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/erp/targets/{target_id}/mappings/{object_kind}", response_model=MappingVersions)
def get_mappings(workspace_id: uuid.UUID, target_id: uuid.UUID, object_kind: str, db: Session = Depends(get_db),
                 context: TenantContext = Depends(RequireViewer)) -> MappingVersions:
    _ws(context, workspace_id)
    _gate(db, context, "erp.mappings.get")
    t = _target(db, workspace_id, target_id)
    _kind(object_kind, PR.supported_objects(t.format, t.preset), "object kind for this target")
    rows = list(db.execute(select(ErpMapping).where(ErpMapping.target_id == t.id, ErpMapping.object_kind == object_kind)
                           .order_by(ErpMapping.version.desc())).scalars())
    active = next((m for m in rows if m.status == v.MAPPING_ACTIVE), None)
    return MappingVersions(target_id=t.id, object_kind=object_kind, active=_mapping_row(active) if active else None,
                           versions=[_mapping_row(m) for m in rows],
                           default_spec=PR.default_mapping(t.format, t.preset, object_kind,
                                                           (t.config or {}).get("lookup_prefix")))


@router.put("/workspaces/{workspace_id}/erp/targets/{target_id}/mappings/{object_kind}", response_model=MappingRow)
def save_mapping(workspace_id: uuid.UUID, target_id: uuid.UUID, object_kind: str, body: MappingSave,
                 db: Session = Depends(get_db), context: TenantContext = Depends(RequireAdmin)) -> MappingRow:
    _ws(context, workspace_id)
    _gate(db, context, "erp.mappings.save")
    t = _target(db, workspace_id, target_id)
    _kind(object_kind, v.OBJECT_KINDS, "object kind")
    try:
        row = service.save_mapping(db, target=t, object_kind=object_kind, spec=body.spec, note=body.note,
                                   actor_user_id=context.user_id)
    except service.ErpError as exc:
        raise _error(exc) from exc
    db.commit()
    return _mapping_row(row)


@router.post("/workspaces/{workspace_id}/erp/targets/{target_id}/mappings/{object_kind}/validate",
             response_model=MappingCheck)
def validate_mapping(workspace_id: uuid.UUID, target_id: uuid.UUID, object_kind: str, body: MappingSave,
                     db: Session = Depends(get_db), context: TenantContext = Depends(RequireAdmin)) -> MappingCheck:
    _ws(context, workspace_id)
    _gate(db, context, "erp.mappings.validate")
    t = _target(db, workspace_id, target_id)
    _kind(object_kind, v.OBJECT_KINDS, "object kind")
    try:
        service.validate_mapping(db, t, object_kind, body.spec)
    except service.ErpError as exc:
        return MappingCheck(valid=False, problems=exc.problems or [str(exc)])
    return MappingCheck(valid=True, problems=[])


@router.post("/workspaces/{workspace_id}/erp/targets/{target_id}/mappings/{object_kind}/default",
             response_model=MappingRow)
def restore_default_mapping(workspace_id: uuid.UUID, target_id: uuid.UUID, object_kind: str,
                            db: Session = Depends(get_db), context: TenantContext = Depends(RequireAdmin)) -> MappingRow:
    _ws(context, workspace_id)
    _gate(db, context, "erp.mappings.default")
    t = _target(db, workspace_id, target_id)
    _kind(object_kind, PR.supported_objects(t.format, t.preset), "object kind for this target")
    try:
        row = service.restore_default_mapping(db, target=t, object_kind=object_kind, actor_user_id=context.user_id)
    except service.ErpError as exc:
        raise _error(exc) from exc
    db.commit()
    return _mapping_row(row)


# ---------------------------------------------------------------------------
# lookup tables
# ---------------------------------------------------------------------------


def _lookup_row(db: Session, t: ErpLookupTable) -> LookupRow:
    used = [f"{name} {kind.lower()}" for name, kind, spec in db.execute(
        select(ErpTarget.name, ErpMapping.object_kind, ErpMapping.spec).join(ErpTarget, ErpTarget.id == ErpMapping.target_id)
        .where(ErpMapping.workspace_id == t.workspace_id, ErpMapping.status == v.MAPPING_ACTIVE)).all()
        if f'"table": "{t.name}"' in __import__("json").dumps(spec)]
    return LookupRow(id=t.id, name=t.name, description=t.description, entries=dict(t.entries or {}),
                     entry_count=len(t.entries or {}), revision=int(t.revision), used_by=used, updated_at=t.updated_at)


@router.get("/workspaces/{workspace_id}/erp/lookups", response_model=LookupList)
def list_lookups(workspace_id: uuid.UUID, db: Session = Depends(get_db),
                 context: TenantContext = Depends(RequireViewer)) -> LookupList:
    _ws(context, workspace_id)
    _gate(db, context, "erp.lookups.list")
    rows = db.execute(select(ErpLookupTable).where(ErpLookupTable.workspace_id == workspace_id)
                      .order_by(ErpLookupTable.name)).scalars()
    return LookupList(items=[_lookup_row(db, t) for t in rows])


@router.post("/workspaces/{workspace_id}/erp/lookups", response_model=LookupRow, status_code=201)
def create_lookup(workspace_id: uuid.UUID, body: LookupCreate, db: Session = Depends(get_db),
                  context: TenantContext = Depends(RequireAdmin)) -> LookupRow:
    _ws(context, workspace_id)
    _gate(db, context, "erp.lookups.create")
    try:
        row = service.create_lookup(db, organization_id=context.organization_id, workspace_id=workspace_id,
                                    actor_user_id=context.user_id, name=body.name, description=body.description,
                                    entries=body.entries)
    except service.ErpError as exc:
        raise _error(exc) from exc
    db.commit()
    return _lookup_row(db, row)


@router.patch("/workspaces/{workspace_id}/erp/lookups/{table_id}", response_model=LookupRow)
def update_lookup(workspace_id: uuid.UUID, table_id: uuid.UUID, body: LookupUpdate, db: Session = Depends(get_db),
                  context: TenantContext = Depends(RequireAdmin)) -> LookupRow:
    _ws(context, workspace_id)
    _gate(db, context, "erp.lookups.update")
    row = _lookup(db, workspace_id, table_id)
    try:
        service.update_lookup(db, table=row, actor_user_id=context.user_id, description=body.description,
                              entries=body.entries, merge=body.merge)
    except service.ErpError as exc:
        raise _error(exc) from exc
    db.commit()
    return _lookup_row(db, row)


@router.delete("/workspaces/{workspace_id}/erp/lookups/{table_id}", status_code=204)
def delete_lookup(workspace_id: uuid.UUID, table_id: uuid.UUID, db: Session = Depends(get_db),
                  context: TenantContext = Depends(RequireAdmin)) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "erp.lookups.delete")
    row = _lookup(db, workspace_id, table_id)
    try:
        service.delete_lookup(db, table=row, actor_user_id=context.user_id)
    except service.ErpError as exc:
        raise _error(exc) from exc
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# outcomes and postings
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/erp/outcomes", response_model=OutcomeList)
def list_outcomes(workspace_id: uuid.UUID, limit: int = Query(default=100, ge=1, le=500),
                  db: Session = Depends(get_db), context: TenantContext = Depends(RequireViewer)) -> OutcomeList:
    _ws(context, workspace_id)
    _gate(db, context, "erp.outcomes.list")
    return OutcomeList(items=[OutcomeRow(**row) for row in service.outcomes_with_status(db, workspace_id, limit=limit)])


@router.get("/workspaces/{workspace_id}/erp/postings", response_model=PostingList)
def list_postings(workspace_id: uuid.UUID, state: Optional[str] = Query(default=None),
                  target_id: Optional[uuid.UUID] = Query(default=None), object_kind: Optional[str] = Query(default=None),
                  source_kind: Optional[str] = Query(default=None), source_id: Optional[uuid.UUID] = Query(default=None),
                  work_item_id: Optional[uuid.UUID] = Query(default=None),
                  q: Optional[str] = Query(default=None, max_length=100),
                  limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
                  db: Session = Depends(get_db), context: TenantContext = Depends(RequireViewer)) -> PostingList:
    _ws(context, workspace_id)
    _gate(db, context, "erp.postings.list")
    base = select(ErpPosting).where(ErpPosting.workspace_id == workspace_id)
    if target_id:
        base = base.where(ErpPosting.target_id == target_id)
    if object_kind:
        base = base.where(ErpPosting.object_kind == object_kind)
    if source_kind:
        base = base.where(ErpPosting.source_kind == source_kind)
    if source_id:
        base = base.where(ErpPosting.source_id == source_id)
    if work_item_id:
        base = base.where(ErpPosting.work_item_id == work_item_id)
    if q:
        like = f"%{q.replace('%', '').replace('_', '')}%"
        base = base.where(or_(ErpPosting.document_number.ilike(like), ErpPosting.external_id.ilike(like)))
    sub = base.subquery()
    counts = dict(db.execute(select(sub.c.state, func.count()).group_by(sub.c.state)).all())
    filtered = base
    if state:
        states = [s_ for s_ in state.split(",") if s_ in v.STATES]
        filtered = filtered.where(ErpPosting.state.in_(states))
    total = db.execute(select(func.count()).select_from(filtered.subquery())).scalar_one()
    rows = list(db.execute(filtered.order_by(ErpPosting.created_at.desc()).limit(limit).offset(offset)).scalars())
    return PostingList(items=_posting_rows(db, rows), total=int(total),
                       counts_by_state={s_: int(counts.get(s_, 0)) for s_ in v.STATES})


@router.post("/workspaces/{workspace_id}/erp/postings", response_model=PostResponse, status_code=201)
def create_postings(workspace_id: uuid.UUID, body: PostRequest, db: Session = Depends(get_db),
                    context: TenantContext = Depends(RequireContributor)) -> PostResponse:
    _ws(context, workspace_id)
    _gate(db, context, "erp.postings.create")
    t = _target(db, workspace_id, body.target_id)
    _kind(body.source_kind, v.SOURCE_KINDS, "source kind")
    results = []
    for kind in body.object_kinds:
        _kind(kind, v.OBJECT_KINDS, "object kind")
        try:
            with db.begin_nested():
                p, created = service.plan(db, target=t, source_kind=body.source_kind, source_id=body.source_id,
                                          object_kind=kind, origin=v.ORIGIN_MANUAL, actor_user_id=context.user_id)
            results.append(PostResult(object_kind=kind, posting_id=p.id, created=created, state=p.state))
        except service.ErpError as exc:
            if exc.code in ("NOT_FOUND",):
                raise _error(exc) from exc
            results.append(PostResult(object_kind=kind, created=False, error=str(exc)))
    db.commit()
    return PostResponse(results=results)


@router.post("/workspaces/{workspace_id}/erp/postings/preview", response_model=PreviewResponse)
def preview_posting(workspace_id: uuid.UUID, body: PreviewRequest, db: Session = Depends(get_db),
                    context: TenantContext = Depends(RequireContributor)) -> PreviewResponse:
    _ws(context, workspace_id)
    _gate(db, context, "erp.postings.preview")
    t = _target(db, workspace_id, body.target_id)
    _kind(body.source_kind, v.SOURCE_KINDS, "source kind")
    _kind(body.object_kind, v.OBJECT_KINDS, "object kind")
    try:
        spec = service.validate_mapping(db, t, body.object_kind, body.spec) if body.spec is not None else \
            (service.active_mapping(db, t, body.object_kind).spec if service.active_mapping(db, t, body.object_kind)
             else None)
        if spec is None:
            return PreviewResponse(ok=False, problems=["no mapping for this object kind"], notes=[])
        outcome = S.load(db, workspace_id, body.source_kind, body.source_id)
        obj = S.build(db, outcome, body.object_kind, today=service.local_today(db, workspace_id))
    except service.ErpError as exc:
        return PreviewResponse(ok=False, problems=exc.problems or [str(exc)], notes=[])
    except (S.SourceError, C.BuildError) as exc:
        return PreviewResponse(ok=False, problems=[str(exc)], notes=[])
    from app.services.erp.formats import x12

    key = service.idempotency_key(workspace_id, t.id, body.object_kind, body.source_kind, body.source_id)
    try:
        r = R.render(R.TargetView(t.format, t.preset, t.config), obj, spec, lookups=service.lookups(db, workspace_id),
                     extra={"id": "preview", "idempotency_key": key, "date": obj.posting_date,
                            "bill_external_id": service.bill_external_id(db, t.id, body.source_kind, body.source_id)},
                     posting_id="preview00", remote_id=service.remote_id(key), at=service.now(),
                     control=x12.ControlNumbers(int(t.control_sequence) + 1, int(t.control_sequence) + 1))
    except R.RenderError as exc:
        return PreviewResponse(ok=False, canonical=obj.as_json(), problems=exc.problems, notes=obj.notes)
    return PreviewResponse(ok=True, canonical=obj.as_json(), mapped=r.record.as_json(),
                           rendered_preview=_preview(r.data, r.media_type), rendered_media_type=r.media_type,
                           filename=r.filename, problems=[], notes=obj.notes)


@router.get("/workspaces/{workspace_id}/erp/postings/{posting_id}", response_model=PostingDetail)
def get_posting(workspace_id: uuid.UUID, posting_id: uuid.UUID, db: Session = Depends(get_db),
                context: TenantContext = Depends(RequireViewer)) -> PostingDetail:
    _ws(context, workspace_id)
    _gate(db, context, "erp.postings.get")
    p = _posting(db, workspace_id, posting_id)
    return _posting_detail(db, p)


def _posting_detail(db: Session, p: ErpPosting) -> PostingDetail:
    attempts = db.execute(select(ErpPostingAttempt).where(ErpPostingAttempt.posting_id == p.id)
                          .order_by(ErpPostingAttempt.seq)).scalars()
    label, changed = None, None
    try:
        outcome = S.load(db, p.workspace_id, p.source_kind, p.source_id)
        label = outcome.label
        if p.source_digest and p.erased_at is None and p.object_kind in outcome.objects:
            try:
                changed = S.build(db, outcome, p.object_kind, today=service.local_today(db, p.workspace_id)).digest() \
                    != p.source_digest
            except (S.SourceError, C.BuildError):
                changed = True
    except S.SourceError:
        changed = None
    return PostingDetail(
        posting=_posting_rows(db, [p])[0], canonical=p.canonical, mapped=p.mapped,
        rendered_preview=_preview(p.rendered, p.rendered_media_type), rendered_media_type=p.rendered_media_type,
        rendered_size=len(p.rendered) if p.rendered is not None else None, ack=dict(p.ack or {}),
        control_numbers=dict(p.control_numbers or {}), remote_path=p.remote_path, idempotency_key=p.idempotency_key,
        source_label=label, source_changed=changed,
        attempts=[AttemptRow(seq=a.seq, kind=a.kind, outcome=a.outcome, http_status=a.http_status, message=a.message,
                             detail=dict(a.detail or {}), actor_user_id=a.actor_user_id, started_at=a.started_at)
                  for a in attempts],
        review_note=p.review_note)


@router.get("/workspaces/{workspace_id}/erp/postings/{posting_id}/file")
def download_posting(workspace_id: uuid.UUID, posting_id: uuid.UUID, db: Session = Depends(get_db),
                     context: TenantContext = Depends(RequireContributor)) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "erp.postings.download")
    p = _posting(db, workspace_id, posting_id)
    if p.rendered is None:
        raise HTTPException(status_code=404, detail="This posting has no file (it was not rendered, or was erased).")
    service._audit(db, organization_id=p.organization_id, workspace_id=workspace_id, actor=context.user_id,
                   operation="posting_downloaded", action=AuditAction.EXPORTED, posting_id=str(p.id),
                   filename=p.rendered_filename, content_sha=p.content_sha)
    db.commit()
    safe = (p.rendered_filename or "posting").replace('"', "")
    return Response(content=bytes(p.rendered), media_type=p.rendered_media_type or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"', "Cache-Control": "no-store",
                             "X-Content-Type-Options": "nosniff"})


def _review(db: Session, context: TenantContext, workspace_id: uuid.UUID, posting_id: uuid.UUID, verdict: str,
            body: Optional[ReviewAction]) -> PostingDetail:
    p = _posting(db, workspace_id, posting_id)
    try:
        service.review(db, posting=p, verdict=verdict, actor_user_id=context.user_id,
                       note=body.note if body else None, reference=body.reference if body else None)
    except service.ErpError as exc:
        raise _error(exc) from exc
    db.commit()
    return _posting_detail(db, p)


@router.post("/workspaces/{workspace_id}/erp/postings/{posting_id}/retry", response_model=PostingDetail)
def retry_posting(workspace_id: uuid.UUID, posting_id: uuid.UUID, body: Optional[ReviewAction] = None,
                  db: Session = Depends(get_db), context: TenantContext = Depends(RequireContributor)) -> PostingDetail:
    _ws(context, workspace_id)
    _gate(db, context, "erp.postings.retry")
    return _review(db, context, workspace_id, posting_id, v.VERDICT_RETRY, body)


@router.post("/workspaces/{workspace_id}/erp/postings/{posting_id}/accept", response_model=PostingDetail)
def accept_posting(workspace_id: uuid.UUID, posting_id: uuid.UUID, body: Optional[ReviewAction] = None,
                   db: Session = Depends(get_db), context: TenantContext = Depends(RequireContributor)) -> PostingDetail:
    _ws(context, workspace_id)
    _gate(db, context, "erp.postings.accept")
    return _review(db, context, workspace_id, posting_id, v.VERDICT_ACCEPT, body)


@router.post("/workspaces/{workspace_id}/erp/postings/{posting_id}/cancel", response_model=PostingDetail)
def cancel_posting(workspace_id: uuid.UUID, posting_id: uuid.UUID, body: Optional[ReviewAction] = None,
                   db: Session = Depends(get_db), context: TenantContext = Depends(RequireContributor)) -> PostingDetail:
    _ws(context, workspace_id)
    _gate(db, context, "erp.postings.cancel")
    return _review(db, context, workspace_id, posting_id, v.VERDICT_CANCEL, body)


@router.post("/workspaces/{workspace_id}/erp/postings/{posting_id}/acknowledge", response_model=PostingDetail)
def acknowledge_posting(workspace_id: uuid.UUID, posting_id: uuid.UUID, body: AcknowledgeRequest,
                        db: Session = Depends(get_db),
                        context: TenantContext = Depends(RequireContributor)) -> PostingDetail:
    _ws(context, workspace_id)
    _gate(db, context, "erp.postings.acknowledge")
    p = _posting(db, workspace_id, posting_id)
    try:
        service.acknowledge(db, posting=p, actor_user_id=context.user_id, accepted=body.accepted,
                            reference=body.reference, reason=body.reason,
                            response_file=body.response_text.encode("utf-8") if body.response_text else None)
    except service.ErpError as exc:
        raise _error(exc) from exc
    db.commit()
    return _posting_detail(db, p)


@router.get("/workspaces/{workspace_id}/work-items/{work_item_id}/postings", response_model=PostingList)
def document_postings(workspace_id: uuid.UUID, work_item_id: uuid.UUID, db: Session = Depends(get_db),
                      context: TenantContext = Depends(RequireViewer)) -> PostingList:
    _ws(context, workspace_id)
    _gate(db, context, "erp.postings.document")
    item = db.execute(select(WorkItem.id).where(WorkItem.id == work_item_id,
                                                WorkItem.workspace_id == workspace_id)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    rows = list(db.execute(select(ErpPosting).where(ErpPosting.workspace_id == workspace_id,
                                                    ErpPosting.work_item_id == work_item_id)
                           .order_by(ErpPosting.created_at.desc())).scalars())
    counts: dict[str, int] = {}
    for p in rows:
        counts[p.state] = counts.get(p.state, 0) + 1
    return PostingList(items=_posting_rows(db, rows), total=len(rows),
                       counts_by_state={s_: counts.get(s_, 0) for s_ in v.STATES})


__all__ = ["router"]
