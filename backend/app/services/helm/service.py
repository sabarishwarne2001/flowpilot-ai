"""Helm's application services: what the API calls (books, settings, mandates, the cockpit, the
ledger, actions, certificates, the payment run). Each function takes a `Viewer` (who is asking, in
which role, which workspaces they may read) and returns plain dicts the API serialises.

READS FOLLOW TODAY'S RULES EXACTLY
==================================

What a person cannot read in Documents mode they cannot read in Helm. Owners and admins read every
workspace; a Billing member reads Helm's figures, but a document's facts only in the workspaces they
hold a grant for (`gate.readable_workspaces`). Anything tied to a document in another workspace is
shown masked, with the workspace's name and a way to ask for access; its contents never leak.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.helm import (
    HelmAction,
    HelmBook,
    HelmCashPosition,
    HelmCertificate,
    HelmCounterparty,
    HelmMandate,
    HelmMandateVersion,
    HelmPlan,
    HelmPromise,
    HelmSettlement,
)
from app.models.workspace import Workspace, WorkspaceStatus
from app.services.helm import certificate as certs
from app.services.helm import gate, identity, ledger_store, money, snapshot_store
from app.services.helm import vocabulary as v
from app.services.helm.errors import HelmError, HelmForbidden, HelmInvalid, HelmNotFound
from app.services.helm.mandates import registry
from app.services.helm.mandates.spec import MandateSpec

MASK = "hidden"


@dataclass(frozen=True)
class Viewer:
    organization_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    readable: frozenset[uuid.UUID] = field(default_factory=frozenset)
    helm: bool = False

    def can_read(self, workspace_id: Optional[uuid.UUID]) -> bool:
        return workspace_id is None or workspace_id in self.readable

    @property
    def decides(self) -> bool:
        return gate.can_decide(self.role)


def viewer(db: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID, role: Any) -> Viewer:
    role = str(getattr(role, "value", role)).upper()
    if not gate.can_view(role):
        raise HelmForbidden("Helm is for organization owners, admins and the billing role. Your Helm tasks are in "
                            "the review hub.", reason="HELM_ROLE")
    return Viewer(organization_id=organization_id, user_id=user_id, role=role,
                  readable=frozenset(gate.readable_workspaces(db, organization_id=organization_id, user_id=user_id,
                                                              role=role)),
                  helm=gate.helm_held(db, organization_id))


def require_decider(who: Viewer) -> None:
    if not who.decides:
        raise HelmForbidden("Only organization owners and admins change Helm. The billing role can see everything and "
                            "approve payment actions.", reason="HELM_DECIDER_ROLE")


def require_helm(who: Viewer, book: Optional[HelmBook] = None) -> None:
    """Writes need `capability.helm`; the sample company is the exception (preview opens and explores it)."""
    if who.helm or (book is not None and book.kind == v.BOOK_SAMPLE):
        return
    from app.core.billing_errors import CapabilityRequiredError

    raise CapabilityRequiredError(
        "Helm is included on the Business and Enterprise plans. Your plan includes Helm preview: the sample "
        "company and one backtest a week on your own documents.",
        details={"reason": "capability_required", "capability": "capability.helm", "remedy": "UPGRADE_PLAN",
                 "preview": True})


# ---------------------------------------------------------------------------
# Books
# ---------------------------------------------------------------------------


def _book(db: Session, who: Viewer, book_id: uuid.UUID) -> HelmBook:
    book = db.get(HelmBook, book_id)
    if book is None or book.organization_id != who.organization_id or book.status != v.BOOK_ACTIVE:
        raise HelmNotFound("Helm book not found.")
    return book


def book_for(db: Session, who: Viewer, book_id: uuid.UUID) -> HelmBook:
    return _book(db, who, book_id)


def _workspace_name(db: Session, workspace_id: Optional[uuid.UUID]) -> Optional[str]:
    if workspace_id is None:
        return None
    ws = db.get(Workspace, workspace_id)
    return ws.workspace_name if ws else None


def book_json(db: Session, who: Viewer, book: HelmBook) -> dict[str, Any]:
    settings = snapshot_store.settings_for(db, book)
    readable = who.can_read(book.workspace_id) if book.kind != v.BOOK_ORGANIZATION else True
    plan = db.execute(select(HelmPlan.id, HelmPlan.version, HelmPlan.created_at, HelmPlan.solver_status)
                      .where(HelmPlan.book_id == book.id, HelmPlan.status == v.PLAN_CURRENT)).first()
    return {
        "id": str(book.id), "kind": book.kind, "name": book.name,
        "workspace_id": str(book.workspace_id) if book.workspace_id else None,
        "workspace_name": _workspace_name(db, book.workspace_id), "readable": readable,
        "sample": book.kind == v.BOOK_SAMPLE, "read_only": not (who.helm or book.kind == v.BOOK_SAMPLE),
        "first_run": {"step": settings.first_run_step, "completed_at": settings.first_run_completed_at.isoformat()
                      if settings.first_run_completed_at else None},
        "plan": {"id": str(plan.id), "version": plan.version, "created_at": plan.created_at.isoformat(),
                 "status": plan.solver_status} if plan else None,
        "created_at": book.created_at.isoformat() if book.created_at else None,
    }


def list_books(db: Session, who: Viewer) -> list[dict[str, Any]]:
    books = db.execute(select(HelmBook).where(HelmBook.organization_id == who.organization_id,
                                              HelmBook.status == v.BOOK_ACTIVE)
                       .order_by(HelmBook.kind == v.BOOK_SAMPLE, HelmBook.created_at)).scalars().all()
    return [book_json(db, who, b) for b in books]


def create_book(db: Session, who: Viewer, *, workspace_id: Optional[uuid.UUID], name: Optional[str] = None,
                kind: str = v.BOOK_WORKSPACE, preview: bool = False) -> HelmBook:
    require_decider(who)
    if not preview:
        require_helm(who)
    if kind == v.BOOK_ORGANIZATION:
        if not gate.all_workspaces_held(db, who.organization_id):
            from app.core.billing_errors import CapabilityRequiredError

            raise CapabilityRequiredError("Planning across every workspace is part of the Enterprise plan.",
                                          details={"capability": "capability.helm_all_workspaces",
                                                   "remedy": "UPGRADE_PLAN"})
        workspace = None
    else:
        workspace = db.get(Workspace, workspace_id) if workspace_id else None
        if workspace is None or workspace.organization_id != who.organization_id:
            raise HelmNotFound("Workspace not found.")
        if workspace.status is not WorkspaceStatus.ACTIVE:
            raise HelmInvalid("An archived workspace cannot get a Helm book.", reason="WORKSPACE_NOT_ACTIVE")
        if workspace.is_sample and kind != v.BOOK_SAMPLE:
            raise HelmInvalid("The sample workspace already has its own Helm book.", reason="SAMPLE_WORKSPACE")
    existing = db.execute(select(HelmBook).where(
        HelmBook.organization_id == who.organization_id, HelmBook.status == v.BOOK_ACTIVE,
        (HelmBook.workspace_id == workspace.id) if workspace is not None else (HelmBook.kind == v.BOOK_ORGANIZATION),
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    book = HelmBook(organization_id=who.organization_id, workspace_id=workspace.id if workspace else None, kind=kind,
                    name=(name or (workspace.workspace_name if workspace else "All workspaces"))[:200],
                    status=v.BOOK_ACTIVE, created_by_user_id=who.user_id)
    db.add(book)
    db.flush()
    settings = snapshot_store.settings_for(db, book)
    settings.identities = suggest_identities(db, book)
    db.flush()
    _audit(db, who, book, "HELM_BOOK", book.id, "CREATED", {"kind": kind, "workspace_id": str(book.workspace_id)
                                                             if book.workspace_id else None})
    for wid in ledger_store.book_workspace_ids(db, book):
        ledger_store.enqueue_rebuild(db, workspace_id=wid, organization_id=book.organization_id, reason="book created")
    return book


def suggest_identities(db: Session, book: HelmBook) -> list[dict[str, Any]]:
    from app.models.document_role import DocumentRole
    from app.models.organization import Organization
    from app.models.work_item import WorkItem

    org = db.get(Organization, book.organization_id)
    workspace_ids = ledger_store.book_workspace_ids(db, book)
    entities = []
    if workspace_ids:
        entities = [e for (e,) in db.execute(
            select(WorkItem.extracted_entities).join(DocumentRole, DocumentRole.work_item_id == WorkItem.id)
            .where(WorkItem.workspace_id.in_(workspace_ids), DocumentRole.role == "INVOICE").limit(500)
        ).all() if isinstance(e, dict)]
    legal = getattr(org, "legal_name", None) or getattr(org, "name", None)
    if book.kind == v.BOOK_SAMPLE:
        legal = None
    return identity.suggest(organization_legal_name=legal, invoice_entities=entities)


def _audit(db: Session, who: Viewer, book: HelmBook, resource_type: str, resource_id: Any, verb: str,
           details: Optional[dict[str, Any]] = None) -> None:
    from app.services import audit_service

    with db.begin_nested():
        audit_service.record(db, organization_id=who.organization_id, workspace_id=book.workspace_id,
                             actor_id=who.user_id, resource_type=resource_type,
                             resource_id=resource_id if isinstance(resource_id, uuid.UUID) else None,
                             action=verb, details={"book_id": str(book.id), **(details or {})})


# ---------------------------------------------------------------------------
# Settings, cash, counterparties
# ---------------------------------------------------------------------------


def settings_json(db: Session, who: Viewer, book: HelmBook) -> dict[str, Any]:
    settings = snapshot_store.settings_for(db, book)
    cash = snapshot_store.cash_positions(db, book, ledger_store.book_workspace_ids(db, book),
                                         datetime.now(timezone.utc).date())
    evidence_visible = who.can_read(book.workspace_id) if book.workspace_id else who.decides
    return {
        "identities": settings.identities or [],
        "identities_confirmed_at": settings.identities_confirmed_at.isoformat()
        if settings.identities_confirmed_at else None,
        "run_weekdays": list(settings.run_weekdays or []), "horizon_days": settings.horizon_days,
        "cost_of_capital_pct": settings.cost_of_capital_bps / 100, "default_autonomy": settings.default_autonomy,
        "briefing_enabled": settings.briefing_enabled, "business_type": settings.business_type,
        "first_run_step": settings.first_run_step,
        "cash": [{"currency": c.currency, "amount_micros": c.amount_micros, "as_of": c.as_of.isoformat(),
                  "source": c.source, "age_days": c.age_days,
                  "evidence": c.evidence if evidence_visible else {}}
                 for c in cash.values()],
    }


def update_settings(db: Session, who: Viewer, book: HelmBook, changes: dict[str, Any]) -> dict[str, Any]:
    require_decider(who)
    require_helm(who, book)
    settings = snapshot_store.settings_for(db, book)
    now = datetime.now(timezone.utc)
    if "identities" in changes and changes["identities"] is not None:
        cleaned = []
        for item in changes["identities"]:
            name = (item.get("name") or "").strip()[:300] or None
            tax = (item.get("tax_id") or "").strip().upper()[:64] or None
            if not name and not tax:
                continue
            cleaned.append({"name": name, "tax_id": tax, "tax_kind": item.get("tax_kind")
                            or ("GSTIN" if tax and len(tax) == 15 else ("TAXID" if tax else None)),
                            "source": "PERSON"})
        if not cleaned:
            raise HelmInvalid("Helm needs at least one legal name or tax id to tell sales from purchases.",
                              reason="IDENTITIES_EMPTY")
        settings.identities = cleaned
        settings.identities_confirmed_at = now
        settings.identities_confirmed_by_user_id = who.user_id
    if changes.get("run_weekdays") is not None:
        days = sorted({int(d) for d in changes["run_weekdays"]})
        if not days or any(d < 1 or d > 7 for d in days):
            raise HelmInvalid("Payment-run days are weekdays, Monday (1) to Sunday (7), and at least one.",
                              reason="RUN_DAYS")
        settings.run_weekdays = days
    if changes.get("horizon_days") is not None:
        horizon = int(changes["horizon_days"])
        if not 7 <= horizon <= 90:
            raise HelmInvalid("The planning horizon is between 7 and 90 days.", reason="HORIZON")
        settings.horizon_days = horizon
    if changes.get("cost_of_capital_pct") is not None:
        pct = float(changes["cost_of_capital_pct"])
        if not 0 <= pct <= 100:
            raise HelmInvalid("The cost of capital is a yearly percentage between 0 and 100.", reason="COST")
        settings.cost_of_capital_bps = int(round(pct * 100))
    if changes.get("default_autonomy") is not None:
        if changes["default_autonomy"] not in v.AUTONOMY_MODES:
            raise HelmInvalid("Autonomy is watch, propose or act.", reason="AUTONOMY")
        settings.default_autonomy = changes["default_autonomy"]
    if changes.get("briefing_enabled") is not None:
        settings.briefing_enabled = bool(changes["briefing_enabled"])
    if changes.get("business_type") is not None:
        settings.business_type = str(changes["business_type"])[:32] or None
    if changes.get("first_run_step") is not None:
        settings.first_run_step = max(int(settings.first_run_step or 0), min(4, int(changes["first_run_step"])))
        if settings.first_run_step >= 4 and settings.first_run_completed_at is None:
            settings.first_run_completed_at = now
    settings.updated_by_user_id = who.user_id
    settings.updated_at = now
    db.flush()
    _audit(db, who, book, "HELM_SETTINGS", None, "UPDATED", {"fields": sorted(k for k, val in changes.items()
                                                                              if val is not None)})
    if "identities" in changes:
        for wid in ledger_store.book_workspace_ids(db, book):
            ledger_store.enqueue_rebuild(db, workspace_id=wid, organization_id=book.organization_id,
                                         reason="identities changed")
    _replan(db, book)
    return settings_json(db, who, book)


def add_cash_position(db: Session, who: Viewer, book: HelmBook, *, currency: str, amount_micros: int, as_of: date,
                      note: Optional[str] = None) -> dict[str, Any]:
    if who.role not in ("OWNER", "ADMIN", "BILLING"):
        raise HelmForbidden("Only owners, admins and the billing role enter cash positions.", reason="HELM_ROLE")
    require_helm(who, book)
    currency = (currency or "").upper()
    if len(currency) != 3 or not currency.isalpha():
        raise HelmInvalid("A currency is three letters, such as INR.", reason="CURRENCY")
    if as_of > datetime.now(timezone.utc).date():
        raise HelmInvalid("A cash position cannot be dated in the future.", reason="CASH_DATE")
    row = HelmCashPosition(organization_id=book.organization_id, book_id=book.id, currency=currency,
                           amount_micros=int(amount_micros), as_of=as_of, source=v.CASH_SOURCE_MANUAL,
                           note=(note or None) and note[:300], entered_by_user_id=who.user_id)
    db.add(row)
    db.flush()
    _audit(db, who, book, "HELM_SETTINGS", row.id, "CREATED", {"cash_currency": currency, "as_of": as_of.isoformat()})
    _replan(db, book)
    return {"id": str(row.id), "currency": currency, "amount_micros": row.amount_micros, "as_of": as_of.isoformat()}


def list_counterparties(db: Session, who: Viewer, book: HelmBook) -> list[dict[str, Any]]:
    readable = who.can_read(book.workspace_id) if book.workspace_id else True
    rows = db.execute(select(HelmCounterparty).where(HelmCounterparty.book_id == book.id)
                      .order_by(HelmCounterparty.display_name)).scalars().all()
    out = []
    for row in rows:
        from app.services.helm import msme

        status = msme.MsmeStatus(registered=bool(row.udyam_number), category=row.msme_category,
                                 confirmed=row.msme_source == v.MSME_SOURCE_PERSON, udyam_number=row.udyam_number)
        out.append({
            "id": str(row.id), "key": row.key if readable else MASK, "name": row.display_name if readable else MASK,
            "role": row.role, "udyam_number": row.udyam_number if readable else None,
            "msme_category": row.msme_category, "msme_source": row.msme_source, "msme_label": status.label,
            "msme_protected": status.protected, "contact_email": row.contact_email if readable else None,
            "behaviour": row.behaviour if readable else {},
        })
    return out


def update_counterparty(db: Session, who: Viewer, book: HelmBook, counterparty_id: uuid.UUID,
                        changes: dict[str, Any]) -> dict[str, Any]:
    require_decider(who)
    require_helm(who, book)
    row = db.get(HelmCounterparty, counterparty_id)
    if row is None or row.book_id != book.id:
        raise HelmNotFound("Counterparty not found.")
    now = datetime.now(timezone.utc)
    if "msme_category" in changes:
        category = changes["msme_category"]
        if category is not None and category not in v.MSME_CATEGORIES:
            raise HelmInvalid("The MSME category is MICRO, SMALL, MEDIUM or NONE.", reason="MSME_CATEGORY")
        row.msme_category = category
        row.msme_source = v.MSME_SOURCE_PERSON if category else (v.MSME_SOURCE_DOCUMENT if row.udyam_number else None)
        row.msme_confirmed_by_user_id = who.user_id if category else None
        row.msme_confirmed_at = now if category else None
    if "contact_email" in changes:
        email = (changes["contact_email"] or "").strip().lower() or None
        if email and ("@" not in email or len(email) > 320):
            raise HelmInvalid("That is not an email address.", reason="EMAIL")
        row.contact_email = email
    if "contact_name" in changes:
        row.contact_name = (changes["contact_name"] or "").strip()[:200] or None
    row.updated_at = now
    db.flush()
    _audit(db, who, book, "HELM_SETTINGS", row.id, "UPDATED", {"counterparty": row.key,
                                                                 "fields": sorted(changes)})
    _replan(db, book)
    return next(c for c in list_counterparties(db, who, book) if c["id"] == str(row.id))


def _replan(db: Session, book: HelmBook) -> None:
    from app.services.helm import plan_store

    plan_store.enqueue(db, book, triggered_by=v.TRIGGER_PERSON, delay_s=2)


# ---------------------------------------------------------------------------
# Mandates
# ---------------------------------------------------------------------------


def _facts(db: Session, book: HelmBook) -> registry.BookFacts:
    from app.services.helm import plan_store

    workspace_ids = ledger_store.book_workspace_ids(db, book)
    keys = set(db.execute(select(HelmCounterparty.key).where(HelmCounterparty.book_id == book.id)).scalars())
    currencies = set(c for (c,) in db.execute(
        select(HelmPromise.currency).where(HelmPromise.workspace_id.in_(workspace_ids or [uuid.uuid4()]),
                                           HelmPromise.currency.is_not(None)).distinct()).all())
    return registry.BookFacts(workspace_ids=frozenset(str(w) for w in workspace_ids),
                              counterparty_keys=frozenset(keys), currencies=frozenset(currencies),
                              active=plan_store.active_mandates(db, book.id),
                              enterprise=gate.custom_held(db, book.organization_id))


def mandate_json(db: Session, mandate: HelmMandate, version: Optional[HelmMandateVersion] = None) -> dict[str, Any]:
    version = version or db.get(HelmMandateVersion, (mandate.id, mandate.current_version))
    template = registry.TEMPLATES.get(mandate.template)
    return {
        "id": str(mandate.id), "book_id": str(mandate.book_id), "template": mandate.template,
        "title": mandate.title, "status": mandate.status, "version": mandate.current_version,
        "spec": version.spec if version else None, "readback": json.loads(version.readback) if version else None,
        "kind": template.kind if template else None, "group": template.group if template else None,
        "is_limit": template.is_limit if template else False,
        "law_links": list(template.law_links) if template else [], "disclaimer": template.disclaimer if template
        else None, "activated_at": mandate.activated_at.isoformat() if mandate.activated_at else None,
        "created_at": mandate.created_at.isoformat() if mandate.created_at else None,
    }


def list_mandates(db: Session, who: Viewer, book: HelmBook) -> list[dict[str, Any]]:
    rows = db.execute(select(HelmMandate).where(HelmMandate.book_id == book.id,
                                                HelmMandate.status != v.MANDATE_ARCHIVED)
                      .order_by(HelmMandate.created_at)).scalars().all()
    return [mandate_json(db, m) for m in rows]


def _spec_from(db: Session, book: HelmBook, raw: dict[str, Any], *, status: str, owner: uuid.UUID) -> MandateSpec:
    template = registry.template(str(raw.get("template") or ""))
    settings = snapshot_store.settings_for(db, book)
    autonomy = raw.get("autonomy") or {"mode": settings.default_autonomy if settings.default_autonomy != "act"
                                       else v.AUTONOMY_PROPOSE}
    spec = registry.parse_spec({
        "template": template.key, "kind": template.kind, "scope": raw.get("scope") or {},
        "params": raw.get("params") or registry.defaults_for(template.key), "autonomy": autonomy,
        "owner_user_id": str(owner), "status": status.lower(),
    })
    return registry.normalized(spec)


def readback_of(spec: MandateSpec) -> dict[str, Any]:
    template = registry.template(spec.template)
    return template.readback(spec, registry.params_of(spec)).as_json()


def preview_spec(db: Session, who: Viewer, book: HelmBook, raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and read back a spec without saving it (the composer and the gallery)."""
    try:
        spec = _spec_from(db, book, raw, status=v.MANDATE_DRAFT, owner=who.user_id)
    except registry.MandateError as exc:
        return {"valid": False, "problems": exc.problems, "spec": None, "readback": None}
    problems = registry.validate(spec, _facts(db, book))
    return {"valid": not problems, "problems": problems, "spec": spec.as_json(), "readback": readback_of(spec)}


