"""ARCH47-S1:canonical — the posting objects every target is rendered from. Pure; no I/O.

A posting object is built from an APPROVED outcome (sources.py loads it) and is
the one thing a mapping reads. Five kinds:

  VENDOR_BILL        a supplier's invoice to be paid: header (number, dates,
                     currency, totals, PO reference, terms), the vendor and the
                     buyer (ARCH-42 ROOT entities: a merged record is posted as
                     its survivor), and lines (ARCH-31 reconciled lines, an
                     ARCH-44 confirmed table, or the extracted line items)
  PURCHASE_ORDER     the order the bill was matched against
  GOODS_RECEIPT      what was received (quantities), against a PO
  JOURNAL_ENTRY      balanced debit / credit lines: a confirmed ledger table,
                     or the accrual of a vendor bill (expenses and tax debited,
                     the payable credited)
  PAYMENT_REFERENCE  what to pay whom, when (the bill's due date on the
                     workspace's holiday calendar) and against which bill

Money is Decimal, quantized to the currency's ISO 4217 minor units (HALF_UP);
dates are calendar dates. `check()` refuses an object whose own figures do not
reconcile: a line whose quantity x price is not its amount, lines that do not
add up to the subtotal, a subtotal plus tax that is not the total, a journal
whose debits are not its credits. Nothing is posted that does not add up; the
refusal names the figures.

The object's JSON (`as_json`) is what mapping paths address; `PATHS` lists
every path per kind, so a mapping naming anything else is refused before it
is saved.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from app.services.erp import vocabulary as v


class BuildError(ValueError):
    """An outcome cannot become this posting object (missing or inconsistent figures)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def quantum(currency: Optional[str]) -> Decimal:
    return Decimal(1).scaleb(-v.currency_exponent(currency or ""))


def money(value: Optional[Decimal], currency: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(value).quantize(quantum(currency), rounding=ROUND_HALF_UP)


def qty(value: Optional[Decimal]) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(value).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP).normalize()


@dataclass
class Party:
    name: Optional[str] = None
    entity_id: Optional[str] = None      # the ARCH-42 ROOT entity (merged_into_id followed)
    tax_id: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None

    def as_json(self) -> dict:
        return {"name": self.name, "entity_id": self.entity_id, "tax_id": self.tax_id, "email": self.email,
                "address": self.address}


@dataclass
class Line:
    number: int
    description: Optional[str] = None
    code: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None
    unit_price: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    account: Optional[str] = None
    debit: Optional[Decimal] = None
    credit: Optional[Decimal] = None
    date: Optional[date] = None
    reference: Optional[str] = None
    po_line: Optional[int] = None
    balancing: bool = False
    #: A line whose account is fixed by its ROLE, never by a fallback: TAX (input tax), AP (the payable),
    #: BANK (the paying account). A mapping must map these explicitly (the default mappings refuse to guess).
    account_code: Optional[str] = None

    def as_json(self) -> dict:
        return {"number": self.number, "description": self.description, "code": self.code,
                "quantity": _dec(self.quantity), "unit": self.unit, "unit_price": _dec(self.unit_price),
                "amount": _dec(self.amount), "tax_amount": _dec(self.tax_amount), "account": self.account,
                "debit": _dec(self.debit), "credit": _dec(self.credit), "date": _iso(self.date),
                "reference": self.reference, "po_line": self.po_line, "balancing": self.balancing,
                "side": "DEBIT" if self.debit else "CREDIT" if self.credit else None,
                "account_code": self.account_code}


