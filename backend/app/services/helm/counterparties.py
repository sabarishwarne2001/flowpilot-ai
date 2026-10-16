"""A book's counterparties: who the suppliers and customers are, and how they behave.

`sync()` refreshes what Helm derives (display name, role, tax id, Udyam number, a Udyam
certificate's category, entity-graph links, contact email, behaviour) from the book's promises and
documents. It never overwrites what a person set: an MSME category a person confirmed
(`msme_source = PERSON`) and a contact typed in Helm settings stay as they are.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.document_role import DocumentRole
from app.models.helm import HelmBook, HelmCounterparty, HelmPromise
from app.models.work_item import WorkItem
from app.services.helm import identity as ident
from app.services.helm import ledger, msme
from app.services.helm import vocabulary as v
from app.services.helm.ledger_store import TEXT_HEAD_CHARS, book_workspace_ids

EMAIL_KEYS = ("vendor_email", "supplier_email", "seller_email", "email", "contact_email", "billing_email",
              "accounts_email", "remit_email")
CUSTOMER_EMAIL_KEYS = ("buyer_email", "customer_email", "bill_to_email", "client_email")


def _date(value: Any) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _email(entities: dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = entities.get(key)
        if isinstance(value, str) and "@" in value and len(value) <= 320:
            return value.strip().lower()
    return None


def derive(db: Session, book: HelmBook, *, as_of: Optional[date] = None) -> dict[str, dict[str, Any]]:
    """What Helm can say about each counterparty from the book's promises and documents (pure read)."""
    as_of = as_of or datetime.now(timezone.utc).date()
    workspace_ids = book_workspace_ids(db, book)
    if not workspace_ids:
        return {}
    promises = db.execute(
        select(HelmPromise).where(HelmPromise.workspace_id.in_(workspace_ids),
                                  HelmPromise.kind.in_((v.KIND_PAYMENT_DUE, v.KIND_PAYMENT_EXPECTED)))
    ).scalars().all()
    rows: list[ledger.PromiseRow] = []
    found: dict[str, dict[str, Any]] = {}
    for p in promises:
        if not p.counterparty_key:
            continue
        details = dict(p.details or {})
        details["invoice_date"] = _date(details.get("invoice_date"))
        settlement = dict(details.get("settlement") or {})
        settlement["paid_on"] = _date(settlement.get("paid_on"))
        details["settlement"] = settlement
        rows.append(ledger.PromiseRow(
            organization_id=p.organization_id, workspace_id=p.workspace_id, work_item_id=p.work_item_id,
            source_key=p.source_key, kind=p.kind, direction=p.direction, status=p.status, confidence=p.confidence,
            title=p.title, counterparty_key=p.counterparty_key, counterparty_name=p.counterparty_name,
            currency=p.currency, amount_micros=p.amount_micros, due_date=p.due_date, details=details))
        entry = found.setdefault(p.counterparty_key, {"name": p.counterparty_name, "roles": set(), "udyam": None,
                                                      "terms_days": [], "work_items": []})
        entry["roles"].add(v.ROLE_SUPPLIER if p.kind == v.KIND_PAYMENT_DUE else v.ROLE_CUSTOMER)
        if p.counterparty_name and not entry["name"]:
            entry["name"] = p.counterparty_name
        if details.get("udyam_number"):
            entry["udyam"] = details["udyam_number"]
        net_days = (p.terms or {}).get("net_days")
        if (p.terms or {}).get("parsed") and net_days is not None:
            entry["terms_days"].append(int(net_days))
        if p.work_item_id:
            entry["work_items"].append(p.work_item_id)

    behaviour = ledger.behaviour(rows, as_of=as_of)

    # Contacts and Udyam certificates from the documents themselves.
    item_ids = [wid for entry in found.values() for wid in entry["work_items"]]
    entities_by_item: dict[uuid.UUID, dict[str, Any]] = {}
    if item_ids:
        for wid, entities in db.execute(
            select(WorkItem.id, WorkItem.extracted_entities).where(WorkItem.id.in_(item_ids))
        ).all():
            entities_by_item[wid] = entities if isinstance(entities, dict) else {}
    certificates: dict[str, tuple[str, str]] = {}
    for wid, entities, text_head, vendor_key in db.execute(
        select(WorkItem.id, WorkItem.extracted_entities, WorkItem.extracted_text, DocumentRole.vendor_key)
        .join(DocumentRole, DocumentRole.work_item_id == WorkItem.id)
        .where(WorkItem.workspace_id.in_(workspace_ids), WorkItem.extracted_text.ilike("%udyam registration%"))
    ).all():
        category = msme.certificate_category((text_head or "")[:TEXT_HEAD_CHARS])
        number = msme.find_udyam(entities if isinstance(entities, dict) else {}, text_head)
        party = ident.seller(entities if isinstance(entities, dict) else {})
        key = vendor_key or party.key
        if category and number and key:
            certificates[key] = (number, category)

    out: dict[str, dict[str, Any]] = {}
    for key, entry in found.items():
        roles = entry["roles"]
        role = v.ROLE_BOTH if len(roles) > 1 else next(iter(roles))
        info = dict(behaviour.get(key) or {})
        if entry["terms_days"]:
            info["terms_days"] = sorted(entry["terms_days"])[len(entry["terms_days"]) // 2]
        contact = None
        for wid in entry["work_items"]:
            entities = entities_by_item.get(wid) or {}
            contact = _email(entities, CUSTOMER_EMAIL_KEYS if role == v.ROLE_CUSTOMER else EMAIL_KEYS)
            if contact:
                break
        cert = certificates.get(key)
        out[key] = {"name": entry["name"] or key.split(":", 1)[-1], "role": role,
                    "udyam": cert[0] if cert else entry["udyam"], "certificate_category": cert[1] if cert else None,
                    "contact": contact, "behaviour": json_safe(info)}
    return out


def json_safe(value: Any) -> Any:
    return ledger.json.loads(ledger.canonical(value))


def sync(db: Session, book: HelmBook, *, as_of: Optional[date] = None) -> int:
    """Upsert derived fields; keep what a person set. Returns the number of counterparties."""
    derived = derive(db, book, as_of=as_of)
    if not derived:
        return 0
    existing = {row.key: row for row in db.execute(
        select(HelmCounterparty).where(HelmCounterparty.book_id == book.id)).scalars()}
    now = datetime.now(timezone.utc)
    for key, facts in sorted(derived.items()):
        row = existing.get(key)
        if row is None:
            category = facts["certificate_category"]
            db.execute(pg_insert(HelmCounterparty).values(
                id=uuid.uuid4(), organization_id=book.organization_id, book_id=book.id, key=key[:255],
                role=facts["role"], display_name=(facts["name"] or key)[:300], udyam_number=facts["udyam"],
                msme_category=category, msme_source=(v.MSME_SOURCE_CERTIFICATE if category else
                                                     v.MSME_SOURCE_DOCUMENT if facts["udyam"] else None),
                contact_email=facts["contact"], behaviour=facts["behaviour"],
            ).on_conflict_do_nothing(constraint="uq_helm_counterparties_book_key"))
            continue
        row.role = facts["role"]
        row.display_name = (facts["name"] or row.display_name)[:300]
        row.behaviour = facts["behaviour"]
        if facts["udyam"] and not row.udyam_number:
            row.udyam_number = facts["udyam"]
        if row.msme_source != v.MSME_SOURCE_PERSON:
            if facts["certificate_category"]:
                row.msme_category, row.msme_source = facts["certificate_category"], v.MSME_SOURCE_CERTIFICATE
            elif row.udyam_number and row.msme_source is None:
                row.msme_source = v.MSME_SOURCE_DOCUMENT
        if facts["contact"] and not row.contact_email:
            row.contact_email = facts["contact"]
        row.updated_at = now
    db.flush()
    return len(derived)


__all__ = ["derive", "sync"]