def create_mandate(db: Session, who: Viewer, book: HelmBook, raw: dict[str, Any], *, activate: bool = False) -> \
        dict[str, Any]:
    require_decider(who)
    require_helm(who, book)
    try:
        spec = _spec_from(db, book, raw, status=v.MANDATE_DRAFT, owner=who.user_id)
    except registry.MandateError as exc:
        raise HelmInvalid("; ".join(exc.problems), reason="MANDATE_INVALID", details={"problems": exc.problems})
    _check_act_cap(who, spec)
    problems = registry.validate(spec, _facts(db, book))
    if problems:
        raise HelmInvalid(" ".join(problems), reason="MANDATE_INVALID", details={"problems": problems})
    template = registry.template(spec.template)
    mandate = HelmMandate(organization_id=book.organization_id, book_id=book.id, template=spec.template,
                          status=v.MANDATE_DRAFT, current_version=1,
                          title=(raw.get("title") or template.title)[:200], created_by_user_id=who.user_id)
    db.add(mandate)
    db.flush()
    _write_version(db, who, mandate, spec, 1)
    _audit(db, who, book, "HELM_MANDATE", mandate.id, "CREATED", {"template": spec.template, "version": 1})
    if activate:
        return set_status(db, who, book, mandate.id, v.MANDATE_ACTIVE)
    return mandate_json(db, mandate)