@dataclass
class PostingObject:
    kind: str
    currency: str
    document_number: Optional[str] = None
    document_date: Optional[date] = None
    due_date: Optional[date] = None
    posting_date: Optional[date] = None
    po_reference: Optional[str] = None
    memo: Optional[str] = None
    payment_terms: Optional[str] = None
    payment_terms_days: Optional[int] = None
    subtotal: Optional[Decimal] = None
    tax_total: Optional[Decimal] = None
    total: Optional[Decimal] = None
    vendor: Party = field(default_factory=Party)
    buyer: Party = field(default_factory=Party)
    lines: list[Line] = field(default_factory=list)
    source: dict = field(default_factory=dict)       # kind, id, work_item_id, label, approved_at
    notes: list[str] = field(default_factory=list)   # how figures were derived (shown with the posting)

    @property
    def total_debit(self) -> Decimal:
        return sum((ln.debit or Decimal(0) for ln in self.lines), Decimal(0))

    @property
    def total_credit(self) -> Decimal:
        return sum((ln.credit or Decimal(0) for ln in self.lines), Decimal(0))

    def as_json(self) -> dict:
        return {
            "kind": self.kind, "currency": self.currency, "document_number": self.document_number,
            "document_date": _iso(self.document_date), "due_date": _iso(self.due_date),
            "posting_date": _iso(self.posting_date), "po_reference": self.po_reference, "memo": self.memo,
            "payment_terms": self.payment_terms, "payment_terms_days": self.payment_terms_days,
            "subtotal": _dec(self.subtotal), "tax_total": _dec(self.tax_total), "total": _dec(self.total),
            "total_debit": _dec(money(self.total_debit, self.currency)) if self.kind == v.OBJECT_JOURNAL_ENTRY else None,
            "total_credit": _dec(money(self.total_credit, self.currency)) if self.kind == v.OBJECT_JOURNAL_ENTRY else None,
            "line_count": len(self.lines), "vendor": self.vendor.as_json(), "buyer": self.buyer.as_json(),
            "lines": [ln.as_json() for ln in self.lines], "lines_with_tax": self._lines_with_tax(),
            "source": dict(self.source), "notes": list(self.notes),
        }

    def _lines_with_tax(self) -> list[dict]:
        """The lines, plus one line carrying the tax (code TAX) when there is tax: what an ERP whose bill total
        is the sum of its lines needs, so the posted total is the invoice's total."""
        out = [ln.as_json() for ln in self.lines]
        if self.kind in (v.OBJECT_VENDOR_BILL, v.OBJECT_PURCHASE_ORDER) and self.tax_total:
            number = max((ln.number for ln in self.lines), default=0) + 1
            out.append(Line(number=number, description="Tax", code="TAX", quantity=Decimal(1),
                            unit_price=self.tax_total, amount=self.tax_total, account_code="TAX").as_json())
        return out

    def digest(self) -> str:
        """sha256 of the object (what was approved, as posted). The notes are explanation, not content."""
        body = self.as_json()
        body.pop("notes", None)
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _dec(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    text = format(Decimal(value), "f")
    return text


def _iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value is not None else None


# ---------------------------------------------------------------------------
# what a mapping may address
# ---------------------------------------------------------------------------

#: path -> type (text, date, decimal, integer, boolean)
HEADER_PATHS: dict[str, str] = {
    "kind": "text", "currency": "text", "document_number": "text", "document_date": "date", "due_date": "date",
    "posting_date": "date", "po_reference": "text", "memo": "text", "payment_terms": "text",
    "payment_terms_days": "integer", "subtotal": "decimal", "tax_total": "decimal", "total": "decimal",
    "total_debit": "decimal", "total_credit": "decimal", "line_count": "integer",
    "vendor.name": "text", "vendor.entity_id": "text", "vendor.tax_id": "text", "vendor.email": "text",
    "vendor.address": "text",
    "buyer.name": "text", "buyer.entity_id": "text", "buyer.tax_id": "text", "buyer.email": "text",
    "buyer.address": "text",
    "source.kind": "text", "source.id": "text", "source.work_item_id": "text", "source.label": "text",
    "source.approved_at": "text",
    "posting.id": "text", "posting.idempotency_key": "text", "posting.date": "date",
    #: a payment names the bill it pays: the external id the SAME target gave that bill's posting
    "posting.bill_external_id": "text",
}
LINE_PATHS: dict[str, str] = {
    "line.number": "integer", "line.description": "text", "line.code": "text", "line.quantity": "decimal",
    "line.unit": "text", "line.unit_price": "decimal", "line.amount": "decimal", "line.tax_amount": "decimal",
    "line.account": "text", "line.debit": "decimal", "line.credit": "decimal", "line.date": "date",
    "line.reference": "text", "line.po_line": "integer", "line.balancing": "boolean", "line.side": "text",
    "line.account_code": "text",
}
#: The header fields each kind fills (the others are None, and a mapping requiring one fails at render).
KIND_HEADER: dict[str, tuple[str, ...]] = {
    v.OBJECT_VENDOR_BILL: ("document_number", "document_date", "due_date", "po_reference", "subtotal", "tax_total",
                           "total", "vendor", "buyer", "payment_terms"),
    v.OBJECT_PURCHASE_ORDER: ("document_number", "document_date", "total", "vendor", "buyer"),
    v.OBJECT_GOODS_RECEIPT: ("document_number", "document_date", "po_reference", "vendor", "buyer"),
    v.OBJECT_JOURNAL_ENTRY: ("document_number", "document_date", "total_debit", "total_credit"),
    v.OBJECT_PAYMENT_REFERENCE: ("document_number", "due_date", "total", "vendor"),
}


def paths_for(kind: str) -> tuple[dict[str, str], dict[str, str]]:
    return dict(HEADER_PATHS), dict(LINE_PATHS)


def lookup(obj_json: dict, path: str, line: Optional[dict] = None, extra: Optional[dict] = None) -> Any:
    """A path's value in the object's JSON (or the current line's; or `extra`: posting.*)."""
    if path.startswith("line."):
        return (line or {}).get(path[5:])
    if path.startswith("posting."):
        return (extra or {}).get(path[8:])
    head, _, rest = path.partition(".")
    value = obj_json.get(head)
    if rest:
        return value.get(rest) if isinstance(value, dict) else None
    return value


# ---------------------------------------------------------------------------
# reconciliation: nothing is posted that does not add up
# ---------------------------------------------------------------------------


def _within(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) <= tolerance


def check(obj: PostingObject) -> PostingObject:
    """Quantize, reconcile, refuse. Returns the object (quantized) or raises BuildError."""
    if obj.kind not in v.OBJECT_KINDS:
        raise BuildError("UNKNOWN_KIND", f"unknown posting object {obj.kind!r}")
    code = (obj.currency or "").upper()
    if len(code) != 3 or not code.isalpha():
        raise BuildError("NO_CURRENCY", "the posting has no ISO 4217 currency (set the workspace currency or read "
                                         "one from the document)")
    obj.currency = code
    q = quantum(code)
    if len(obj.lines) > v.MAX_LINES:
        raise BuildError("TOO_MANY_LINES", f"{len(obj.lines)} lines; at most {v.MAX_LINES} are posted at once")
    for name in ("subtotal", "tax_total", "total"):
        setattr(obj, name, money(getattr(obj, name), code))
    for ln in obj.lines:
        ln.amount = money(ln.amount, code)
        ln.tax_amount = money(ln.tax_amount, code)
        ln.debit = money(ln.debit, code)
        ln.credit = money(ln.credit, code)
        ln.quantity = qty(ln.quantity)
        if ln.unit_price is not None:
            ln.unit_price = Decimal(ln.unit_price).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP).normalize()
    if obj.kind == v.OBJECT_JOURNAL_ENTRY:
        return _check_journal(obj, q)
    if obj.kind == v.OBJECT_GOODS_RECEIPT:
        if not obj.lines:
            raise BuildError("NO_LINES", "a goods receipt needs received lines (quantities)")
        missing = [ln.number for ln in obj.lines if ln.quantity is None]
        if missing:
            raise BuildError("NO_QUANTITY", f"received quantity missing on line(s) {missing}")
        return obj
    if obj.kind == v.OBJECT_PAYMENT_REFERENCE:
        if obj.total is None or obj.total <= 0:
            raise BuildError("NO_AMOUNT", "a payment needs a positive amount")
        if not obj.vendor.name:
            raise BuildError("NO_PAYEE", "a payment needs a payee")
        return obj
    # VENDOR_BILL / PURCHASE_ORDER
    if not obj.lines:
        raise BuildError("NO_LINES", "no lines")
    for ln in obj.lines:
        if ln.amount is None:
            if ln.quantity is not None and ln.unit_price is not None:
                ln.amount = money(ln.quantity * ln.unit_price, code)
            else:
                raise BuildError("LINE_AMOUNT", f"line {ln.number} has no amount")
        if ln.quantity is not None and ln.unit_price is not None:
            tolerance = max(q, abs(ln.quantity) * q / 2)
            if not _within(ln.quantity * ln.unit_price, ln.amount, tolerance):
                raise BuildError("LINE_ARITHMETIC", f"line {ln.number}: {ln.quantity} x {ln.unit_price} is "
                                                    f"{money(ln.quantity * ln.unit_price, code)}, not {ln.amount}")
    lines_total = sum((ln.amount for ln in obj.lines), Decimal(0))
    line_tax = sum((ln.tax_amount or Decimal(0) for ln in obj.lines), Decimal(0))
    tolerance = q * max(1, len(obj.lines))
    if obj.subtotal is None:
        obj.subtotal = money(lines_total, code)
        obj.notes.append("subtotal = the sum of the lines")
    elif not _within(lines_total, obj.subtotal, tolerance):
        raise BuildError("SUBTOTAL", f"the lines add up to {money(lines_total, code)}, the subtotal is {obj.subtotal}")
    if obj.tax_total is None:
        if obj.total is not None:
            obj.tax_total = money(obj.total - obj.subtotal, code)
            obj.notes.append("tax = total - subtotal")
        else:
            obj.tax_total = money(line_tax, code)
    if obj.total is None:
        obj.total = money(obj.subtotal + obj.tax_total, code)
        obj.notes.append("total = subtotal + tax")
    if not _within(obj.subtotal + obj.tax_total, obj.total, q):
        raise BuildError("TOTAL", f"subtotal {obj.subtotal} + tax {obj.tax_total} is "
                                  f"{money(obj.subtotal + obj.tax_total, code)}, the total is {obj.total}")
    if obj.tax_total < 0 or obj.total < 0:
        raise BuildError("NEGATIVE", "a negative bill is a credit note, which this milestone does not post")
    return obj


