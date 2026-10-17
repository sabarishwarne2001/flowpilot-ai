"""Helm's API (docs/helm/ARCHITECTURE.md §11). Organization-scoped; every route is a plain `def`
(FastAPI runs it in the threadpool: no database work on the event loop).

    GET    /organizations/{oid}/helm                                  access, limits, books, workspaces
    GET    /organizations/{oid}/helm/workspaces/{wid}/card            the workspace Overview's Helm card
    GET    /organizations/{oid}/helm/books/{bid}/bank-accounts        bank accounts (last four digits only)
    POST   /organizations/{oid}/helm/books/{bid}/bank-accounts
    PATCH  /organizations/{oid}/helm/books/{bid}/bank-accounts/{aid}  rename, or save its column mapping
    POST   /organizations/{oid}/helm/books/{bid}/bank-statements/preview   columns, lines, the mapping to confirm
    POST   /organizations/{oid}/helm/books/{bid}/bank-statements      import: new lines, cash, matches
    GET    /organizations/{oid}/helm/books/{bid}/bank-imports
    GET    /organizations/{oid}/helm/books/{bid}/bank-lines           ?status=WAITING|MATCHED|IGNORED
    POST   /organizations/{oid}/helm/bank-lines/{lid}/match | ignore | reopen
    GET    /organizations/{oid}/helm/library                          the mandate gallery
    POST   /organizations/{oid}/helm/books                            start Helm on a workspace (or all, Enterprise)
    GET    /organizations/{oid}/helm/books/{bid}                      one book
    GET    /organizations/{oid}/helm/books/{bid}/settings             identities, cash, run days, approval chain
    PATCH  /organizations/{oid}/helm/books/{bid}/settings             (the approval chain: Enterprise)
    POST   /organizations/{oid}/helm/books/{bid}/cash-positions       type a cash position
    GET    /organizations/{oid}/helm/books/{bid}/counterparties
    PATCH  /organizations/{oid}/helm/books/{bid}/counterparties/{cid} confirm an MSME category, a contact
    GET    /organizations/{oid}/helm/books/{bid}/cockpit              the plan, instruments, actions, value held
    POST   /organizations/{oid}/helm/books/{bid}/plan                 re-plan now
    POST   /organizations/{oid}/helm/books/{bid}/plan/approve-all     approve every action that passed the Guard
                                                                      (approved, waiting for someone else, skipped)
    POST   /organizations/{oid}/helm/books/{bid}/what-if              a scenario beside today's plan (saves nothing)
    GET    /organizations/{oid}/helm/books/{bid}/mandates
    POST   /organizations/{oid}/helm/books/{bid}/mandates             create (from the gallery or a draft)
    POST   /organizations/{oid}/helm/books/{bid}/mandates/preview     validate and read back without saving
    POST   /organizations/{oid}/helm/books/{bid}/mandates/draft       a sentence to a draft (metered model call)
    PATCH  /organizations/{oid}/helm/books/{bid}/mandates/{mid}       edit: a new immutable version
    POST   /organizations/{oid}/helm/books/{bid}/mandates/{mid}/activate | pause | archive
    GET    /organizations/{oid}/helm/books/{bid}/ledger               the Promise ledger, filtered
    GET    /organizations/{oid}/helm/books/{bid}/calendar             money due per day (the cockpit's calendar)
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
    GET    /organizations/{oid}/helm/backtests/{tid}/pdf              the replay as a PDF report (audited export)

Roles: owners and admins decide; the billing role sees everything and approves payment actions;
members get 403 (their Helm tasks are in the review hub). Writes need `capability.helm` (402
CAPABILITY_REQUIRED otherwise), except on the sample company. Read-only billing states and an ended
trial refuse writes before the route runs (`get_organization_context`).
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
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


class ChainTierIn(BaseModel):
    """One tier of an approval chain: above `above_micros` in `currency`, `approvals` different people
    (2 or 3), from `roles` when given. The service checks the values and says what is wrong in words."""

    model_config = ConfigDict(extra="forbid")

    above_micros: int = Field(..., ge=0)
    currency: str = Field(..., min_length=1, max_length=8)
    approvals: int = Field(..., ge=0, le=9)
    roles: Optional[list[str]] = Field(default=None, max_length=8)


class ApprovalChainIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: An empty list removes the chain.
    tiers: list[ChainTierIn] = Field(default_factory=list, max_length=12)


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
    approval_chain: Optional[ApprovalChainIn] = None


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


class BankAccountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    #: Any form of the number; only its last four characters are kept.
    number: Optional[str] = Field(default=None, max_length=64)
    currency: str = Field(..., min_length=3, max_length=3)


class BankAccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    mapping: Optional[dict[str, Any]] = None


class BankLineMatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_item_id: uuid.UUID


class BankLineIgnoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Optional[str] = Field(default=None, max_length=300)


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


class WhatIfIn(BaseModel):
    """A scenario. Values are deliberately loose here: `helm/what_if.parse` checks each type and range
    and refuses in plain words ("The delay is a whole number of days."), not in a validator's."""

    model_config = ConfigDict(extra="forbid")

    #: A one-off change to today's cash: a signed whole number of millionths of the currency.
    cash_change_micros: Optional[Any] = None
    #: The cash change's currency; may be left out when the book holds cash in one currency.
    currency: Optional[Any] = None
    #: Customers pay this many days later than usual (0 to 90).
    delay_receivables_days: Optional[Any] = None
    #: Invoices we owe (their promise ids) to hold out of the plan.
    hold_promise_ids: Optional[list[Any]] = Field(default=None, max_length=200)
    #: How far ahead to compare the flight paths (7 to 90 days; the cockpit's 90 when left out).
    horizon_days: Optional[Any] = None


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