def _check_act_cap(who: Viewer, spec: MandateSpec) -> None:
    cap = spec.autonomy.act_cap
    if spec.autonomy.mode == v.AUTONOMY_ACT and cap is not None and cap.amount_micros > 0 and \
            who.role not in ("OWNER", "ADMIN"):
        raise HelmForbidden("Only an organization owner or admin sets an act cap.", reason="ACT_CAP_ROLE")


def _write_version(db: Session, who: Viewer, mandate: HelmMandate, spec: MandateSpec, version: int) -> None:
    db.add(HelmMandateVersion(mandate_id=mandate.id, version=version, organization_id=mandate.organization_id,
                              spec=spec.as_json(), spec_hash=registry.spec_hash(spec),
                              readback=json.dumps(readback_of(spec), sort_keys=True), created_by_user_id=who.user_id))
    db.flush()


def _mandate(db: Session, book: HelmBook, mandate_id: uuid.UUID) -> HelmMandate:
    mandate = db.get(HelmMandate, mandate_id)
    if mandate is None or mandate.book_id != book.id:
        raise HelmNotFound("Mandate not found.")
    return mandate


def update_mandate(db: Session, who: Viewer, book: HelmBook, mandate_id: uuid.UUID, raw: dict[str, Any]) -> \
        dict[str, Any]:
    """Editing writes a new immutable version; plans and certificates keep naming the old one."""
    require_decider(who)
    require_helm(who, book)
    mandate = _mandate(db, book, mandate_id)
    if mandate.status == v.MANDATE_ARCHIVED:
        raise HelmInvalid("An archived mandate cannot be edited.", reason="MANDATE_ARCHIVED")
    current = db.get(HelmMandateVersion, (mandate.id, mandate.current_version))
    merged = dict(current.spec)
    for key in ("scope", "params", "autonomy"):
        if raw.get(key) is not None:
            merged[key] = raw[key]
    merged["template"] = mandate.template
    try:
        spec = _spec_from(db, book, merged, status=mandate.status, owner=who.user_id)
    except registry.MandateError as exc:
        raise HelmInvalid("; ".join(exc.problems), reason="MANDATE_INVALID", details={"problems": exc.problems})
    _check_act_cap(who, spec)
    problems = registry.validate(spec, _facts(db, book), ignore_mandate_id=str(mandate.id))
    if problems:
        raise HelmInvalid(" ".join(problems), reason="MANDATE_INVALID", details={"problems": problems})
    if registry.spec_hash(spec) == current.spec_hash and not raw.get("title"):
        return mandate_json(db, mandate)
    version = mandate.current_version + 1
    _write_version(db, who, mandate, spec, version)
    mandate.current_version = version
    if raw.get("title"):
        mandate.title = str(raw["title"])[:200]
    mandate.updated_at = datetime.now(timezone.utc)
    db.flush()
    _audit(db, who, book, "HELM_MANDATE", mandate.id, "UPDATED", {"version": version})
    _replan(db, book)
    return mandate_json(db, mandate)