def _check_journal(obj: PostingObject, q: Decimal) -> PostingObject:
    if len(obj.lines) < 2:
        raise BuildError("NO_LINES", "a journal entry needs at least two lines")
    for ln in obj.lines:
        d, c = ln.debit or Decimal(0), ln.credit or Decimal(0)
        if d < 0 or c < 0:
            raise BuildError("NEGATIVE", f"line {ln.number}: debits and credits are positive amounts")
        if (d > 0) == (c > 0):
            raise BuildError("ONE_SIDE", f"line {ln.number}: exactly one of debit and credit")
    if obj.total_debit != obj.total_credit:
        raise BuildError("UNBALANCED", f"debits {obj.total_debit} are not credits {obj.total_credit}")
    obj.total = money(obj.total_debit, obj.currency)
    return obj


def balance(obj: PostingObject, *, account: Optional[str], description: str) -> PostingObject:
    """Add the balancing line a one-sided set of journal lines needs (its account comes from the mapping)."""
    diff = obj.total_debit - obj.total_credit
    if diff == 0:
        return obj
    number = max((ln.number for ln in obj.lines), default=0) + 1
    obj.lines.append(Line(number=number, description=description, account=account,
                          debit=-diff if diff < 0 else None, credit=diff if diff > 0 else None, balancing=True,
                          date=obj.document_date))
    obj.notes.append(f"balancing line {number}: {'credit' if diff > 0 else 'debit'} {money(abs(diff), obj.currency)}")
    return obj