@router.get(f"{P}/workspaces/{{workspace_id}}/card")
def helm_workspace_card(organization_id: uuid.UUID, workspace_id: uuid.UUID, db: Session = Depends(get_db),
                        context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    """The workspace Overview's Helm card: owners, admins and billing only, like Helm itself."""
    who = _viewer(db, context)
    return service.workspace_card(db, who, workspace_id)


@router.get(f"{P}/library")
def helm_library(organization_id: uuid.UUID, db: Session = Depends(get_db),
                 context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    _viewer(db, context)
    enterprise = gate.custom_held(db, context.organization_id)
    from app.services.helm.mandates.base import GROUPS

    # First run (brief §10.3): each kind of business in plain words, with the mandates recommended for it.
    business_types = [{"key": key, "label": label, "recommended": registry.recommended(key)}
                      for key, label in v.BUSINESS_TYPES.items()]
    return {"groups": list(GROUPS), "templates": registry.library(enterprise=enterprise),
            "business_types": business_types}


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


@router.post(f"{P}/books/{{book_id}}/what-if")
def helm_what_if(organization_id: uuid.UUID, book_id: uuid.UUID, payload: WhatIfIn, db: Session = Depends(get_db),
                 context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    """Today's plan and a scenario's, side by side: both flight paths, both chances of staying above
    the cash floor, the mandates whose state changes and a summary in words. Nothing is saved."""
    from app.services.helm import what_if

    who = _viewer(db, context)
    result = what_if.run(db, who, service.book_for(db, who, book_id), payload.model_dump())
    db.rollback()  # nothing a what-if read is kept; this also releases the ledger's shared locks
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
                due_on: Optional[date] = Query(None), limit: int = Query(200, ge=1, le=500),
                offset: int = Query(0, ge=0),
                db: Session = Depends(get_db),
                context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    who = _viewer(db, context)
    for value, allowed in ((kind, v.PROMISE_KINDS), (direction, v.DIRECTIONS), (status, v.PROMISE_STATUSES)):
        if value is not None and value not in allowed:
            from app.services.helm.errors import HelmInvalid

            raise HelmInvalid(f"Unknown filter value {value!r}.", reason="FILTER")
    return service.ledger_view(db, who, service.book_for(db, who, book_id), kind=kind, direction=direction,
                               status=status, counterparty=counterparty, at_risk=at_risk, due_on=due_on,
                               limit=limit, offset=offset)


@router.get(f"{P}/books/{{book_id}}/calendar")
def helm_promise_calendar(organization_id: uuid.UUID, book_id: uuid.UUID, start: Optional[date] = Query(None),
                          days: int = Query(84, ge=1, le=366), db: Session = Depends(get_db),
                          context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    """The cockpit's Promise calendar: money due per day (read-only)."""
    who = _viewer(db, context)
    return service.promise_calendar(db, who, service.book_for(db, who, book_id), start=start or date.today(),
                                    days=days)


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


# ---------------------------------------------------------------------------
# Bank statements (owner addition A)
# ---------------------------------------------------------------------------


def _statement_bytes(file: UploadFile) -> tuple[str, bytes]:
    from app.services.file_validation_service import FileValidationError, spool_upload_file
    from app.services.helm import bank_parse
    from app.services.helm.errors import HelmInvalid

    try:
        handle, _size = spool_upload_file(file, max_bytes=bank_parse.MAX_BYTES)
    except FileValidationError as exc:
        raise HelmInvalid(f"The file could not be read: {exc}", reason="STATEMENT_UPLOAD")
    try:
        handle.seek(0)
        return (file.filename or "statement")[:255], handle.read()
    finally:
        handle.close()


def _mapping_form(raw: Optional[str]) -> Optional[dict[str, Any]]:
    from app.services.helm.errors import HelmInvalid

    if not raw:
        return None
    try:
        found = json.loads(raw)
    except ValueError:
        raise HelmInvalid("The column mapping is not readable.", reason="MAPPING_JSON")
    if not isinstance(found, dict):
        raise HelmInvalid("The column mapping is not readable.", reason="MAPPING_JSON")
    return found


@router.get(f"{P}/books/{{book_id}}/bank-accounts")
def helm_bank_accounts(organization_id: uuid.UUID, book_id: uuid.UUID, db: Session = Depends(get_db),
                       context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    from app.services.helm import bank

    who = _viewer(db, context)
    return {"accounts": bank.list_accounts(db, who, service.book_for(db, who, book_id))}


@router.post(f"{P}/books/{{book_id}}/bank-accounts", status_code=201)
def helm_create_bank_account(organization_id: uuid.UUID, book_id: uuid.UUID, payload: BankAccountIn,
                             db: Session = Depends(get_db),
                             context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    from app.services.helm import bank

    who = _viewer(db, context)
    result = bank.create_account(db, who, service.book_for(db, who, book_id), name=payload.name,
                                 number=payload.number, currency=payload.currency)
    db.commit()
    return result


@router.patch(f"{P}/books/{{book_id}}/bank-accounts/{{account_id}}")
def helm_update_bank_account(organization_id: uuid.UUID, book_id: uuid.UUID, account_id: uuid.UUID,
                             payload: BankAccountPatch, db: Session = Depends(get_db),
                             context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    from app.services.helm import bank

    who = _viewer(db, context)
    result = bank.update_account(db, who, service.book_for(db, who, book_id), account_id, name=payload.name,
                                 mapping=payload.mapping)
    db.commit()
    return result


@router.post(f"{P}/books/{{book_id}}/bank-statements/preview")
def helm_preview_bank_statement(organization_id: uuid.UUID, book_id: uuid.UUID, file: UploadFile = File(...),
                                account_id: Optional[uuid.UUID] = Form(default=None),
                                mapping: Optional[str] = Form(default=None), db: Session = Depends(get_db),
                                context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    """The file's columns, a few lines read with the mapping (the person's, the account's saved one,
    else a guess) and that mapping to confirm. Nothing is saved."""
    from app.services.helm import bank

    who = _viewer(db, context)
    filename, data = _statement_bytes(file)
    return bank.preview(db, who, service.book_for(db, who, book_id), filename=filename, data=data,
                        account_id=account_id, mapping=_mapping_form(mapping))


@router.post(f"{P}/books/{{book_id}}/bank-statements", status_code=201)
def helm_import_bank_statement(organization_id: uuid.UUID, book_id: uuid.UUID, file: UploadFile = File(...),
                               account_id: uuid.UUID = Form(...), mapping: Optional[str] = Form(default=None),
                               save_mapping: bool = Form(default=True), db: Session = Depends(get_db),
                               context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    """Import a CSV or Excel statement: new lines, the closing balance as cash, and the matches."""
    from app.services.helm import bank

    who = _viewer(db, context)
    filename, data = _statement_bytes(file)
    result = bank.import_statement(db, who, service.book_for(db, who, book_id), account_id=account_id,
                                   filename=filename, data=data, mapping=_mapping_form(mapping),
                                   save_mapping=save_mapping)
    db.commit()
    return result


@router.get(f"{P}/books/{{book_id}}/bank-imports")
def helm_bank_imports(organization_id: uuid.UUID, book_id: uuid.UUID, db: Session = Depends(get_db),
                      context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    from app.services.helm import bank

    who = _viewer(db, context)
    return {"imports": bank.list_imports(db, who, service.book_for(db, who, book_id))}


@router.get(f"{P}/books/{{book_id}}/bank-lines")
def helm_bank_lines(organization_id: uuid.UUID, book_id: uuid.UUID,
                    status: Optional[str] = Query(default=None, max_length=10),
                    account_id: Optional[uuid.UUID] = Query(default=None), db: Session = Depends(get_db),
                    context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    from app.services.helm import bank

    who = _viewer(db, context)
    return bank.list_lines(db, who, service.book_for(db, who, book_id), status=status, account_id=account_id)


@router.post(f"{P}/bank-lines/{{line_id}}/match")
def helm_match_bank_line(organization_id: uuid.UUID, line_id: uuid.UUID, payload: BankLineMatchIn,
                         db: Session = Depends(get_db),
                         context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    from app.services.helm import bank

    who = _viewer(db, context)
    result = bank.confirm(db, who, line_id, payload.work_item_id)
    db.commit()
    return result


@router.post(f"{P}/bank-lines/{{line_id}}/ignore")
def helm_ignore_bank_line(organization_id: uuid.UUID, line_id: uuid.UUID, payload: BankLineIgnoreIn,
                          db: Session = Depends(get_db),
                          context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    from app.services.helm import bank

    who = _viewer(db, context)
    result = bank.ignore(db, who, line_id, payload.note)
    db.commit()
    return result


@router.post(f"{P}/bank-lines/{{line_id}}/reopen")
def helm_reopen_bank_line(organization_id: uuid.UUID, line_id: uuid.UUID, db: Session = Depends(get_db),
                          context: OrganizationContext = Depends(RequireAnyOrgRole)) -> dict[str, Any]:
    from app.services.helm import bank

    who = _viewer(db, context)
    result = bank.reopen(db, who, line_id)
    db.commit()
    return result


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


@router.get(f"{P}/backtests/{{backtest_id}}/pdf")
def helm_backtest_pdf(organization_id: uuid.UUID, backtest_id: uuid.UUID, db: Session = Depends(get_db),
                      context: OrganizationContext = Depends(RequireAnyOrgRole)) -> Response:
    """The finished replay as a PDF report: the same access as reading it; 409 in words while it runs or
    after it failed. The same replay always gives the same bytes."""
    who = _viewer(db, context)
    report = service.backtest_pdf(db, who, backtest_id)
    db.commit()  # the export's audit row
    return Response(content=report["content"], media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{report["filename"]}"',
                             "X-Helm-Result-SHA256": report["sha256"]})


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
