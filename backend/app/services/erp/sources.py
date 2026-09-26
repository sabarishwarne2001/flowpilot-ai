"""ARCH47-S1:sources — approved outcomes, and the canonical posting objects built from them.

Three outcomes can be posted, and only once a person (or the engine's own
arithmetic, confirmed by a person) has approved them:

  PROCUREMENT_CASE   an ARCH-31 three-way match with status APPROVED: the vendor
                     bill is the invoice with the RECONCILED lines (the case's
                     invoice quantities and prices), the purchase order and goods
                     receipt are the matched PO and receipt, the journal is the
                     bill's accrual and the payment reference pays the bill
  TABLE              an ARCH-44 table a person ACCEPTED (status REVIEWED): a
                     ledger-shaped table (debit / credit / signed amount columns)
                     becomes a journal entry; a line-item table on an invoice
                     becomes the vendor bill's lines
  CASE               an ARCH-43 case that reached COMPLETE: its invoice / PO /
                     receipt documents, read the same way

Header values are the documents' extracted fields read by ARCH-45's typed
readers (Indian, Western and European grouping; DMY / MDY dates; currency
symbols and codes). Parties are ARCH-42 ROOT entities (merged records are
followed through merged_into_id in one recursive query, so a posting names the
survivor). A due date the document does not state is its date + the terms'
net days, rolled FOLLOWING to a business day on the workspace's DEFAULT holiday
calendar (ARCH-46's stored calendars and arithmetic). Every derivation is
written into the object's notes.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.erp import canonical as C
from app.services.erp import vocabulary as v


class SourceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Outcome:
    kind: str
    id: uuid.UUID
    workspace_id: uuid.UUID
    organization_id: uuid.UUID
    label: str
    approved_at: Optional[datetime]
    work_item_id: Optional[uuid.UUID]
    documents: dict[str, uuid.UUID] = field(default_factory=dict)   # role -> work item (invoice, po, receipt)
    objects: tuple[str, ...] = ()

    def as_json(self) -> dict:
        return {"kind": self.kind, "id": str(self.id), "label": self.label,
                "approved_at": self.approved_at.isoformat() if self.approved_at else None,
                "work_item_id": str(self.work_item_id) if self.work_item_id else None,
                "documents": {k: str(val) for k, val in self.documents.items()}, "objects": list(self.objects)}


# ---------------------------------------------------------------------------
# reading extracted fields
# ---------------------------------------------------------------------------

_KEYS = {
    "invoice_number": ("invoice_number", "invoice_no", "bill_number", "bill_no", "document_number", "number",
                       "invoice_id"),
    "invoice_date": ("invoice_date", "bill_date", "document_date", "date", "dated"),
    "po_number": ("po_number", "purchase_order_number", "order_number", "po_no", "po_reference", "document_number",
                  "number"),
    "po_date": ("po_date", "order_date", "purchase_order_date", "document_date", "date"),
    "receipt_number": ("grn_number", "receipt_number", "delivery_note_number", "dn_number", "goods_receipt_number",
                       "document_number", "number"),
    "receipt_date": ("grn_date", "receipt_date", "delivery_date", "received_date", "document_date", "date"),
    "due_date": ("due_date", "payment_due_date", "due", "pay_by", "payment_due"),
    "po_reference": ("po_reference", "po_number", "purchase_order_number", "po_ref", "buyer_order_number",
                     "reference_po", "purchase_order", "order_number", "po_no"),
    "currency": ("currency", "currency_code", "ccy"),
    "subtotal": ("subtotal", "sub_total", "taxable_value", "taxable_amount", "net_amount"),
    "tax": ("tax_amount", "tax", "gst_amount", "vat_amount", "total_tax", "total_gst"),
    "total": ("total_amount", "grand_total", "invoice_total", "total", "amount_due", "net_payable", "total_payable",
              "order_total", "total_value"),
    "payment_terms": ("payment_terms", "terms_of_payment", "credit_terms", "terms"),
    "vendor": ("vendor_name", "supplier_name", "seller_name", "vendor", "supplier", "seller", "billed_by",
               "service_provider", "consignor"),
    "buyer": ("buyer_name", "customer_name", "buyer", "customer", "bill_to", "billed_to", "sold_to", "client_name",
              "purchaser"),
    "vendor_tax_id": ("vendor_gstin", "supplier_gstin", "seller_gstin", "gstin", "vendor_vat", "vat_number",
                      "tax_id", "vendor_tax_id", "supplier_tax_id"),
    "buyer_tax_id": ("buyer_gstin", "customer_gstin", "buyer_vat", "customer_tax_id", "buyer_tax_id"),
}
_LINE_KEYS = ("line_items", "lineitems", "line_item", "items", "invoice_lines", "lines", "products")
_VENDOR_ROLES = ("vendor", "supplier", "seller", "billed_by", "provider", "consignor", "payee")
_BUYER_ROLES = ("buyer", "customer", "bill_to", "billed_to", "client", "purchaser", "consignee", "sold_to")
_TERMS = re.compile(r"(?:net|within|in)?\s*(\d{1,3})\s*(?:days?|d\b)|\bnet\s*(\d{1,3})\b", re.I)


def _flat(fields: Any) -> dict[str, Any]:
    from app.services.corroboration import fields as FF

    return FF.flatten(fields if isinstance(fields, Mapping) else {})


def _first(flat: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in flat and flat[name] not in (None, "", []):
            return flat[name]
    return None


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, list):
        value = ", ".join(str(x) for x in value)
    text = " ".join(str(value).split())
    return text or None


def _date(value: Any, order: str) -> Optional[date]:
    from app.services.tables import values as vals

    if value is None:
        return None
    if isinstance(value, date):
        return value
    return vals.parse_date(vals.clean(str(value)), order) or (vals.classify(str(value), order).when)


def _money(value: Any) -> tuple[Optional[Decimal], Optional[str]]:
    """(amount, ISO currency or None) from an extracted value ("INR 1,18,000.00", "€1.234,56", 118000)."""
    from app.services.corroboration import normalize as n
    from app.services.tables import values as vals

    if value is None or isinstance(value, bool):
        return None, None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)), None
    text = n.fold(str(value))
    currency = None
    for token in n.value_tokens(text):
        if token.kind == "MONEY":
            code = token.canonical.split()[0].partition(":")[2]
            if re.fullmatch(r"[a-z]{3}", code):
                currency = code.upper()
            break
    parsed = vals.parse_number(vals.clean(re.sub(r"(?i)\b[a-z]{3}\b|[₹$€£¥]|rs\.?", " ", str(value))))
    if parsed is not None and parsed.number is not None:
        return parsed.number, currency
    return None, currency


def _decimal(value: Any) -> Optional[Decimal]:
    return _money(value)[0]


def _terms_days(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = _TERMS.search(text)
    if not m:
        return None
    days = int(m.group(1) or m.group(2))
    return days if 0 <= days <= 365 else None


def _role(role: str) -> Optional[str]:
    r = (role or "").lower()
    if any(k in r for k in _VENDOR_ROLES):
        return "vendor"
    if any(k in r for k in _BUYER_ROLES):
        return "buyer"
    return None


# ---------------------------------------------------------------------------
# the database side
# ---------------------------------------------------------------------------


def _workspace(db: Session, workspace_id: uuid.UUID) -> Any:
    from app.models.workspace import Workspace

    ws = db.get(Workspace, workspace_id)
    if ws is None:
        raise SourceError("NOT_FOUND", "workspace not found")
    return ws


def _organization_name(db: Session, organization_id: uuid.UUID) -> Optional[str]:
    from app.models.organization import Organization

    org = db.get(Organization, organization_id)
    return getattr(org, "name", None) if org else None


def _items(db: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Any]:
    from app.models.work_item import WorkItem

    wanted = [i for i in ids if i]
    if not wanted:
        return {}
    return {w.id: w for w in db.execute(select(WorkItem).where(WorkItem.id.in_(wanted))).scalars()}


def parties(db: Session, workspace_id: uuid.UUID, work_item_id: Optional[uuid.UUID]) -> dict[str, tuple[str, str]]:
    """role -> (ROOT entity id, the root's display name) for one document (ARCH-42 mentions, merges followed)."""
    if work_item_id is None:
        return {}
    from app.services.corroboration import loader

    mentions = loader.mentions_for(db, workspace_id, [work_item_id]).get(work_item_id, ([], []))[0]
    out: dict[str, tuple[str, str]] = {}
    for m in mentions:
        role = _role(m.role)
        if role and role not in out and m.kind == "ORGANIZATION":
            out[role] = (m.entity_id, m.display_name)
    for m in mentions:  # a party of another kind (PERSON) only when no organization plays the role
        role = _role(m.role)
        if role and role not in out:
            out[role] = (m.entity_id, m.display_name)
    return out


def load(db: Session, workspace_id: uuid.UUID, source_kind: str, source_id: uuid.UUID) -> Outcome:
    """The approved outcome, or SourceError NOT_FOUND / NOT_APPROVED."""
    ws = _workspace(db, workspace_id)
    if source_kind == v.SOURCE_PROCUREMENT_CASE:
        from app.models.procurement import ProcurementCase

        case = db.execute(select(ProcurementCase).where(ProcurementCase.id == source_id,
                                                        ProcurementCase.workspace_id == workspace_id)).scalar_one_or_none()
        if case is None:
            raise SourceError("NOT_FOUND", "procurement case not found")
        if case.status != "APPROVED":
            raise SourceError("NOT_APPROVED", f"the three-way match is {case.status}; only an APPROVED match is posted")
        docs = {k: val for k, val in (("invoice", case.invoice_work_item_id), ("po", case.po_work_item_id),
                                      ("receipt", case.receipt_work_item_id)) if val}
        items = _items(db, docs.values())
        name = items.get(case.invoice_work_item_id).original_filename if case.invoice_work_item_id in items else "match"
        objects = [v.OBJECT_VENDOR_BILL, v.OBJECT_JOURNAL_ENTRY, v.OBJECT_PAYMENT_REFERENCE] if "invoice" in docs else []
        if "po" in docs:
            objects.append(v.OBJECT_PURCHASE_ORDER)
        if "receipt" in docs:
            objects.append(v.OBJECT_GOODS_RECEIPT)
        return Outcome(source_kind, case.id, workspace_id, case.organization_id, f"Reconciled invoice {name}",
                       case.resolved_at, case.invoice_work_item_id, docs, tuple(k for k in v.OBJECT_KINDS if k in objects))
    if source_kind == v.SOURCE_TABLE:
        from app.models.tables import ExtractedTable

        table = db.execute(select(ExtractedTable).where(ExtractedTable.id == source_id,
                                                        ExtractedTable.workspace_id == workspace_id)).scalar_one_or_none()
        if table is None:
            raise SourceError("NOT_FOUND", "table not found")
        if table.status != "REVIEWED":
            raise SourceError("NOT_APPROVED", f"the table is {table.status}; only a table a person accepted is posted")
        roles = {c.get("role") for c in (table.columns or [])}
        objects = []
        if roles & {"DEBIT", "CREDIT"} or ("AMOUNT" in roles and "QUANTITY" not in roles and "UNIT_PRICE" not in roles):
            objects.append(v.OBJECT_JOURNAL_ENTRY)
        if roles & {"AMOUNT", "TOTAL"} and roles & {"DESCRIPTION", "CODE", "QUANTITY"}:
            objects.append(v.OBJECT_VENDOR_BILL)
        item = _items(db, [table.work_item_id]).get(table.work_item_id)
        label = f"Table {table.ordinal + 1}{' — ' + table.title if table.title else ''} of " \
                f"{item.original_filename if item else 'a document'}"
        return Outcome(source_kind, table.id, workspace_id, table.organization_id, label, table.reviewed_at,
                       table.work_item_id, {"invoice": table.work_item_id},
                       tuple(k for k in v.OBJECT_KINDS if k in objects))
    if source_kind == v.SOURCE_CASE:
        from app.models.cases import Case, CaseDocument

        case = db.execute(select(Case).where(Case.id == source_id, Case.workspace_id == workspace_id)).scalar_one_or_none()
        if case is None:
            raise SourceError("NOT_FOUND", "case not found")
        if case.status != "COMPLETE" and not (case.status == "CLOSED" and case.completed_at is not None):
            raise SourceError("NOT_APPROVED", f"the case is {case.status}; only a COMPLETE case is posted")
        docs: dict[str, uuid.UUID] = {}
        for d in db.execute(select(CaseDocument).where(CaseDocument.case_id == case.id)
                            .order_by(CaseDocument.added_at)).scalars():
            t = (d.document_type or "").lower()
            role = "invoice" if "invoice" in t or "bill" in t else "po" if "purchase" in t or t in ("po", "order") \
                else "receipt" if any(k in t for k in ("receipt", "grn", "delivery")) else None
            if role and role not in docs:
                docs[role] = d.work_item_id
        objects = [v.OBJECT_VENDOR_BILL, v.OBJECT_JOURNAL_ENTRY, v.OBJECT_PAYMENT_REFERENCE] if "invoice" in docs else []
        if "po" in docs:
            objects.append(v.OBJECT_PURCHASE_ORDER)
        if "receipt" in docs:
            objects.append(v.OBJECT_GOODS_RECEIPT)
        return Outcome(source_kind, case.id, workspace_id, case.organization_id, f"Case {case.title}",
                       case.completed_at, docs.get("invoice") or next(iter(docs.values()), None), docs,
                       tuple(k for k in v.OBJECT_KINDS if k in objects))
    raise SourceError("UNKNOWN_SOURCE", f"unknown source kind {source_kind!r}")


def eligible(db: Session, workspace_id: uuid.UUID, *, since: Optional[datetime] = None,
             kinds: Sequence[str] = v.SOURCE_KINDS, limit: int = 200) -> list[Outcome]:
    """Approved outcomes (newest first), optionally approved at or after `since` (the auto-post horizon)."""
    from app.models.cases import Case
    from app.models.procurement import ProcurementCase
    from app.models.tables import ExtractedTable

    found: list[tuple[datetime, str, uuid.UUID]] = []
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    if v.SOURCE_PROCUREMENT_CASE in kinds:
        q = select(ProcurementCase.id, ProcurementCase.resolved_at).where(
            ProcurementCase.workspace_id == workspace_id, ProcurementCase.status == "APPROVED")
        if since:
            q = q.where(ProcurementCase.resolved_at >= since)
        found += [(at or epoch, v.SOURCE_PROCUREMENT_CASE, i) for i, at in db.execute(q.limit(limit)).all()]
    if v.SOURCE_TABLE in kinds:
        q = select(ExtractedTable.id, ExtractedTable.reviewed_at).where(
            ExtractedTable.workspace_id == workspace_id, ExtractedTable.status == "REVIEWED")
        if since:
            q = q.where(ExtractedTable.reviewed_at >= since)
        found += [(at or epoch, v.SOURCE_TABLE, i) for i, at in db.execute(q.limit(limit)).all()]
    if v.SOURCE_CASE in kinds:
        q = select(Case.id, Case.completed_at).where(Case.workspace_id == workspace_id,
                                                     Case.status.in_(("COMPLETE", "CLOSED")),
                                                     Case.completed_at.is_not(None))
        if since:
            q = q.where(Case.completed_at >= since)
        found += [(at or epoch, v.SOURCE_CASE, i) for i, at in db.execute(q.limit(limit)).all()]
    out = []
    for _, kind, ident in sorted(found, key=lambda x: x[0], reverse=True)[:limit]:
        try:
            out.append(load(db, workspace_id, kind, ident))
        except SourceError:
            continue
    return out


# ---------------------------------------------------------------------------
# building
# ---------------------------------------------------------------------------


@dataclass
class _Doc:
    flat: dict[str, Any]
    raw: Mapping[str, Any]
    item: Any
    order: str


def _doc(db: Session, work_item_id: Optional[uuid.UUID], order: str) -> Optional[_Doc]:
    if work_item_id is None:
        return None
    item = _items(db, [work_item_id]).get(work_item_id)
    if item is None:
        return None
    raw = item.extracted_entities if isinstance(item.extracted_entities, Mapping) else {}
    return _Doc(_flat(raw), raw, item, order)


def _raw_lines(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key, value in raw.items():
        k = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        if k in _LINE_KEYS and isinstance(value, list):
            return [x for x in value if isinstance(x, Mapping)]
    return []


def _line_from_fields(n: int, row: Mapping[str, Any]) -> C.Line:
    flat = _flat(row)
    return C.Line(
        number=n, description=_text(_first(flat, ("description", "item_description", "item", "name", "particulars",
                                                  "product", "service"))),
        code=_text(_first(flat, ("sku", "code", "item_code", "product_code", "hsn", "hsn_sac", "part_number"))),
        quantity=_decimal(_first(flat, ("quantity", "qty", "units"))),
        unit=_text(_first(flat, ("unit", "uom", "unit_of_measure"))),
        unit_price=_decimal(_first(flat, ("unit_price", "rate", "price", "unit_cost"))),
        amount=_decimal(_first(flat, ("amount", "line_total", "total", "net_amount", "value", "line_amount"))),
        tax_amount=_decimal(_first(flat, ("tax_amount", "tax", "gst", "vat"))))


def _table_lines(db: Session, work_item_id: uuid.UUID, *, reviewed_only: bool) -> tuple[list[C.Line], Optional[str]]:
    """Line items from the document's confirmed (or validated) ARCH-44 table with line-item roles."""
    from app.models.tables import ExtractedTable
    from app.services.tables import service as table_service

    statuses = ("REVIEWED",) if reviewed_only else ("REVIEWED", "VALIDATED")
    for table in db.execute(select(ExtractedTable).where(ExtractedTable.work_item_id == work_item_id,
                                                         ExtractedTable.status.in_(statuses))
                            .order_by(ExtractedTable.ordinal)).scalars():
        roles = {c.get("role") for c in (table.columns or [])}
        if not (roles & {"AMOUNT", "TOTAL"} and roles & {"DESCRIPTION", "CODE", "QUANTITY"}):
            continue
        lines, _ = lines_of_table(table_service.load(db, table))
        if lines:
            return lines, f"lines from table {table.ordinal + 1} ({table.status.lower()})"
    return [], None


def lines_of_table(out: Any) -> tuple[list[C.Line], list[C.Line]]:
    """(line items, journal lines) of an ARCH-44 engine table: BODY rows only; totals and headers are not lines."""
    by_role: dict[str, int] = {}
    for col in out.columns:
        by_role.setdefault(col.role, col.index)
    cells = {(c.row, c.col): c for c in out.cells}

    def val(row: int, role: str) -> Any:
        col = by_role.get(role)
        if col is None:
            return None
        cell = cells.get((row, col))
        if cell is None:
            return None
        if cell.number is not None:
            return Decimal(cell.number)
        if cell.when is not None:
            return cell.when
        return _text(cell.text)

    items: list[C.Line] = []
    journal: list[C.Line] = []
    n = 0
    for row in out.rows:
        if row.kind != "BODY":
            continue
        n += 1
        amount = val(row.index, "AMOUNT")
        if amount is None:
            amount = val(row.index, "TOTAL")
        description = val(row.index, "DESCRIPTION")
        code = val(row.index, "CODE")
        items.append(C.Line(number=n, description=str(description) if description is not None else None,
                            code=str(code) if code is not None else None,
                            quantity=val(row.index, "QUANTITY") if isinstance(val(row.index, "QUANTITY"), Decimal) else None,
                            unit=val(row.index, "UNIT") if isinstance(val(row.index, "UNIT"), str) else None,
                            unit_price=val(row.index, "UNIT_PRICE") if isinstance(val(row.index, "UNIT_PRICE"), Decimal) else None,
                            amount=amount if isinstance(amount, Decimal) else None,
                            tax_amount=val(row.index, "TAX") if isinstance(val(row.index, "TAX"), Decimal) else None))
        debit, credit = val(row.index, "DEBIT"), val(row.index, "CREDIT")
        debit = debit if isinstance(debit, Decimal) and debit != 0 else None
        credit = credit if isinstance(credit, Decimal) and credit != 0 else None
        if debit is None and credit is None and isinstance(amount, Decimal) and amount != 0:
            debit, credit = (amount, None) if amount > 0 else (None, -amount)
        if debit is not None and debit < 0:
            debit, credit = None, -debit
        if credit is not None and credit < 0:
            debit, credit = -credit, None
        when = val(row.index, "DATE")
        reference = val(row.index, "REFERENCE")
        if debit is not None or credit is not None:
            journal.append(C.Line(number=len(journal) + 1, description=str(description) if description else None,
                                  code=str(code) if code is not None else None, debit=debit, credit=credit,
                                  date=when if isinstance(when, date) else None,
                                  reference=str(reference) if reference is not None else None))
    return items, journal


def _header(obj: C.PostingObject, doc: _Doc, kind: str, currency_default: Optional[str]) -> None:
    flat, order = doc.flat, doc.order
    number_key, date_key = {"invoice": ("invoice_number", "invoice_date"), "po": ("po_number", "po_date"),
                            "receipt": ("receipt_number", "receipt_date")}[kind]
    obj.document_number = _text(_first(flat, _KEYS[number_key]))
    obj.document_date = _date(_first(flat, _KEYS[date_key]), order)
    if kind != "po":
        ref = _first(flat, [k for k in _KEYS["po_reference"] if k not in _KEYS[number_key][:1]])
        if ref is not None and _text(ref) != obj.document_number:
            obj.po_reference = _text(ref)
    currency = _text(_first(flat, _KEYS["currency"]))
    obj.subtotal, c1 = _money(_first(flat, _KEYS["subtotal"]))
    obj.tax_total, c2 = _money(_first(flat, _KEYS["tax"]))
    obj.total, c3 = _money(_first(flat, _KEYS["total"]))
    code = (currency or c3 or c1 or c2 or currency_default or "").strip().upper()
    obj.currency = code[:3] if re.fullmatch(r"[A-Za-z]{3}", code[:3] or "") else (currency_default or "")
    if not currency and not (c1 or c2 or c3) and currency_default:
        obj.notes.append(f"currency {currency_default}: the workspace's (the document states none)")
    obj.payment_terms = _text(_first(flat, _KEYS["payment_terms"]))
    obj.payment_terms_days = _terms_days(obj.payment_terms)


def _parties(db: Session, obj: C.PostingObject, outcome: Outcome, doc: _Doc, work_item_id: Optional[uuid.UUID]) -> None:
    found = parties(db, outcome.workspace_id, work_item_id)
    flat = doc.flat
    vendor = found.get("vendor")
    buyer = found.get("buyer")
    obj.vendor = C.Party(name=vendor[1] if vendor else _text(_first(flat, _KEYS["vendor"])),
                         entity_id=vendor[0] if vendor else None,
                         tax_id=_text(_first(flat, _KEYS["vendor_tax_id"])))
    obj.buyer = C.Party(name=buyer[1] if buyer else _text(_first(flat, _KEYS["buyer"])),
                        entity_id=buyer[0] if buyer else None, tax_id=_text(_first(flat, _KEYS["buyer_tax_id"])))
    if vendor:
        obj.notes.append(f"vendor: the ARCH-42 record {vendor[1]} (merges followed to the surviving record)")
    elif obj.vendor.name:
        obj.notes.append("vendor: the name as extracted (no resolved record yet)")
    if not obj.buyer.name:
        obj.buyer.name = _organization_name(db, outcome.organization_id)
        if obj.buyer.name:
            obj.notes.append("buyer: this organization (the document names none)")


def due_date(db: Session, workspace_id: uuid.UUID, start: Optional[date], days: Optional[int]) -> tuple[Optional[date], Optional[str]]:
    """start + days, rolled FOLLOWING to a business day on the workspace's default holiday calendar (ARCH-46)."""
    if start is None or days is None:
        return None, None
    from app.services.obligations import service as ob_service
    from app.services.obligations import temporal as T

    row = ob_service.default_calendar(db, workspace_id)
    cal = ob_service.calendar_of(row)
    raw = start + timedelta(days=int(days))
    rolled = T.roll(raw, "FOLLOWING", cal)
    note = f"due date = {start.isoformat()} + {days} days = {raw.isoformat()}"
    if rolled != raw:
        note += f", {cal.why_closed(raw)}: rolled to the next business day {rolled.isoformat()} ({cal.name})"
    return rolled, note


def build(db: Session, outcome: Outcome, object_kind: str, *, today: date) -> C.PostingObject:
    """The canonical object for one outcome and kind, checked (C.check). Raises BuildError / SourceError."""
    if object_kind not in outcome.objects:
        raise SourceError("NOT_AVAILABLE", f"a {v.OBJECT_LABELS[object_kind].lower()} cannot be built from this "
                                           f"{v.SOURCE_LABELS[outcome.kind].lower()}")
    ws = _workspace(db, outcome.workspace_id)
    order = "MDY" if str(getattr(ws, "date_format", "") or "").upper().startswith("MM") else "DMY"
    default_currency = (getattr(ws, "currency", None) or "").upper() or None
    source = {"kind": outcome.kind, "id": str(outcome.id), "label": outcome.label,
              "work_item_id": str(outcome.work_item_id) if outcome.work_item_id else None,
              "approved_at": outcome.approved_at.isoformat() if outcome.approved_at else None}

    if object_kind in (v.OBJECT_JOURNAL_ENTRY,) and outcome.kind == v.SOURCE_TABLE:
        return _table_journal(db, outcome, source, today, default_currency, order)

    role = {v.OBJECT_PURCHASE_ORDER: "po", v.OBJECT_GOODS_RECEIPT: "receipt"}.get(object_kind, "invoice")
    work_item_id = outcome.documents.get(role)
    doc = _doc(db, work_item_id, order)
    if doc is None:
        raise SourceError("NO_DOCUMENT", f"the {role} document is no longer available")
    base_kind = v.OBJECT_VENDOR_BILL if object_kind in (v.OBJECT_JOURNAL_ENTRY, v.OBJECT_PAYMENT_REFERENCE) else object_kind
    obj = C.PostingObject(kind=base_kind, currency="", posting_date=today, source=dict(source))
    _header(obj, doc, role, default_currency)
    _parties(db, obj, outcome, doc, work_item_id)

    lines: list[C.Line] = []
    if outcome.kind == v.SOURCE_PROCUREMENT_CASE:
        lines = _case_lines(db, outcome.id, role, obj.currency)
        if lines:
            obj.notes.append(f"lines: the three-way match's reconciled {role} lines")
    if not lines and outcome.kind == v.SOURCE_TABLE and role == "invoice":
        from app.models.tables import ExtractedTable
        from app.services.tables import service as table_service

        table = db.get(ExtractedTable, outcome.id)
        lines, _ = lines_of_table(table_service.load(db, table))
        obj.notes.append("lines: the confirmed table")
    if not lines and work_item_id is not None:
        lines, note = _table_lines(db, work_item_id, reviewed_only=False)
        if note:
            obj.notes.append(note)
    if not lines:
        raw = _raw_lines(doc.raw)
        lines = [_line_from_fields(i, row) for i, row in enumerate(raw, start=1)]
        if lines:
            obj.notes.append("lines: the extracted line items")
    if object_kind == v.OBJECT_GOODS_RECEIPT:
        lines = [C.Line(number=ln.number, description=ln.description, code=ln.code, quantity=ln.quantity,
                        unit=ln.unit, unit_price=ln.unit_price, po_line=ln.po_line or ln.number) for ln in lines]
    if not lines and base_kind in (v.OBJECT_VENDOR_BILL, v.OBJECT_PURCHASE_ORDER) and (obj.subtotal or obj.total):
        amount = obj.subtotal if obj.subtotal is not None else (obj.total - (obj.tax_total or Decimal(0)))
        lines = [C.Line(number=1, description=f"{v.OBJECT_LABELS[base_kind]} {obj.document_number or ''}".strip(),
                        quantity=Decimal(1), unit_price=amount, amount=amount)]
        obj.notes.append("lines: one line for the whole document (no line items were read)")
    obj.lines = lines
    if base_kind == v.OBJECT_VENDOR_BILL:
        explicit = _date(_first(doc.flat, _KEYS["due_date"]), order)
        if explicit is not None:
            obj.due_date = explicit
            obj.notes.append("due date: as stated on the document")
        else:
            obj.due_date, note = due_date(db, outcome.workspace_id, obj.document_date, obj.payment_terms_days)
            if note:
                obj.notes.append(note)
    if not obj.document_number:
        raise C.BuildError("NO_NUMBER", f"the {role} has no document number (extracted fields do not name one)")
    if not obj.document_date and object_kind != v.OBJECT_PAYMENT_REFERENCE:
        raise C.BuildError("NO_DATE", f"the {role} has no date")
    obj = C.check(obj)
    if object_kind == v.OBJECT_JOURNAL_ENTRY:
        return C.check(C.accrual(obj))
    if object_kind == v.OBJECT_PAYMENT_REFERENCE:
        return C.check(C.payment(obj))
    return obj


def _case_lines(db: Session, case_id: uuid.UUID, role: str, currency: str) -> list[C.Line]:
    from app.models.procurement import ProcurementCaseLine

    rows = list(db.execute(select(ProcurementCaseLine).where(ProcurementCaseLine.case_id == case_id)
                           .order_by(ProcurementCaseLine.line_number)).scalars())
    out = []
    micro = Decimal(1_000_000)
    for r in rows:
        if role == "invoice":
            if r.invoice_line_index is None:
                continue
            qty, price, amount = r.invoice_quantity, r.invoice_unit_price_micros, r.invoice_amount_micros
        elif role == "po":
            if r.po_line_index is None:
                continue
            qty, price, amount = r.po_quantity, r.po_unit_price_micros, r.po_amount_micros
        else:
            if r.receipt_line_index is None:
                continue
            qty, price, amount = r.receipt_quantity, r.po_unit_price_micros, None
        out.append(C.Line(number=len(out) + 1, description=r.description, code=r.sku,
                          quantity=Decimal(qty) if qty is not None else None,
                          unit_price=(Decimal(price) / micro) if price is not None else None,
                          amount=(Decimal(amount) / micro) if amount is not None else None,
                          po_line=(r.po_line_index + 1) if r.po_line_index is not None else None))
    return out


def _table_journal(db: Session, outcome: Outcome, source: dict, today: date, currency: Optional[str],
                   order: str) -> C.PostingObject:
    from app.models.tables import ExtractedTable
    from app.services.tables import service as table_service

    table = db.get(ExtractedTable, outcome.id)
    _, lines = lines_of_table(table_service.load(db, table))
    if not lines:
        raise C.BuildError("NO_LINES", "the table has no debit, credit or amount rows")
    doc = _doc(db, table.work_item_id, order)
    number = None
    if doc is not None:
        number = _text(_first(doc.flat, ("statement_number", "document_number", "reference", "number")))
    dates = [ln.date for ln in lines if ln.date]
    obj = C.PostingObject(kind=v.OBJECT_JOURNAL_ENTRY, currency=currency or "",
                          document_number=number or f"TBL-{str(table.id)[:8].upper()}",
                          document_date=max(dates) if dates else today, posting_date=today,
                          memo=outcome.label, source=dict(source), lines=lines)
    obj.notes.append(f"lines: the confirmed table's {len(lines)} debit / credit rows")
    C.balance(obj, account=None, description="Balancing entry")
    for ln in obj.lines:
        if ln.balancing:
            ln.account_code = "CONTRA"
    return C.check(obj)


__all__ = ["Outcome", "SourceError", "build", "due_date", "eligible", "lines_of_table", "load", "parties"]