def set_status(db: Session, who: Viewer, book: HelmBook, mandate_id: uuid.UUID, status: str) -> dict[str, Any]:
    require_decider(who)
    require_helm(who, book)
    mandate = _mandate(db, book, mandate_id)
    if status not in (v.MANDATE_ACTIVE, v.MANDATE_PAUSED, v.MANDATE_ARCHIVED):
        raise HelmInvalid("A mandate is activated, paused or archived.", reason="MANDATE_STATUS")
    if mandate.status == status:
        return mandate_json(db, mandate)
    if status == v.MANDATE_ACTIVE:
        if book.kind != v.BOOK_SAMPLE:
            gate.assert_mandate_room(db, book.organization_id)
        version = db.get(HelmMandateVersion, (mandate.id, mandate.current_version))
        spec = registry.parse_spec(version.spec)
        problems = registry.validate(spec, _facts(db, book), ignore_mandate_id=str(mandate.id))
        if problems:
            raise HelmInvalid(" ".join(problems), reason="MANDATE_INVALID", details={"problems": problems})
        mandate.activated_at = datetime.now(timezone.utc)
    mandate.status = status
    mandate.updated_at = datetime.now(timezone.utc)
    db.flush()
    _audit(db, who, book, "HELM_MANDATE", mandate.id, {"ACTIVE": "ENABLED", "PAUSED": "DISABLED"}.get(status,
                                                                                                  "ARCHIVED"),
           {"status": status})
    _replan(db, book)
    return mandate_json(db, mandate)


