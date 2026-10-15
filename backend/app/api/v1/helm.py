"""Helm's API (docs/helm/ARCHITECTURE.md §11). Organization-scoped; every route is a plain `def`
(FastAPI runs it in the threadpool: no database work on the event loop).

    GET    /organizations/{oid}/helm                                  access, limits, books, workspaces
    GET    /organizations/{oid}/helm/library                          the mandate gallery
    POST   /organizations/{oid}/helm/books                            start Helm on a workspace (or all, Enterprise)
    GET    /organizations/{oid}/helm/books/{bid}                      one book
    GET    /organizations/{oid}/helm/books/{bid}/settings             identities, cash, run days, autonomy
    PATCH  /organizations/{oid}/helm/books/{bid}/settings
    POST   /organizations/{oid}/helm/books/{bid}/cash-positions       type a cash position
    GET    /organizations/{oid}/helm/books/{bid}/counterparties
    PATCH  /organizations/{oid}/helm/books/{bid}/counterparties/{cid} confirm an MSME category, a contact
    GET    /organizations/{oid}/helm/books/{bid}/cockpit              the plan, instruments, actions, value held
    POST   /organizations/{oid}/helm/books/{bid}/plan                 re-plan now
    POST   /organizations/{oid}/helm/books/{bid}/plan/approve-all     approve every action that passed the Guard
    GET    /organizations/{oid}/helm/books/{bid}/mandates
    POST   /organizations/{oid}/helm/books/{bid}/mandates             create (from the gallery or a draft)
    POST   /organizations/{oid}/helm/books/{bid}/mandates/preview     validate and read back without saving
    POST   /organizations/{oid}/helm/books/{bid}/mandates/draft       a sentence to a draft (metered model call)
    PATCH  /organizations/{oid}/helm/books/{bid}/mandates/{mid}       edit: a new immutable version
    POST   /organizations/{oid}/helm/books/{bid}/mandates/{mid}/activate | pause | archive
    GET    /organizations/{oid}/helm/books/{bid}/ledger               the Promise ledger, filtered
    GET    /organizations/{oid}/helm/books/{bid}/payment-run          the hand-over list (masked accounts)
    POST   /organizations/{oid}/helm/books/{bid}/invoices/{wid}/paid  a person records a payment
    GET    /organizations/{oid}/helm/books/{bid}/certificates
    GET    /organizations/{oid}/helm/actions/{aid}
    POST   /organizations/{oid}/helm/actions/{aid}/approve | reject | undo
    GET    /organizations/{oid}/helm/certificates/{cid}               the readable page's data
    GET    /organizations/{oid}/helm/certificates/{cid}/download      the canonical JSON
    POST   /organizations/{oid}/helm/sample                           build the sample company (a job; any plan)
    DELETE /organizations/{oid}/helm/sample                           archive it
    POST   /organizations/{oid}/helm/backtests                        replay the past (preview: once a week)
    GET    /organizations/{oid}/helm/books/{bid}/backtests
    GET    /organizations/{oid}/helm/backtests/{tid}                  findings, value by honesty class

Roles: owners and admins decide; the billing role sees everything and approves payment actions;
members get 403 (their Helm tasks are in the review hub). Writes need `capability.helm` (402
CAPABILITY_REQUIRED otherwise), except on the sample company. Read-only billing states and an ended
trial refuse writes before the route runs (`get_organization_context`).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import OrganizationContext, RequireAnyOrgRole, get_db
from app.services.helm import gate, money, service
from app.services.helm import vocabulary as v
from app.services.helm.mandates import registry

router = APIRouter(tags=["Helm"])

P = "/organizations/{organization_id}/helm"


def _viewer(db: Session, context: OrganizationContext) -> service.Viewer:
    return service.viewer(db, organization_id=context.organization_id, user_id=context.user_id,
                          role=context.role)


class IdentityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, max_length=300)
    tax_id: Optional[str] = Field(default=None, max_length=64)
    tax_kind: Optional[str] = Field(default=None, max_length=16)


class BookIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: Optional[uuid.UUID] = None
    kind: Literal["WORKSPACE", "ORGANIZATION"] = "WORKSPACE"
    name: Optional[str] = Field(default=None, max_length=200)


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identities: Optional[list[IdentityIn]] = Field(default=None, max_length=10)
    run_weekdays: Optional[list[int]] = Field(default=None, max_length=7)
    horizon_days: Optional[int] = None
    cost_of_capital_pct: Optional[float] = None
    default_autonomy: Optional[Literal["watch", "propose", "act"]] = None
    briefing_enabled: Optional[bool] = None
    business_type: Optional[str] = Field(default=None, max_length=32)
    first_run_step: Optional[int] = Field(default=None, ge=0, le=4)


class CashIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = Field(..., min_length=3, max_length=3)
    amount: Decimal = Field(..., ge=Decimal("-1000000000000"), le=Decimal("1000000000000"))
    as_of: date
    note: Optional[str] = Field(default=None, max_length=300)


class CounterpartyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    msme_category: Optional[Literal["MICRO", "SMALL", "MEDIUM", "NONE"]] = None
    contact_email: Optional[str] = Field(default=None, max_length=320)
    contact_name: Optional[str] = Field(default=None, max_length=200)


class MandateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: str = Field(..., min_length=3, max_length=48)
    params: Optional[dict[str, Any]] = None
    scope: Optional[dict[str, Any]] = None
    autonomy: Optional[dict[str, Any]] = None
    title: Optional[str] = Field(default=None, max_length=200)
    activate: bool = False


class PreviewIn(MandateIn):
    #: The mandate being edited, so the validator does not call it a duplicate of itself.
    mandate_id: Optional[uuid.UUID] = None


class MandatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    params: Optional[dict[str, Any]] = None
    scope: Optional[dict[str, Any]] = None
    autonomy: Optional[dict[str, Any]] = None
    title: Optional[str] = Field(default=None, max_length=200)


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentence: str = Field(..., min_length=1, max_length=500)


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)


class BacktestMandateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: str = Field(..., min_length=3, max_length=48)
    params: Optional[dict[str, Any]] = None
    scope: Optional[dict[str, Any]] = None


class BacktestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: Optional[uuid.UUID] = None
    workspace_id: Optional[uuid.UUID] = None
    months: int = Field(default=6, ge=1, le=24)
    templates: Optional[list[str]] = Field(default=None, max_length=20)
    #: Full draft specs: a drafted mandate backtested before it is saved.
    mandates: Optional[list[BacktestMandateIn]] = Field(default=None, max_length=20)


class PaidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paid_on: date
    reference: Optional[str] = Field(default=None, max_length=200)


# ---------------------------------------------------------------------------


@router.get(P)
def helm_overview(organization_id: uuid.UUID, db: Session = Depends(get_db),
                  context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    from app.models.helm import HelmBook
    from app.models.workspace import Workspace, WorkspaceStatus

    who = _viewer(db, context)
    org_id = context.organization_id
    allowed, next_at = gate.preview_backtest_allowed(db, org_id)
    booked = {b for (b,) in db.execute(select(HelmBook.workspace_id).where(
        HelmBook.organization_id == org_id, HelmBook.status == v.BOOK_ACTIVE)).all() if b}
    workspaces = [
        {"id": str(w.id), "name": w.workspace_name, "slug": w.slug, "is_sample": w.is_sample,
         "has_book": w.id in booked, "currency": w.currency}
        for w in db.execute(select(Workspace).where(Workspace.organization_id == org_id,
                                                    Workspace.status == WorkspaceStatus.ACTIVE)
                            .order_by(Workspace.created_at)).scalars()
        if who.can_read(w.id)
    ]
    return {
        "access": {"helm": who.helm, "preview": not who.helm, "custom": gate.custom_held(db, org_id),
                   "approval_chains": gate.approval_chains_held(db, org_id),
                   "all_workspaces": gate.all_workspaces_held(db, org_id), "api": gate.api_held(db, org_id)},
        "role": who.role, "can_decide": who.decides,
        "limits": {"mandates": gate.mandates_limit(db, org_id), "active_mandates": gate.active_mandate_count(db, org_id),
                   "backtest_months": gate.backtest_months_limit(db, org_id)},
        "preview": {"backtest_allowed": allowed, "next_backtest_at": next_at.isoformat() if next_at else None},
        "books": service.list_books(db, who), "workspaces": workspaces, "sample": service.sample_state(db, who),
    }


@router.get(f"{P}/library")
def helm_library(organization_id: uuid.UUID, db: Session = Depends(get_db),
                 context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    _viewer(db, context)
    enterprise = gate.custom_held(db, context.organization_id)
    from app.services.helm.mandates.base import GROUPS

    return {"groups": list(GROUPS), "templates": registry.library(enterprise=enterprise)}


@router.post(f"{P}/books", status_code=201)
def helm_create_book(organization_id: uuid.UUID, payload: BookIn, db: Session = Depends(get_db),
                     context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    book = service.create_book(db, who, workspace_id=payload.workspace_id, name=payload.name, kind=payload.kind)
    db.commit()
    return service.book_json(db, who, book)


@router.get(f"{P}/books/{{book_id}}")
def helm_get_book(organization_id: uuid.UUID, book_id: uuid.UUID, db: Session = Depends(get_db),
                  context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    return service.book_json(db, who, service.book_for(db, who, book_id))


@router.get(f"{P}/books/{{book_id}}/settings")
def helm_get_settings(organization_id: uuid.UUID, book_id: uuid.UUID, db: Session = Depends(get_db),
                      context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    return service.settings_json(db, who, service.book_for(db, who, book_id))


@router.patch(f"{P}/books/{{book_id}}/settings")
def helm_update_settings(organization_id: uuid.UUID, book_id: uuid.UUID, payload: SettingsIn,
                         db: Session = Depends(get_db),
                         context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    changes = payload.model_dump(exclude_unset=True)
    if payload.identities is not None:
        changes["identities"] = [i.model_dump() for i in payload.identities]
    result = service.update_settings(db, who, service.book_for(db, who, book_id), changes)
    db.commit()
    return result


@router.post(f"{P}/books/{{book_id}}/cash-positions", status_code=201)
def helm_add_cash(organization_id: uuid.UUID, book_id: uuid.UUID, payload: CashIn, db: Session = Depends(get_db),
                  context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    result = service.add_cash_position(db, who, service.book_for(db, who, book_id), currency=payload.currency,
                                       amount_micros=money.to_micros(payload.amount) or 0, as_of=payload.as_of,
                                       note=payload.note)
    db.commit()
    return result


@router.get(f"{P}/books/{{book_id}}/counterparties")
def helm_counterparties(organization_id: uuid.UUID, book_id: uuid.UUID, db: Session = Depends(get_db),
                        context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    return {"counterparties": service.list_counterparties(db, who, service.book_for(db, who, book_id))}


@router.patch(f"{P}/books/{{book_id}}/counterparties/{{counterparty_id}}")
def helm_update_counterparty(organization_id: uuid.UUID, book_id: uuid.UUID, counterparty_id: uuid.UUID,
                             payload: CounterpartyIn, db: Session = Depends(get_db),
                             context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    result = service.update_counterparty(db, who, service.book_for(db, who, book_id), counterparty_id,
                                         payload.model_dump(exclude_unset=True))
    db.commit()
    return result


@router.get(f"{P}/books/{{book_id}}/cockpit")
def helm_cockpit(organization_id: uuid.UUID, book_id: uuid.UUID, db: Session = Depends(get_db),
                 context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    return service.cockpit(db, who, service.book_for(db, who, book_id))


@router.post(f"{P}/books/{{book_id}}/plan")
def helm_replan(organization_id: uuid.UUID, book_id: uuid.UUID, db: Session = Depends(get_db),
                context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    result = service.replan(db, who, service.book_for(db, who, book_id))
    db.commit()
    return result


@router.post(f"{P}/books/{{book_id}}/plan/approve-all")
def helm_approve_all(organization_id: uuid.UUID, book_id: uuid.UUID, db: Session = Depends(get_db),
                     context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    result = service.approve_all(db, who, service.book_for(db, who, book_id))
    db.commit()
    return result


@router.get(f"{P}/books/{{book_id}}/mandates")
def helm_mandates(organization_id: uuid.UUID, book_id: uuid.UUID, db: Session = Depends(get_db),
                  context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    return {"mandates": service.list_mandates(db, who, service.book_for(db, who, book_id))}


@router.post(f"{P}/books/{{book_id}}/mandates", status_code=201)
def helm_create_mandate(organization_id: uuid.UUID, book_id: uuid.UUID, payload: MandateIn,
                        db: Session = Depends(get_db),
                        context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    result = service.create_mandate(db, who, service.book_for(db, who, book_id),
                                    payload.model_dump(exclude={"activate"}), activate=payload.activate)
    db.commit()
    return result


@router.post(f"{P}/books/{{book_id}}/mandates/preview")
def helm_preview_mandate(organization_id: uuid.UUID, book_id: uuid.UUID, payload: PreviewIn,
                         db: Session = Depends(get_db),
                         context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    return service.preview_spec(db, who, service.book_for(db, who, book_id),
                                payload.model_dump(exclude={"activate", "mandate_id"}),
                                editing=str(payload.mandate_id) if payload.mandate_id else None)


@router.post(f"{P}/books/{{book_id}}/mandates/draft")
def helm_draft_mandate(organization_id: uuid.UUID, book_id: uuid.UUID, payload: DraftIn,
                       db: Session = Depends(get_db),
                       context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    """A sentence to a mandate draft: one metered model call, then the validator and the readback.
    Nothing is saved; the gallery answers when the model cannot."""
    from app.services.helm import drafting

    who = _viewer(db, context)
    result = drafting.draft(db, who, service.book_for(db, who, book_id), payload.sentence)
    db.commit()  # the call's metering
    return result


@router.patch(f"{P}/books/{{book_id}}/mandates/{{mandate_id}}")
def helm_update_mandate(organization_id: uuid.UUID, book_id: uuid.UUID, mandate_id: uuid.UUID,
                        payload: MandatePatch, db: Session = Depends(get_db),
                        context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    result = service.update_mandate(db, who, service.book_for(db, who, book_id), mandate_id,
                                    payload.model_dump(exclude_unset=True))
    db.commit()
    return result


def _status_route(status: str):
    def route(organization_id: uuid.UUID, book_id: uuid.UUID, mandate_id: uuid.UUID, db: Session = Depends(get_db),
              context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
        who = _viewer(db, context)
        result = service.set_status(db, who, service.book_for(db, who, book_id), mandate_id, status)
        db.commit()
        return result

    route.__name__ = f"helm_mandate_{status.lower()}"
    return route


router.add_api_route(f"{P}/books/{{book_id}}/mandates/{{mandate_id}}/activate", _status_route(v.MANDATE_ACTIVE),
                     methods=["POST"], name="helm_mandate_activate")
router.add_api_route(f"{P}/books/{{book_id}}/mandates/{{mandate_id}}/pause", _status_route(v.MANDATE_PAUSED),
                     methods=["POST"], name="helm_mandate_pause")
router.add_api_route(f"{P}/books/{{book_id}}/mandates/{{mandate_id}}/archive", _status_route(v.MANDATE_ARCHIVED),
                     methods=["POST"], name="helm_mandate_archive")


@router.get(f"{P}/books/{{book_id}}/ledger")
def helm_ledger(organization_id: uuid.UUID, book_id: uuid.UUID, kind: Optional[str] = Query(None),
                direction: Optional[str] = Query(None), status: Optional[str] = Query(None),
                counterparty: Optional[str] = Query(None, max_length=255), at_risk: bool = Query(False),
                limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0),
                db: Session = Depends(get_db),
                context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    for value, allowed in ((kind, v.PROMISE_KINDS), (direction, v.DIRECTIONS), (status, v.PROMISE_STATUSES)):
        if value is not None and value not in allowed:
            from app.services.helm.errors import HelmInvalid

            raise HelmInvalid(f"Unknown filter value {value!r}.", reason="FILTER")
    return service.ledger_view(db, who, service.book_for(db, who, book_id), kind=kind, direction=direction,
                               status=status, counterparty=counterparty, at_risk=at_risk, limit=limit, offset=offset)


@router.get(f"{P}/books/{{book_id}}/payment-run")
def helm_payment_run(organization_id: uuid.UUID, book_id: uuid.UUID, on: Optional[date] = Query(None),
                     db: Session = Depends(get_db),
                     context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    return service.payment_run(db, who, service.book_for(db, who, book_id), on=on)


@router.post(f"{P}/books/{{book_id}}/invoices/{{work_item_id}}/paid")
def helm_mark_paid(organization_id: uuid.UUID, book_id: uuid.UUID, work_item_id: uuid.UUID, payload: PaidIn,
                   db: Session = Depends(get_db),
                   context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    result = service.mark_paid(db, who, service.book_for(db, who, book_id), work_item_id, paid_on=payload.paid_on,
                               reference=payload.reference)
    db.commit()
    return result


@router.get(f"{P}/books/{{book_id}}/certificates")
def helm_certificates(organization_id: uuid.UUID, book_id: uuid.UUID, limit: int = Query(100, ge=1, le=500),
                      db: Session = Depends(get_db),
                      context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    return {"certificates": service.list_certificates(db, who, service.book_for(db, who, book_id), limit=limit)}


@router.get(f"{P}/actions/{{action_id}}")
def helm_get_action(organization_id: uuid.UUID, action_id: uuid.UUID, db: Session = Depends(get_db),
                    context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    return service.action_json(db, who, service.action_for(db, who, action_id))


@router.post(f"{P}/actions/{{action_id}}/approve")
def helm_approve(organization_id: uuid.UUID, action_id: uuid.UUID, db: Session = Depends(get_db),
                 context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    result = service.approve(db, who, action_id)
    db.commit()
    return result


@router.post(f"{P}/actions/{{action_id}}/reject")
def helm_reject(organization_id: uuid.UUID, action_id: uuid.UUID, payload: RejectIn, db: Session = Depends(get_db),
                context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    result = service.reject(db, who, action_id, payload.reason)
    db.commit()
    return result


@router.post(f"{P}/actions/{{action_id}}/undo")
def helm_undo(organization_id: uuid.UUID, action_id: uuid.UUID, db: Session = Depends(get_db),
              context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    result = service.undo(db, who, action_id)
    db.commit()
    return result


@router.get(f"{P}/certificates/{{certificate_id}}")
def helm_certificate(organization_id: uuid.UUID, certificate_id: uuid.UUID, db: Session = Depends(get_db),
                     context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    return service.certificate_json(db, who, certificate_id)


@router.get(f"{P}/certificates/{{certificate_id}}/download")
def helm_certificate_download(organization_id: uuid.UUID, certificate_id: uuid.UUID, db: Session = Depends(get_db),
                              context: OrganizationContext = Depends(RequireAnyOrgRole)) -> Response:
    import json

    who = _viewer(db, context)
    found = service.certificate_json(db, who, certificate_id)
    text = found["canonical"] if found["readable"] else json.dumps(found["body"], sort_keys=True,
                                                                  separators=(",", ":"))
    return Response(content=text, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="helm-certificate-{certificate_id}.json"',
                             "X-Helm-Certificate-SHA256": found["sha256"]})


@router.post(f"{P}/backtests", status_code=201)
def helm_request_backtest(organization_id: uuid.UUID, payload: BacktestIn, db: Session = Depends(get_db),
                          context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    result = service.request_backtest(db, who, book_id=payload.book_id, workspace_id=payload.workspace_id,
                                      months=payload.months, templates=payload.templates,
                                      mandates=[m.model_dump() for m in payload.mandates]
                                      if payload.mandates else None)
    db.commit()
    return result


@router.get(f"{P}/books/{{book_id}}/backtests")
def helm_list_backtests(organization_id: uuid.UUID, book_id: uuid.UUID, db: Session = Depends(get_db),
                        context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    return {"backtests": service.list_backtests(db, who, service.book_for(db, who, book_id))}


@router.get(f"{P}/backtests/{{backtest_id}}")
def helm_backtest(organization_id: uuid.UUID, backtest_id: uuid.UUID, db: Session = Depends(get_db),
                  context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    return service.backtest_json(db, who, backtest_id)


@router.post(f"{P}/sample", status_code=202)
def helm_build_sample(organization_id: uuid.UUID, db: Session = Depends(get_db),
                      context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    result = service.build_sample(db, who)
    db.commit()
    return result


@router.delete(f"{P}/sample")
def helm_delete_sample(organization_id: uuid.UUID, db: Session = Depends(get_db),
                       context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    result = service.delete_sample(db, who)
    db.commit()
    return result