def accrual(bill: PostingObject) -> PostingObject:
    """The journal a vendor bill books: each line's amount (and its tax) debited, the payable credited."""
    je = PostingObject(kind=v.OBJECT_JOURNAL_ENTRY, currency=bill.currency, document_number=bill.document_number,
                       document_date=bill.document_date, posting_date=bill.posting_date, memo=bill.memo,
                       vendor=bill.vendor, buyer=bill.buyer, source=dict(bill.source), po_reference=bill.po_reference)
    n = 0
    for ln in bill.lines:
        n += 1
        je.lines.append(Line(number=n, description=ln.description, code=ln.code, account=ln.account, debit=ln.amount,
                             date=bill.document_date, reference=bill.document_number))
    if bill.tax_total:
        n += 1
        je.lines.append(Line(number=n, description="Input tax", code="TAX", debit=bill.tax_total,
                             date=bill.document_date, reference=bill.document_number, account_code="TAX"))
    n += 1
    je.lines.append(Line(number=n, description=f"Payable to {bill.vendor.name or 'vendor'}", code="AP",
                         credit=bill.total, date=bill.document_date, reference=bill.document_number, balancing=True,
                         account_code="AP"))
    je.notes.append("accrual of the vendor bill: lines and tax debited, the payable credited")
    return je


def payment(bill: PostingObject) -> PostingObject:
    pay = PostingObject(kind=v.OBJECT_PAYMENT_REFERENCE, currency=bill.currency,
                        document_number=bill.document_number, document_date=bill.document_date,
                        due_date=bill.due_date, posting_date=bill.posting_date, po_reference=bill.po_reference,
                        total=bill.total, vendor=bill.vendor, buyer=bill.buyer, payment_terms=bill.payment_terms,
                        payment_terms_days=bill.payment_terms_days, source=dict(bill.source),
                        memo=f"Payment of {bill.document_number or 'bill'}")
    pay.lines = [Line(number=1, description=f"Bill {bill.document_number or ''}".strip(), amount=bill.total,
                      reference=bill.document_number, date=bill.due_date)]
    pay.notes = [n for n in bill.notes if "due" in n]
    return pay


__all__ = ["BuildError", "HEADER_PATHS", "KIND_HEADER", "LINE_PATHS", "Line", "Party", "PostingObject", "accrual",
           "balance", "check", "lookup", "money", "paths_for", "payment", "quantum"]