# ---------------------------------------------------------------------------
# The cockpit and the plan
# ---------------------------------------------------------------------------


def action_json(db: Session, who: Viewer, action: HelmAction, *, names: Optional[dict] = None) -> dict[str, Any]:
    visible = who.can_read(action.workspace_id)
    names = names if names is not None else {}
    workspace_name = names.get(action.workspace_id) if action.workspace_id in names else _workspace_name(
        db, action.workspace_id)
    out = {
        "id": str(action.id), "kind": action.kind, "state": action.state, "autonomy": action.autonomy,
        "needs_approval": action.needs_approval, "guard_passed": action.guard_passed,
        "required_approvals": action.required_approvals,
        "approvals": [{"role": a.get("role"), "at": a.get("at"), "by_helm": a.get("by") == "helm"}
                      for a in (action.approvals or [])],
        "mandate_id": str(action.mandate_id) if action.mandate_id else None, "mandate_version": action.mandate_version,
        "workspace_id": str(action.workspace_id) if action.workspace_id else None, "workspace_name": workspace_name,
        "run_date": action.run_date.isoformat() if action.run_date else None, "currency": action.currency,
        "undoable": action.undoable, "irreversible": action.kind in v.IRREVERSIBLE_ACTIONS,
        "certificate_id": str(action.certificate_id) if action.certificate_id else None,
        "rejected_reason": action.rejected_reason, "created_at": action.created_at.isoformat()
        if action.created_at else None, "executed_at": action.executed_at.isoformat() if action.executed_at else None,
        "readable": visible,
    }
    if visible:
        out.update({"title": action.title, "explanation": action.explanation, "amount_micros": action.amount_micros,
                    "value_micros": action.value_micros, "work_item_id": str(action.work_item_id)
                    if action.work_item_id else None, "promise_id": str(action.promise_id) if action.promise_id
                    else None, "counterparty_key": action.counterparty_key,
                    "params": _public_params(action.params or {}), "guard": action.guard,
                    "result": _public_result(action.result or {})})
    else:
        out.update({"title": f"An action on a document in {workspace_name or 'another workspace'}",
                    "explanation": MASK, "amount_micros": None, "value_micros": None, "work_item_id": None,
                    "promise_id": None, "counterparty_key": None, "params": {},
                    "guard": {"passed": action.guard_passed, "needs_approval": action.needs_approval,
                              "checks": [{"id": c.get("id"), "title": c.get("title"), "result": c.get("result"),
                                          "detail": MASK} for c in (action.guard or {}).get("checks", [])]},
                    "result": {}, "ask_access": True})
    return out


def _public_params(params: dict[str, Any]) -> dict[str, Any]:
    return {k: val for k, val in params.items() if k not in ("to",)}


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    return {k: val for k, val in result.items() if k not in ("previous",)}


