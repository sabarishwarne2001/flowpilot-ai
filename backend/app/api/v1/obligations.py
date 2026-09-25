"""ARCH46-S1:api — Obligations & Temporal Intelligence. Every route is gated on
capability.obligations as its second line (402 CAPABILITY_REQUIRED without it).
Reads need VIEWER; changing an obligation CONTRIBUTOR; holiday calendars
ADMIN; a member issues and revokes their OWN calendar feeds (an admin may
revoke anyone's). The unauthenticated feed itself is in
public_calendar_feeds.py (PUBLIC_ROUTES)."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.obligations import CalendarFeedToken, HolidayCalendar, Obligation, ObligationEvent
from app.models.work_item import WorkItem
from app.models.workspace import WorkspaceRole
from app.schemas.obligations import (
    CalendarOccurrence,
    CalendarUpdateResult,
    CalendarView,
    CompleteRequest,
    DateCalculation,
    DateCalculationRequest,
    DocumentObligations,
    EntityObligations,
    ExtractResult,
    FeedCreate,
    FeedIssued,
    FeedList,
    FeedRow,
    HolidayCalendarCreate,
    HolidayCalendarList,
    HolidayCalendarRow,
    HolidayCalendarUpdate,
    HolidayRow,
    ObligationBrief,
    ObligationCreate,
    ObligationDetail,
    ObligationEventRow,
    ObligationList,
    ObligationRow,
    ObligationUpdate,
    OccurrenceRow,
    ReviewRequest,
    TemplateRow,
    WaiveRequest,
)
from app.services import audit_service
from app.services.obligations import feeds as F
from app.services.obligations import holidays as H
from app.services.obligations import service
from app.services.obligations import temporal as T
from app.services.obligations import vocabulary as v

router = APIRouter(tags=["Obligations"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
RequireAdmin = RequireWorkspaceRole(WorkspaceRole.ADMIN)
CAPABILITY = entitlements.OBLIGATIONS_CAPABILITY


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(db, context=context, capability_key=CAPABILITY, operation=operation)


def _ws(context: TenantContext, workspace_id: uuid.UUID) -> None:
    if context.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")


def _error(exc: service.ObligationError) -> HTTPException:
    codes = {"NOT_FOUND": 404, "ALREADY_CLOSED": 409, "NOT_CLOSED": 409, "ALREADY_REVIEWED": 409, "SUPERSEDED": 409,
             "REJECTED": 409, "EXTRACTED": 409, "ALREADY_REVOKED": 409, "NAME_TAKEN": 409, "TOO_MANY_FEEDS": 409,
             "NO_DATE": 409, "NOT_EXTRACTED": 409}
    return HTTPException(status_code=codes.get(exc.code, status.HTTP_422_UNPROCESSABLE_ENTITY),
                         detail={"code": exc.code, "message": str(exc)})


def _role(context: TenantContext) -> str:
    role = getattr(context, "role", "")
    return str(getattr(role, "value", role) or "")


def _one(db: Session, workspace_id: uuid.UUID, obligation_id: uuid.UUID) -> Obligation:
    row = db.execute(select(Obligation).where(Obligation.id == obligation_id,
                                              Obligation.workspace_id == workspace_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Obligation not found.")
    return row


def _today(db: Session, workspace_id: uuid.UUID) -> tuple[date, str]:
    ws = service.workspace_row(db, workspace_id)
    zone, known = T.zone(ws.timezone)
    # One clock for the service, the sweep and the API (service.now: UTC).
    return T.local_today(service.now(), ws.timezone), (ws.timezone if known else "UTC")


def _rows(db: Session, obs: list[Obligation], today: date) -> list[ObligationRow]:
    from app.models.user import User

    owners = {o.owner_user_id for o in obs if o.owner_user_id}
    emails = {u.id: u.email for u in db.execute(select(User).where(User.id.in_(owners))).scalars()} if owners else {}
    docs = {o.work_item_id for o in obs if o.work_item_id}
    names = dict(db.execute(select(WorkItem.id, WorkItem.original_filename).where(WorkItem.id.in_(docs))).all()) \
        if docs else {}
    roots = service.roots_of(db, [o.entity_id for o in obs])
    out = []
    for o in obs:
        root = roots.get(o.entity_id) if o.entity_id else None
        text = None
        if o.recurrence:
            try:
                text = T.describe_rrule(o.recurrence)
            except T.TemporalError:
                text = o.recurrence
        out.append(ObligationRow(
            id=o.id, kind=o.kind, title=o.title, state=o.state, review=o.review, origin=o.origin, due_date=o.due_date,
            days_until=(o.due_date - today).days if o.due_date else None, lead_days=int(o.lead_days),
            recurrence=o.recurrence, recurrence_text=text, occurrence=int(o.occurrence),
            completed_occurrences=int(o.completed_occurrences), business_day_rule=o.business_day_rule,
            calendar_id=o.calendar_id, anchor_obligation_id=o.anchor_obligation_id, owner_user_id=o.owner_user_id,
            owner_email=emails.get(o.owner_user_id), entity_id=o.entity_id, entity_root_id=root[0] if root else None,
            entity_name=root[1] if root else None, counterparty_name=o.counterparty_name, work_item_id=o.work_item_id,
            work_item_filename=names.get(o.work_item_id), amount=None if o.amount is None else str(o.amount),
            currency=o.currency, confidence=float(o.confidence), reasons=[str(r) for r in (o.reasons or [])],
            superseded=o.superseded_at is not None, created_at=o.created_at, updated_at=o.updated_at,
            state_changed_at=o.state_changed_at))
    return out


def _brief(o: Optional[Obligation]) -> Optional[ObligationBrief]:
    if o is None:
        return None
    return ObligationBrief(id=o.id, kind=o.kind, title=o.title, due_date=o.due_date, state=o.state)


def _detail(db: Session, ob: Obligation) -> ObligationDetail:
    today, zone = _today(db, ob.workspace_id)
    events = list(db.execute(select(ObligationEvent).where(ObligationEvent.obligation_id == ob.id)
                             .order_by(ObligationEvent.created_at, ObligationEvent.id)).scalars())
    upcoming: list[OccurrenceRow] = []
    if ob.recurrence and ob.state in v.ACTIVE_STATES:
        for n, due in T.occurrences(service.rule_of(ob), cal=service.calendar_for(db, ob),
                                    first=int(ob.occurrence) + 1, limit=6):
            upcoming.append(OccurrenceRow(occurrence=n, due_date=due))
    anchor = db.get(Obligation, ob.anchor_obligation_id) if ob.anchor_obligation_id else None
    dependents = list(db.execute(select(Obligation).where(Obligation.anchor_obligation_id == ob.id)).scalars())
    cal = db.get(HolidayCalendar, ob.calendar_id) if ob.calendar_id else None
    return ObligationDetail(
        obligation=_rows(db, [ob], today)[0], description=ob.description, due_rule=dict(ob.due_rule or {}),
        derivation=[str(s) for s in (ob.derivation or [])], evidence=list(ob.evidence or []), quote=ob.quote,
        clause_number=ob.clause_number, detail=dict(ob.detail or {}),
        events=[ObligationEventRow(id=e.id, kind=e.kind, from_state=e.from_state, to_state=e.to_state,
                                   due_date=e.due_date, occurrence=e.occurrence, emitted=e.emitted,
                                   actor_user_id=e.actor_user_id, detail=dict(e.detail or {}), created_at=e.created_at)
                for e in events],
        upcoming=upcoming, anchor=_brief(anchor), dependents=[_brief(d) for d in dependents],
        done_at=ob.done_at, done_by_user_id=ob.done_by_user_id, waived_at=ob.waived_at, waiver_reason=ob.waiver_reason,
        reviewed_at=ob.reviewed_at, reviewed_by_user_id=ob.reviewed_by_user_id, engine_version=ob.engine_version,
        calendar_name=cal.name if cal else None, today=today, timezone=zone)


def _visible(query, *, include_superseded: bool = False, include_rejected: bool = False):
    if not include_superseded:
        query = query.where(Obligation.superseded_at.is_(None))
    if not include_rejected:
        query = query.where(Obligation.review != v.REVIEW_REJECTED)
    return query


# ---------------------------------------------------------------------------
# obligations
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/obligations", response_model=ObligationList)
def list_obligations(workspace_id: uuid.UUID, state: Optional[str] = Query(default=None),
                     kind: Optional[str] = Query(default=None), review: Optional[str] = Query(default=None),
                     owner: Optional[str] = Query(default=None), entity_id: Optional[uuid.UUID] = Query(default=None),
                     work_item_id: Optional[uuid.UUID] = Query(default=None),
                     due_from: Optional[date] = Query(default=None), due_to: Optional[date] = Query(default=None),
                     q: Optional[str] = Query(default=None, max_length=200),
                     include_superseded: bool = Query(default=False), include_rejected: bool = Query(default=False),
                     limit: int = Query(default=100, ge=1, le=v.MAX_LIST), offset: int = Query(default=0, ge=0),
                     db: Session = Depends(get_db), context: TenantContext = Depends(RequireViewer)) -> ObligationList:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.list")
    today, zone = _today(db, workspace_id)
    base = _visible(select(Obligation).where(Obligation.workspace_id == workspace_id),
                    include_superseded=include_superseded, include_rejected=include_rejected)
    if state:
        base = base.where(Obligation.state.in_([s.strip().upper() for s in state.split(",") if s.strip()]))
    if kind:
        base = base.where(Obligation.kind.in_([k.strip().upper() for k in kind.split(",") if k.strip()]))
    if review:
        base = base.where(Obligation.review.in_([r.strip().upper() for r in review.split(",") if r.strip()]))
    if owner:
        if owner.strip().lower() == "me":
            base = base.where(Obligation.owner_user_id == context.user_id)
        elif owner.strip().lower() == "none":
            base = base.where(Obligation.owner_user_id.is_(None))
        else:
            try:
                base = base.where(Obligation.owner_user_id == uuid.UUID(owner))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail={"code": "BAD_OWNER", "message": "owner is me, none or a user id."}) from exc
    if entity_id is not None:
        from app.services.entities import graph

        try:
            members = graph.cluster_ids(db, graph.root_of(db, entity_id).id)
        except Exception:  # noqa: BLE001 - an unknown record lists nothing
            members = [entity_id]
        base = base.where(Obligation.entity_id.in_(members))
    if work_item_id is not None:
        base = base.where(Obligation.work_item_id == work_item_id)
    if due_from is not None:
        base = base.where(Obligation.due_date >= due_from)
    if due_to is not None:
        base = base.where(Obligation.due_date <= due_to)
    if q:
        like = f"%{q.strip()}%"
        base = base.where(or_(Obligation.title.ilike(like), Obligation.counterparty_name.ilike(like)))
    total = int(db.execute(select(func.count()).select_from(base.subquery())).scalar_one())
    obs = list(db.execute(base.order_by(Obligation.due_date.asc().nulls_last(), Obligation.created_at.desc())
                          .limit(limit).offset(offset)).scalars())
    counts = {s: 0 for s in v.STATES}
    for s, n in db.execute(_visible(select(Obligation.state, func.count()).where(Obligation.workspace_id == workspace_id))
                           .group_by(Obligation.state)).all():
        counts[s] = int(n)
    pending = int(db.execute(select(func.count()).select_from(Obligation).where(
        Obligation.workspace_id == workspace_id, Obligation.review == v.REVIEW_PENDING,
        Obligation.superseded_at.is_(None))).scalar_one())
    return ObligationList(items=_rows(db, obs, today), total=total, counts_by_state=counts, pending_review=pending,
                          today=today, timezone=zone)


@router.post("/workspaces/{workspace_id}/obligations", response_model=ObligationDetail, status_code=201)
def create_obligation(workspace_id: uuid.UUID, body: ObligationCreate, db: Session = Depends(get_db),
                      context: TenantContext = Depends(RequireContributor)) -> ObligationDetail:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.create")
    try:
        ob = service.create_manual(
            db, organization_id=context.organization_id, workspace_id=workspace_id, actor_user_id=context.user_id,
            kind=body.kind, title=body.title, rule=body.rule.model_dump(mode="json"), description=body.description,
            owner_user_id=body.owner_user_id, entity_id=body.entity_id, work_item_id=body.work_item_id,
            calendar_id=body.calendar_id, lead_days=body.lead_days,
            amount=None if body.amount is None else Decimal(str(body.amount)), currency=body.currency,
            counterparty_name=body.counterparty_name)
    except service.ObligationError as exc:
        raise _error(exc) from exc
    db.commit()
    return _detail(db, ob)


@router.get("/workspaces/{workspace_id}/obligations/calendar", response_model=CalendarView)
def obligation_calendar(workspace_id: uuid.UUID, from_date: date = Query(alias="from"), to_date: date = Query(alias="to"),
                        owner: Optional[str] = Query(default=None), db: Session = Depends(get_db),
                        context: TenantContext = Depends(RequireViewer)) -> CalendarView:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.calendar")
    if to_date < from_date or (to_date - from_date).days > 400:
        raise HTTPException(status_code=422, detail={"code": "BAD_RANGE", "message": "from..to spans 0-400 days."})
    today, zone = _today(db, workspace_id)
    query = _visible(select(Obligation).where(Obligation.workspace_id == workspace_id))
    if owner and owner.lower() == "me":
        query = query.where(Obligation.owner_user_id == context.user_id)
    obs = list(db.execute(query.where(or_(Obligation.recurrence.is_not(None),
                                          Obligation.due_date.between(from_date, to_date)))).scalars())
    items: list[CalendarOccurrence] = []
    for o in obs:
        if o.due_date is not None and from_date <= o.due_date <= to_date:
            items.append(CalendarOccurrence(obligation_id=o.id, occurrence=int(o.occurrence), due_date=o.due_date,
                                            kind=o.kind, title=o.title, state=o.state, review=o.review,
                                            owner_user_id=o.owner_user_id, projected=False))
        if o.recurrence and o.state in v.ACTIVE_STATES:
            for n, due in T.occurrences(service.rule_of(o), cal=service.calendar_for(db, o),
                                        first=int(o.occurrence) + 1, until=to_date, limit=62):
                if due >= from_date:
                    items.append(CalendarOccurrence(obligation_id=o.id, occurrence=n, due_date=due, kind=o.kind,
                                                    title=o.title, state=v.STATE_OPEN, review=o.review,
                                                    owner_user_id=o.owner_user_id, projected=True))
    items.sort(key=lambda x: (x.due_date, x.title, x.occurrence))
    return CalendarView(from_date=from_date, to_date=to_date, today=today, timezone=zone, items=items[:2000])


@router.post("/workspaces/{workspace_id}/obligations/calculate", response_model=DateCalculation)
def calculate(workspace_id: uuid.UUID, body: DateCalculationRequest, db: Session = Depends(get_db),
              context: TenantContext = Depends(RequireViewer)) -> DateCalculation:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.calculate")
    spec = body.rule.model_dump(mode="json")
    spec.pop("anchor_obligation_id", None)
    if str(spec.get("kind", "")).upper() == v.RULE_OFFSET and not spec.get("anchor_date"):
        spec["anchor_date"] = body.anchor_due.isoformat() if body.anchor_due else None
    try:
        rule, _ = service.build_rule(spec, workspace_id=workspace_id, db=db)
    except service.ObligationError as exc:
        raise _error(exc) from exc
    cal_row = None
    if body.calendar_id is not None:
        cal_row = db.get(HolidayCalendar, body.calendar_id)
        if cal_row is None or cal_row.workspace_id != workspace_id:
            raise HTTPException(status_code=404, detail="Holiday calendar not found.")
    cal = service.calendar_of(cal_row)
    try:
        computed = T.compute(rule, anchor_due=body.anchor_due, cal=cal)
        upcoming = [OccurrenceRow(occurrence=n, due_date=d)
                    for n, d in T.occurrences(rule, cal=cal, limit=body.occurrences)]
    except T.TemporalError as exc:
        raise HTTPException(status_code=422, detail={"code": "BAD_RULE", "message": str(exc)}) from exc
    return DateCalculation(due_date=computed.due, steps=computed.steps, upcoming=upcoming)


@router.get("/workspaces/{workspace_id}/obligations/export")
def export_obligations(workspace_id: uuid.UUID, fmt: str = Query(default="ics", alias="format"),
                       db: Session = Depends(get_db), context: TenantContext = Depends(RequireViewer)) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.export")
    fmt = fmt.strip().lower()
    if fmt not in ("ics", "csv"):
        raise HTTPException(status_code=422, detail={"code": "UNKNOWN_FORMAT", "message": "format is ics or csv."})
    today, zone = _today(db, workspace_id)
    obs = list(db.execute(_visible(select(Obligation).where(Obligation.workspace_id == workspace_id))
                          .order_by(Obligation.due_date.asc().nulls_last(), Obligation.id).limit(v.MAX_LIST * 10)).scalars())
    if fmt == "ics":
        ws = service.workspace_row(db, workspace_id)
        data = service.render_ical(db, obligations=[o for o in obs if o.due_date], name=f"{ws.workspace_name} obligations",
                                   zone=zone, at=service.now(), today=today,
                                   prefix=service.link_prefix(db, workspace_id)).encode("utf-8")
        media, name = "text/calendar; charset=utf-8", "obligations.ics"
    else:
        from app.services.tables.export import safe_text

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "kind", "title", "state", "review", "due_date", "recurrence", "owner", "counterparty",
                         "document", "amount", "currency", "lead_days"])
        for row in _rows(db, obs, today):
            writer.writerow([str(row.id), row.kind, safe_text(row.title), row.state, row.review,
                             row.due_date.isoformat() if row.due_date else "", row.recurrence or "",
                             safe_text(row.owner_email or ""), safe_text(row.counterparty_name or ""),
                             safe_text(row.work_item_filename or ""), row.amount or "", row.currency or "",
                             row.lead_days])
        data = ("﻿" + buffer.getvalue()).encode("utf-8")
        media, name = "text/csv; charset=utf-8", "obligations.csv"
    audit_service.record(db, organization_id=context.organization_id, workspace_id=context.workspace_id,
                         actor_id=context.user_id, resource_type=AuditResourceType.WORKSPACE,
                         resource_id=context.workspace_id, action=AuditAction.EXPORTED, outcome=AuditOutcome.ALLOWED,
                         details={"obligations": {"operation": "export", "format": fmt, "rows": len(obs)}})
    db.commit()
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/workspaces/{workspace_id}/obligations/{obligation_id}", response_model=ObligationDetail)
def get_obligation(workspace_id: uuid.UUID, obligation_id: uuid.UUID, db: Session = Depends(get_db),
                   context: TenantContext = Depends(RequireViewer)) -> ObligationDetail:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.read")
    return _detail(db, _one(db, workspace_id, obligation_id))


@router.patch("/workspaces/{workspace_id}/obligations/{obligation_id}", response_model=ObligationDetail)
def update_obligation(workspace_id: uuid.UUID, obligation_id: uuid.UUID, body: ObligationUpdate,
                      db: Session = Depends(get_db), context: TenantContext = Depends(RequireContributor)) -> ObligationDetail:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.update")
    ob = _one(db, workspace_id, obligation_id)
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if "rule" in changes and changes["rule"] is not None:
        changes["rule"] = body.rule.model_dump(mode="json")
    if "amount" in changes and changes["amount"] is not None:
        changes["amount"] = Decimal(str(changes["amount"]))
    try:
        service.update(db, ob=ob, actor_user_id=context.user_id, changes=changes)
    except service.ObligationError as exc:
        raise _error(exc) from exc
    db.commit()
    return _detail(db, ob)


@router.post("/workspaces/{workspace_id}/obligations/{obligation_id}/complete", response_model=ObligationDetail)
def complete_obligation(workspace_id: uuid.UUID, obligation_id: uuid.UUID, body: CompleteRequest,
                        db: Session = Depends(get_db), context: TenantContext = Depends(RequireContributor)) -> ObligationDetail:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.complete")
    ob = _one(db, workspace_id, obligation_id)
    try:
        service.complete(db, ob=ob, actor_user_id=context.user_id, note=body.note)
    except service.ObligationError as exc:
        raise _error(exc) from exc
    db.commit()
    return _detail(db, ob)


@router.post("/workspaces/{workspace_id}/obligations/{obligation_id}/waive", response_model=ObligationDetail)
def waive_obligation(workspace_id: uuid.UUID, obligation_id: uuid.UUID, body: WaiveRequest,
                     db: Session = Depends(get_db), context: TenantContext = Depends(RequireContributor)) -> ObligationDetail:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.waive")
    ob = _one(db, workspace_id, obligation_id)
    try:
        service.waive(db, ob=ob, actor_user_id=context.user_id, reason=body.reason)
    except service.ObligationError as exc:
        raise _error(exc) from exc
    db.commit()
    return _detail(db, ob)


@router.post("/workspaces/{workspace_id}/obligations/{obligation_id}/reopen", response_model=ObligationDetail)
def reopen_obligation(workspace_id: uuid.UUID, obligation_id: uuid.UUID, db: Session = Depends(get_db),
                      context: TenantContext = Depends(RequireContributor)) -> ObligationDetail:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.reopen")
    ob = _one(db, workspace_id, obligation_id)
    try:
        service.reopen(db, ob=ob, actor_user_id=context.user_id)
    except service.ObligationError as exc:
        raise _error(exc) from exc
    db.commit()
    return _detail(db, ob)


@router.post("/workspaces/{workspace_id}/obligations/{obligation_id}/review", response_model=ObligationDetail)
def review_obligation(workspace_id: uuid.UUID, obligation_id: uuid.UUID, body: ReviewRequest,
                      db: Session = Depends(get_db), context: TenantContext = Depends(RequireContributor)) -> ObligationDetail:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.review")
    ob = _one(db, workspace_id, obligation_id)
    try:
        service.review(db, obligation=ob, verdict=body.verdict, actor_user_id=context.user_id)
    except service.ObligationError as exc:
        raise _error(exc) from exc
    db.commit()
    return _detail(db, ob)


@router.delete("/workspaces/{workspace_id}/obligations/{obligation_id}", status_code=204)
def delete_obligation(workspace_id: uuid.UUID, obligation_id: uuid.UUID, db: Session = Depends(get_db),
                      context: TenantContext = Depends(RequireContributor)) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.delete")
    ob = _one(db, workspace_id, obligation_id)
    try:
        service.delete_obligation(db, ob=ob, actor_user_id=context.user_id)
    except service.ObligationError as exc:
        raise _error(exc) from exc
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# a document's and a record's obligations
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/work-items/{work_item_id}/obligations", response_model=DocumentObligations)
def document_obligations(workspace_id: uuid.UUID, work_item_id: uuid.UUID, db: Session = Depends(get_db),
                         context: TenantContext = Depends(RequireViewer)) -> DocumentObligations:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.document")
    item = db.execute(select(WorkItem).where(WorkItem.id == work_item_id, WorkItem.workspace_id == workspace_id)) \
        .scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    today, _ = _today(db, workspace_id)
    obs = list(db.execute(_visible(select(Obligation).where(Obligation.work_item_id == item.id), include_rejected=True)
                          .order_by(Obligation.due_date.asc().nulls_last(), Obligation.kind)).scalars())
    return DocumentObligations(work_item_id=item.id, original_filename=item.original_filename,
                               items=_rows(db, obs, today), today=today)


@router.post("/workspaces/{workspace_id}/work-items/{work_item_id}/obligations/extract", response_model=ExtractResult)
def extract_document(workspace_id: uuid.UUID, work_item_id: uuid.UUID, db: Session = Depends(get_db),
                     context: TenantContext = Depends(RequireContributor)) -> ExtractResult:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.extract")
    item = db.execute(select(WorkItem).where(WorkItem.id == work_item_id, WorkItem.workspace_id == workspace_id)) \
        .scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    summary = service.extract_for_work_item(db, work_item=item, organization_id=context.organization_id,
                                            actor_user_id=context.user_id)
    audit_service.record(db, organization_id=context.organization_id, workspace_id=workspace_id,
                         actor_id=context.user_id, resource_type=AuditResourceType.WORKSPACE, resource_id=workspace_id,
                         action=AuditAction.UPDATED, outcome=AuditOutcome.ALLOWED,
                         details={"obligations": {"operation": "extract", "work_item_id": str(item.id),
                                                  **summary.as_json()}})
    db.commit()
    return ExtractResult(extracted=True, **summary.as_json())


@router.get("/workspaces/{workspace_id}/entities/{entity_id}/obligations", response_model=EntityObligations)
def entity_obligations(workspace_id: uuid.UUID, entity_id: uuid.UUID, db: Session = Depends(get_db),
                       context: TenantContext = Depends(RequireViewer)) -> EntityObligations:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.entity")
    from app.models.entity_graph import Entity
    from app.services.entities import graph

    entity = db.get(Entity, entity_id)
    if entity is None or entity.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Record not found.")
    root = graph.root_of(db, entity_id)
    members = graph.cluster_ids(db, root.id)
    today, _ = _today(db, workspace_id)
    obs = list(db.execute(_visible(select(Obligation).where(Obligation.workspace_id == workspace_id,
                                                            Obligation.entity_id.in_(members)))
                          .order_by(Obligation.due_date.asc().nulls_last(), Obligation.kind).limit(v.MAX_LIST)).scalars())
    return EntityObligations(entity_id=entity_id, root_id=root.id, items=_rows(db, obs, today), today=today)


# ---------------------------------------------------------------------------
# holiday calendars
# ---------------------------------------------------------------------------


def _calendar_row(db: Session, cal: HolidayCalendar) -> HolidayCalendarRow:
    used = int(db.execute(select(func.count()).select_from(Obligation).where(Obligation.calendar_id == cal.id))
               .scalar_one())
    return HolidayCalendarRow(id=cal.id, name=cal.name, source=cal.source, template_code=cal.template_code,
                              region=cal.region, weekend_days=[int(x) for x in (cal.weekend_days or [])],
                              holidays=[HolidayRow(date=d, name=n) for d, n in zip(cal.holidays or [],
                                                                                   cal.holiday_names or [])],
                              is_default=bool(cal.is_default), revision=int(cal.revision), obligations=used,
                              updated_at=cal.updated_at)


def _calendar_or_404(db: Session, workspace_id: uuid.UUID, calendar_id: uuid.UUID) -> HolidayCalendar:
    row = db.execute(select(HolidayCalendar).where(HolidayCalendar.id == calendar_id,
                                                   HolidayCalendar.workspace_id == workspace_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Holiday calendar not found.")
    return row


@router.get("/workspaces/{workspace_id}/holiday-calendars", response_model=HolidayCalendarList)
def list_calendars(workspace_id: uuid.UUID, db: Session = Depends(get_db),
                   context: TenantContext = Depends(RequireViewer)) -> HolidayCalendarList:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.calendars")
    rows = list(db.execute(select(HolidayCalendar).where(HolidayCalendar.workspace_id == workspace_id)
                           .order_by(HolidayCalendar.is_default.desc(), HolidayCalendar.name)).scalars())
    return HolidayCalendarList(items=[_calendar_row(db, r) for r in rows],
                               templates=[TemplateRow(code=t.code, name=t.name, region=t.region,
                                                      weekend_days=list(t.weekend), description=t.description)
                                          for t in H.TEMPLATES])


@router.post("/workspaces/{workspace_id}/holiday-calendars", response_model=HolidayCalendarRow, status_code=201)
def create_calendar(workspace_id: uuid.UUID, body: HolidayCalendarCreate, db: Session = Depends(get_db),
                    context: TenantContext = Depends(RequireAdmin)) -> HolidayCalendarRow:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.calendar_create")
    try:
        row = service.create_calendar(
            db, organization_id=context.organization_id, workspace_id=workspace_id, actor_user_id=context.user_id,
            name=body.name, template=body.template, years=body.years,
            holidays=[H.Holiday(h.date, h.name) for h in body.holidays], ics=body.ics, weekend=body.weekend_days,
            is_default=body.is_default)
    except service.ObligationError as exc:
        raise _error(exc) from exc
    db.commit()
    return _calendar_row(db, row)


@router.patch("/workspaces/{workspace_id}/holiday-calendars/{calendar_id}", response_model=CalendarUpdateResult)
def update_calendar(workspace_id: uuid.UUID, calendar_id: uuid.UUID, body: HolidayCalendarUpdate,
                    db: Session = Depends(get_db), context: TenantContext = Depends(RequireAdmin)) -> CalendarUpdateResult:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.calendar_update")
    row = _calendar_or_404(db, workspace_id, calendar_id)
    try:
        moved = service.update_calendar(
            db, row=row, actor_user_id=context.user_id, name=body.name,
            holidays=None if body.holidays is None else [H.Holiday(h.date, h.name) for h in body.holidays],
            weekend=body.weekend_days, is_default=body.is_default)
    except service.ObligationError as exc:
        raise _error(exc) from exc
    db.commit()
    return CalendarUpdateResult(calendar=_calendar_row(db, row), rescheduled=moved)


@router.delete("/workspaces/{workspace_id}/holiday-calendars/{calendar_id}", status_code=204)
def delete_calendar(workspace_id: uuid.UUID, calendar_id: uuid.UUID, db: Session = Depends(get_db),
                    context: TenantContext = Depends(RequireAdmin)) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.calendar_delete")
    row = _calendar_or_404(db, workspace_id, calendar_id)
    service.delete_calendar(db, row=row, actor_user_id=context.user_id)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# calendar feeds (issuing and revoking; the feed itself is public_calendar_feeds.py)
# ---------------------------------------------------------------------------


def _feed_row(row: CalendarFeedToken, me: uuid.UUID) -> FeedRow:
    return FeedRow(id=row.id, label=row.label, scope=row.scope, include_closed=row.include_closed, user_id=row.user_id,
                   mine=row.user_id == me, created_at=row.created_at, expires_at=row.expires_at,
                   last_used_at=row.last_used_at, use_count=int(row.use_count), revoked_at=row.revoked_at)


@router.get("/workspaces/{workspace_id}/calendar-feeds", response_model=FeedList)
def list_feeds(workspace_id: uuid.UUID, db: Session = Depends(get_db),
               context: TenantContext = Depends(RequireViewer)) -> FeedList:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.feeds")
    query = select(CalendarFeedToken).where(CalendarFeedToken.workspace_id == workspace_id)
    if _role(context) not in ("ADMIN", "OWNER"):
        query = query.where(CalendarFeedToken.user_id == context.user_id)
    rows = list(db.execute(query.order_by(CalendarFeedToken.revoked_at.is_not(None), CalendarFeedToken.created_at.desc())
                           .limit(200)).scalars())
    return FeedList(items=[_feed_row(r, context.user_id) for r in rows])


@router.post("/workspaces/{workspace_id}/calendar-feeds", response_model=FeedIssued, status_code=201)
def issue_feed(workspace_id: uuid.UUID, body: FeedCreate, db: Session = Depends(get_db),
               context: TenantContext = Depends(RequireViewer)) -> FeedIssued:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.feed_issue")
    expires = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    try:
        row, token = service.issue_feed(db, organization_id=context.organization_id, workspace_id=workspace_id,
                                        user_id=context.user_id, label=body.label or "Obligations", scope=body.scope,
                                        include_closed=body.include_closed, expires_at=expires)
    except service.ObligationError as exc:
        raise _error(exc) from exc
    db.commit()
    # The token is in this response and nowhere else: never logged, stored only as its SHA-256.
    return FeedIssued(feed=_feed_row(row, context.user_id), token=token, path=F.feed_path(token))


@router.delete("/workspaces/{workspace_id}/calendar-feeds/{feed_id}", status_code=204)
def revoke_feed(workspace_id: uuid.UUID, feed_id: uuid.UUID, db: Session = Depends(get_db),
                context: TenantContext = Depends(RequireViewer)) -> Response:
    _ws(context, workspace_id)
    _gate(db, context, "obligations.feed_revoke")
    row = db.execute(select(CalendarFeedToken).where(CalendarFeedToken.id == feed_id,
                                                     CalendarFeedToken.workspace_id == workspace_id)).scalar_one_or_none()
    if row is None or (row.user_id != context.user_id and _role(context) not in ("ADMIN", "OWNER")):
        raise HTTPException(status_code=404, detail="Feed not found.")
    try:
        service.revoke_feed(db, row=row, actor_user_id=context.user_id)
    except service.ObligationError as exc:
        raise _error(exc) from exc
    db.commit()
    return Response(status_code=204)


__all__ = ["router"]