def cockpit(db: Session, who: Viewer, book: HelmBook) -> dict[str, Any]:
    from app.services.helm import plan_store, value

    plan = plan_store.current_plan(db, book.id)
    readable = who.can_read(book.workspace_id) if book.kind != v.BOOK_ORGANIZATION else True
    actions = db.execute(select(HelmAction).where(
        HelmAction.book_id == book.id,
        HelmAction.state.in_((v.ACTION_PROPOSED, v.ACTION_APPROVED, v.ACTION_EXECUTING, v.ACTION_BLOCKED,
                              v.ACTION_FAILED)))
        .order_by(HelmAction.run_date.nulls_last(), HelmAction.created_at)).scalars().all()
    recent = db.execute(select(HelmAction).where(HelmAction.book_id == book.id,
                                                 HelmAction.state.in_((v.ACTION_EXECUTED, v.ACTION_UNDONE,
                                                                       v.ACTION_REJECTED)))
                        .order_by(HelmAction.updated_at.desc()).limit(20)).scalars().all()
    names = {w.id: w.workspace_name for w in db.execute(
        select(Workspace).where(Workspace.organization_id == who.organization_id)).scalars()}
    summary = (plan.summary or {}) if plan else {}
    if not readable:
        summary = _mask_summary(summary)
    return {
        "book": book_json(db, who, book),
        "plan": {"id": str(plan.id), "version": plan.version, "status": plan.solver_status,
                 "created_at": plan.created_at.isoformat(), "seed": str(plan.seed),
                 "inputs_digest": plan.inputs_digest, "horizon_days": plan.horizon_days,
                 "conflict": plan.conflict if readable else (
                     {"headline": plan.conflict.get("headline"), "options": []} if plan.conflict else None),
                 "duration_ms": plan.duration_ms, "triggered_by": plan.triggered_by,
                 "mandate_versions": plan.mandate_versions} if plan else None,
        "summary": summary,
        "actions": [action_json(db, who, a, names=names) for a in actions],
        "recent": [action_json(db, who, a, names=names) for a in recent],
        "value_held": value.totals(db, book) if readable else None,
        "mandates": list_mandates(db, who, book),
    }


def _mask_summary(summary: dict[str, Any]) -> dict[str, Any]:
    out = dict(summary)
    out["instruments"] = [{**i, "items": [], "detail": MASK} for i in summary.get("instruments") or []]
    out["risks"] = []
    if out.get("forecast"):
        out["forecast"] = {**out["forecast"], "expected_invoices": []}
    if out.get("simulation"):
        out["simulation"] = {**out["simulation"], "expected_invoices": []}
    return out


def replan(db: Session, who: Viewer, book: HelmBook) -> dict[str, Any]:
    from app.services.helm import plan_store

    require_helm(who, book)
    # In the same order as a snapshot takes the ledger locks (snapshot_store.take).
    for wid in sorted(ledger_store.book_workspace_ids(db, book), key=str):
        ledger_store.rebuild_workspace(db, workspace_id=wid)
    plan = plan_store.run(db, book, triggered_by=v.TRIGGER_PERSON, force=True)
    if plan is None:
        raise HelmError("Helm is already planning this book; try again in a moment.", reason="PLAN_IN_PROGRESS")
    return cockpit(db, who, book)


def action_for(db: Session, who: Viewer, action_id: uuid.UUID) -> HelmAction:
    action = db.get(HelmAction, action_id)
    if action is None or action.organization_id != who.organization_id:
        raise HelmNotFound("Action not found.")
    return action


def _actor(who: Viewer, action: HelmAction):
    from app.services.helm.guard import Actor

    return Actor(user_id=str(who.user_id), role=who.role, can_read_workspace=who.can_read(action.workspace_id))


def _required_approvals(db: Session, book: HelmBook, action: HelmAction) -> int:
    """Enterprise approval chains: a second approver above the book's chain amount (preferences)."""
    if not gate.approval_chains_held(db, book.organization_id):
        return 1
    settings = snapshot_store.settings_for(db, book)
    chain = (settings.preferences or {}).get("approval_chain") or {}
    above = chain.get("second_approver_above_micros")
    if above is None or action.kind not in v.PAYMENT_ACTIONS:
        return 1
    if (chain.get("currency") in (None, action.currency)) and int(action.amount_micros or 0) > int(above):
        return 2
    return 1


def approve(db: Session, who: Viewer, action_id: uuid.UUID) -> dict[str, Any]:
    from app.services.helm import executor

    action = action_for(db, who, action_id)
    book = _book(db, who, action.book_id)
    require_helm(who, book)
    try:
        executor.approve(db, action=action, actor=_actor(who, action),
                         required_approvals=_required_approvals(db, book, action))
    except executor.ActionError as exc:
        raise _as_helm_error(exc)
    return action_json(db, who, action)


def approve_all(db: Session, who: Viewer, book: HelmBook) -> dict[str, Any]:
    from app.services.helm import executor

    require_helm(who, book)
    approved, skipped = 0, 0
    for action in db.execute(select(HelmAction).where(HelmAction.book_id == book.id,
                                                      HelmAction.state == v.ACTION_PROPOSED,
                                                      HelmAction.guard_passed.is_(True))
                             .order_by(HelmAction.run_date.nulls_last(), HelmAction.created_at)).scalars().all():
        if not gate.can_approve(who.role, action.kind) or not who.can_read(action.workspace_id):
            skipped += 1
            continue
        try:
            executor.approve(db, action=action, actor=_actor(who, action),
                             required_approvals=_required_approvals(db, book, action))
            approved += 1
        except executor.ActionError:
            skipped += 1
    return {"approved": approved, "skipped": skipped}


def reject(db: Session, who: Viewer, action_id: uuid.UUID, reason: str) -> dict[str, Any]:
    from app.services.helm import executor

    action = action_for(db, who, action_id)
    book = _book(db, who, action.book_id)
    require_helm(who, book)
    try:
        executor.reject(db, action=action, actor=_actor(who, action), reason=reason)
    except executor.ActionError as exc:
        raise _as_helm_error(exc)
    return action_json(db, who, action)


def undo(db: Session, who: Viewer, action_id: uuid.UUID) -> dict[str, Any]:
    from app.services.helm import executor

    action = action_for(db, who, action_id)
    book = _book(db, who, action.book_id)
    require_helm(who, book)
    if not who.can_read(action.workspace_id):
        raise HelmForbidden("You cannot read this document's workspace.", reason="HELM_NO_ACCESS")
    try:
        executor.undo(db, action=action, actor=_actor(who, action))
    except executor.ActionError as exc:
        raise _as_helm_error(exc)
    return action_json(db, who, action)


def _as_helm_error(exc) -> HelmError:
    if exc.status_code == 403:
        return HelmForbidden(exc.message, reason=exc.code)
    if exc.status_code == 422:
        return HelmInvalid(exc.message, reason=exc.code)
    return HelmError(exc.message, reason=exc.code)


def mark_paid(db: Session, who: Viewer, book: HelmBook, work_item_id: uuid.UUID, *, paid_on: date,
              reference: Optional[str]) -> dict[str, Any]:
    """A person records that an invoice was paid (with an optional reference). An action of its own,
    guarded, certified and audited like any other, executed at once."""
    from app.services.helm import executor
    from app.services.helm.guard import Actor

    require_helm(who, book)
    if not gate.can_approve(who.role, v.ACTION_MARK_PAID):
        raise HelmForbidden("Only owners, admins and the billing role record payments.", reason="HELM_ROLE")
    promise = db.execute(select(HelmPromise).where(
        HelmPromise.work_item_id == work_item_id,
        HelmPromise.kind.in_((v.KIND_PAYMENT_DUE, v.KIND_PAYMENT_EXPECTED)),
        HelmPromise.workspace_id.in_(ledger_store.book_workspace_ids(db, book) or [uuid.uuid4()]))).scalar_one_or_none()
    if promise is None:
        raise HelmNotFound("No invoice in this book has that document.")
    if not who.can_read(promise.workspace_id):
        raise HelmForbidden("You cannot read this document's workspace.", reason="HELM_NO_ACCESS")
    if paid_on > datetime.now(timezone.utc).date():
        raise HelmInvalid("A payment cannot be dated in the future.", reason="PAID_DATE")
    key = f"helm:mark_paid:{work_item_id}:{paid_on.isoformat()}:{(reference or '')[:40]}"
    existing = db.execute(select(HelmAction).where(HelmAction.idempotency_key == key)).scalar_one_or_none()
    if existing is not None:
        return action_json(db, who, existing)
    now = datetime.now(timezone.utc)
    action = HelmAction(organization_id=book.organization_id, book_id=book.id, workspace_id=promise.workspace_id,
                        work_item_id=promise.work_item_id, promise_id=promise.id, kind=v.ACTION_MARK_PAID,
                        idempotency_key=key, state=v.ACTION_APPROVED, autonomy=v.AUTONOMY_PROPOSE,
                        needs_approval=False, guard_passed=True, amount_micros=promise.amount_micros,
                        currency=promise.currency, counterparty_key=promise.counterparty_key,
                        title=f"Record {money.say(promise.amount_micros, promise.currency)} paid",
                        explanation=f"Recorded by a person on {now.date().isoformat()}: paid on {paid_on.isoformat()}"
                                    + (f", reference {reference}" if reference else "") + ".",
                        params={"paid_on": paid_on.isoformat(), "reference": reference}, undoable=True,
                        approvals=[{"user_id": str(who.user_id), "role": who.role, "at": now.isoformat()}],
                        decided_by_user_id=who.user_id, decided_at=now)
    db.add(action)
    db.flush()
    executor.execute(db, action_id=action.id, actor=Actor(user_id=str(who.user_id), role=who.role,
                                                          can_read_workspace=True))
    db.refresh(action)
    return action_json(db, who, action)


# ---------------------------------------------------------------------------
# Certificates, the ledger, the payment run
# ---------------------------------------------------------------------------


def certificate_json(db: Session, who: Viewer, certificate_id: uuid.UUID) -> dict[str, Any]:
    row = db.get(HelmCertificate, certificate_id)
    if row is None or row.organization_id != who.organization_id:
        raise HelmNotFound("Certificate not found.")
    body = json.loads(row.canonical)
    action = db.get(HelmAction, row.action_id) if row.action_id else None
    readable = who.can_read(action.workspace_id) if action else True
    workspace_name = _workspace_name(db, action.workspace_id) if action else None
    return {
        "id": str(row.id), "stage": row.stage, "passed": row.passed, "sha256": row.sha256,
        "verified": certs.verify(row.canonical, row.sha256), "created_at": row.created_at.isoformat(),
        "action_id": str(row.action_id) if row.action_id else None, "readable": readable,
        "workspace_name": workspace_name,
        "body": body if readable else certs.masked(body),
        "canonical": row.canonical if readable else None,
    }


def list_certificates(db: Session, who: Viewer, book: HelmBook, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = db.execute(select(HelmCertificate, HelmAction)
                      .join(HelmAction, HelmAction.id == HelmCertificate.action_id, isouter=True)
                      .where(HelmCertificate.book_id == book.id)
                      .order_by(HelmCertificate.created_at.desc()).limit(min(500, max(1, limit)))).all()
    out = []
    for cert, action in rows:
        readable = who.can_read(action.workspace_id) if action else True
        out.append({"id": str(cert.id), "stage": cert.stage, "passed": cert.passed, "sha256": cert.sha256,
                    "created_at": cert.created_at.isoformat(), "action_id": str(cert.action_id) if cert.action_id
                    else None, "action_kind": action.kind if action else None,
                    "title": action.title if (action and readable) else MASK, "readable": readable})
    return out


def promise_json(who: Viewer, promise: HelmPromise, names: dict) -> dict[str, Any]:
    readable = who.can_read(promise.workspace_id)
    base = {"id": str(promise.id), "kind": promise.kind, "direction": promise.direction, "status": promise.status,
            "confidence": promise.confidence, "workspace_id": str(promise.workspace_id),
            "workspace_name": names.get(promise.workspace_id), "readable": readable,
            "due_date": promise.due_date.isoformat() if promise.due_date else None, "currency": promise.currency}
    if not readable:
        return {**base, "title": f"A promise in {names.get(promise.workspace_id) or 'another workspace'}",
                "amount_micros": None, "counterparty_name": None, "counterparty_key": None, "evidence": [],
                "fulfilments": [], "details": {}, "work_item_id": None, "ask_access": True}
    return {**base, "title": promise.title, "amount_micros": promise.amount_micros,
            "counterparty_name": promise.counterparty_name, "counterparty_key": promise.counterparty_key,
            "work_item_id": str(promise.work_item_id) if promise.work_item_id else None,
            "window_end": promise.window_end.isoformat() if promise.window_end else None, "terms": promise.terms,
            "evidence": promise.evidence, "fulfilments": promise.fulfilments, "details": promise.details}


def ledger_view(db: Session, who: Viewer, book: HelmBook, *, kind: Optional[str] = None,
                direction: Optional[str] = None, status: Optional[str] = None, counterparty: Optional[str] = None,
                at_risk: bool = False, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    workspace_ids = ledger_store.book_workspace_ids(db, book)
    query = select(HelmPromise).where(HelmPromise.workspace_id.in_(workspace_ids or [uuid.uuid4()]))
    if kind:
        query = query.where(HelmPromise.kind == kind)
    if direction:
        query = query.where(HelmPromise.direction == direction)
    if status:
        query = query.where(HelmPromise.status == status)
    if counterparty:
        query = query.where(HelmPromise.counterparty_key == counterparty)
    if at_risk:
        query = query.where(HelmPromise.status.in_((v.STATUS_BROKEN, v.STATUS_PARTIAL)) |
                            ((HelmPromise.status == v.STATUS_OPEN) & (HelmPromise.due_date <= func.current_date() + 7)))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(query.order_by(HelmPromise.due_date.nulls_last(), HelmPromise.source_key)
                      .limit(min(500, max(1, limit))).offset(max(0, offset))).scalars().all()
    names = {w.id: w.workspace_name for w in db.execute(
        select(Workspace).where(Workspace.organization_id == who.organization_id)).scalars()}
    counts = {k: n for k, n in db.execute(
        select(HelmPromise.kind, func.count()).where(HelmPromise.workspace_id.in_(workspace_ids or [uuid.uuid4()]))
        .group_by(HelmPromise.kind)).all()}
    owed = {}
    for direction_key, currency, amount in db.execute(
        select(HelmPromise.direction, HelmPromise.currency, func.sum(HelmPromise.amount_micros))
        .where(HelmPromise.workspace_id.in_(workspace_ids or [uuid.uuid4()]),
               HelmPromise.kind.in_((v.KIND_PAYMENT_DUE, v.KIND_PAYMENT_EXPECTED)),
               HelmPromise.status.in_((v.STATUS_OPEN, v.STATUS_PARTIAL, v.STATUS_BROKEN)))
        .group_by(HelmPromise.direction, HelmPromise.currency)).all():
        owed.setdefault(direction_key, {})[currency or "?"] = int(amount or 0)
    return {"total": total, "promises": [promise_json(who, p, names) for p in rows], "counts": counts,
            "open_totals": owed}


def payment_run(db: Session, who: Viewer, book: HelmBook, *, on: Optional[date] = None) -> dict[str, Any]:
    """The hand-over: what a person pays from their own bank, run day by run day (masked accounts)."""
    workspace_ids = ledger_store.book_workspace_ids(db, book)
    query = (select(HelmSettlement, HelmPromise)
             .join(HelmPromise, (HelmPromise.work_item_id == HelmSettlement.work_item_id)
                   & (HelmPromise.kind == v.KIND_PAYMENT_DUE))
             .where(HelmSettlement.workspace_id.in_(workspace_ids or [uuid.uuid4()]),
                    HelmSettlement.state.in_((v.SETTLEMENT_SCHEDULED, v.SETTLEMENT_APPROVED))))
    if on is not None:
        query = query.where(HelmSettlement.run_date == on)
    rows = db.execute(query.order_by(HelmSettlement.run_date, HelmPromise.counterparty_name)).all()
    actions = {a.work_item_id: a for a in db.execute(
        select(HelmAction).where(HelmAction.book_id == book.id, HelmAction.kind == v.ACTION_SCHEDULE_PAYMENT,
                                 HelmAction.state == v.ACTION_EXECUTED)
        .order_by(HelmAction.executed_at)).scalars()}
    days: dict[str, dict[str, Any]] = {}
    for settlement, promise in rows:
        readable = who.can_read(settlement.workspace_id)
        key = settlement.run_date.isoformat() if settlement.run_date else "unscheduled"
        day = days.setdefault(key, {"run_date": key, "lines": [], "totals": {}})
        action = actions.get(settlement.work_item_id)
        line = {"work_item_id": str(settlement.work_item_id) if readable else None,
                "payee": promise.counterparty_name if readable else MASK,
                "bank_account": (promise.details or {}).get("bank_account") if readable else MASK,
                "amount_micros": promise.amount_micros if readable else None, "currency": promise.currency,
                "invoice": (promise.details or {}).get("document_number") if readable else MASK,
                "state": settlement.state,
                "certificate_id": str(action.certificate_id) if (action and action.certificate_id) else None,
                "readable": readable}
        day["lines"].append(line)
        if readable and promise.currency:
            day["totals"][promise.currency] = day["totals"].get(promise.currency, 0) + int(promise.amount_micros or 0)
    return {"days": list(days.values()),
            "note": "Helm never moves money. Pay these from your bank or accounting system, then mark them paid."}


__all__ = [
    "Viewer", "action_for", "action_json", "add_cash_position", "approve", "approve_all", "book_for", "book_json",
    "certificate_json", "cockpit", "create_book", "create_mandate", "ledger_view", "list_books",
    "list_certificates", "list_counterparties", "list_mandates", "mark_paid", "payment_run", "preview_spec",
    "reject", "replan", "require_decider", "require_helm", "set_status", "settings_json", "undo",
    "update_counterparty", "update_mandate", "update_settings", "viewer",
]
