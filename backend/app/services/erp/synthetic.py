"""ARCH47-S1:synthetic — golden posting objects and lookup tables with exact truth. Pure; seeded.

The same five objects are rendered through every format and preset by the
golden-file gate (verify_arch47 F1-F8); the held-out set varies currencies
(including 0- and 3-decimal ones), line counts, quantities with fractions,
Indian and European digit grouping in the source figures, long and
separator-laden names (X12 separators, XML specials, quotes, commas, a
leading "="), and missing optional fields.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from app.services.erp import canonical as C
from app.services.erp import vocabulary as v

VENDOR_ID = "5f0c2a4e-8b1d-4d52-9e2b-1a7d3c9f6b10"
BUYER = "Globex Manufacturing Ltd"
LOOKUPS = {
    "vendors": {VENDOR_ID: "56"},
    "accounts": {"default": "7", "TAX": "31", "AP": "33", "bank": "35", "payable": "33", "SVC-100": "64",
                 "HW-200": "65", "4010": "4010", "1200": "1200", "2100": "2100"},
    "items": {"SVC-100": "501", "HW-200": "502"},
    "settings": {"company_code": "1010", "purchasing_organization": "1010", "purchasing_group": "001",
                 "plant": "1010", "INR": "INR"},
}
CONFIG = {
    "tally": {"company": "Globex Manufacturing Ltd"},
    "x12": {"sender_id": "GLOBEX", "receiver_id": "ACMETECH", "usage": "T"},
    "qbo": {"realm_id": "9130350000000000"}, "zoho": {"organization_id": "60012345678"},
    "bc": {"company_id": "c1d2e3f4-0000-4000-8000-000000000001"}, "s4": {"sap_client": "100"},
    "rest": {"paths": {k: f"/postings/{k.lower()}" for k in v.OBJECT_KINDS}, "id_pointer": "/id",
             "echo": {"documentNumber": "/documentNumber"}},
}


def _party(name: str, entity: str | None = None, tax: str | None = None) -> C.Party:
    return C.Party(name=name, entity_id=entity, tax_id=tax)


def vendor_bill() -> C.PostingObject:
    obj = C.PostingObject(
        kind=v.OBJECT_VENDOR_BILL, currency="INR", document_number="INV-2026-0042", document_date=date(2026, 9, 1),
        due_date=date(2026, 10, 1), posting_date=date(2026, 9, 25), po_reference="PO-7781", payment_terms="Net 30",
        payment_terms_days=30, subtotal=Decimal("100000.00"), tax_total=Decimal("18000.00"),
        total=Decimal("118000.00"),
        vendor=_party("Acme Technology Services Pvt Ltd", VENDOR_ID, "29ABCDE1234F1Z5"),
        buyer=_party(BUYER, None, "27AAACG1234A1Z9"),
        lines=[C.Line(1, "Managed IT services, September", "SVC-100", Decimal("1"), "EA", Decimal("60000"),
                      Decimal("60000")),
               C.Line(2, "Laptop docking station", "HW-200", Decimal("8"), "NOS", Decimal("5000"), Decimal("40000"))],
        source={"kind": v.SOURCE_PROCUREMENT_CASE, "id": "c0000000-0000-4000-8000-000000000042",
                "work_item_id": "d0000000-0000-4000-8000-000000000042", "label": "Invoice INV-2026-0042 (3-way match)",
                "approved_at": "2026-09-24T10:00:00+00:00"})
    return C.check(obj)


def purchase_order() -> C.PostingObject:
    bill = vendor_bill()
    obj = C.PostingObject(kind=v.OBJECT_PURCHASE_ORDER, currency="INR", document_number="PO-7781",
                          document_date=date(2026, 8, 20), posting_date=date(2026, 9, 25), vendor=bill.vendor,
                          buyer=bill.buyer, subtotal=Decimal("100000.00"), tax_total=Decimal("18000.00"),
                          total=Decimal("118000.00"), lines=[C.Line(**{**ln.__dict__}) for ln in bill.lines],
                          source=dict(bill.source))
    return C.check(obj)


def goods_receipt() -> C.PostingObject:
    bill = vendor_bill()
    obj = C.PostingObject(kind=v.OBJECT_GOODS_RECEIPT, currency="INR", document_number="GRN-5510",
                          document_date=date(2026, 8, 28), posting_date=date(2026, 9, 25), po_reference="PO-7781",
                          vendor=bill.vendor, buyer=bill.buyer, source=dict(bill.source),
                          lines=[C.Line(1, "Managed IT services, September", "SVC-100", Decimal("1"), "EA", po_line=1),
                                 C.Line(2, "Laptop docking station", "HW-200", Decimal("8"), "NOS", po_line=2)])
    return C.check(obj)


def journal_entry() -> C.PostingObject:
    return C.check(C.accrual(vendor_bill()))


def payment() -> C.PostingObject:
    return C.check(C.payment(vendor_bill()))


GOLDEN = {v.OBJECT_VENDOR_BILL: vendor_bill, v.OBJECT_PURCHASE_ORDER: purchase_order,
          v.OBJECT_GOODS_RECEIPT: goods_receipt, v.OBJECT_JOURNAL_ENTRY: journal_entry,
          v.OBJECT_PAYMENT_REFERENCE: payment}

_NAMES = ("Acme Technology Services Pvt Ltd", "Müller & Söhne GmbH", "O'Brien \"Quality\" Supplies, Inc.",
          "=HYPERLINK(\"x\") Ltd", "Tata*Consultancy~Services>Ltd", "株式会社サンプル", "Initech <Components> & Co")
_CURRENCIES = ("INR", "USD", "EUR", "JPY", "KWD", "GBP")


def held_out(seed: int) -> C.PostingObject:
    """A random, internally consistent vendor bill (or journal / payment / PO / receipt) with exact figures."""
    rng = random.Random(seed)
    currency = rng.choice(_CURRENCIES)
    q = C.quantum(currency)
    lines = []
    for n in range(1, rng.randint(1, 12) + 1):
        quantity = Decimal(rng.choice((1, 2, 3, 10, 12, 48))) if rng.random() < 0.8 else Decimal(rng.randint(1, 900)) / 4
        price = (Decimal(rng.randint(1, 500000)) * q).quantize(q)
        lines.append(C.Line(n, rng.choice(("Consulting", "Widget, blue", "Freight & handling", "Licence \"Pro\"",
                                           "Spare part #7")) + f" {n}",
                            rng.choice((None, f"SKU-{n:03d}", "SVC-100")), quantity, rng.choice(("EA", "NOS", "KG", None)),
                            price, C.money(quantity * price, currency)))
    subtotal = sum((ln.amount for ln in lines), Decimal(0))
    tax = C.money(subtotal * Decimal(rng.choice(("0", "0.05", "0.12", "0.18", "0.2"))), currency)
    start = date(2026, 1, 1) + timedelta(days=rng.randint(0, 360))
    bill = C.PostingObject(
        kind=v.OBJECT_VENDOR_BILL, currency=currency, document_number=f"INV-{seed}-{rng.randint(1, 9999):04d}",
        document_date=start, due_date=start + timedelta(days=rng.choice((0, 15, 30, 45, 60))), posting_date=start,
        po_reference=rng.choice((None, f"PO-{seed}")), payment_terms_days=rng.choice((None, 30)),
        subtotal=subtotal if rng.random() < 0.7 else None, tax_total=tax, total=subtotal + tax,
        vendor=_party(rng.choice(_NAMES), VENDOR_ID), buyer=_party(BUYER), lines=lines,
        source={"kind": v.SOURCE_PROCUREMENT_CASE, "id": f"00000000-0000-4000-8000-{seed:012d}", "label": f"held-out {seed}"})
    bill = C.check(bill)
    kind = rng.choice(v.OBJECT_KINDS)
    if kind == v.OBJECT_JOURNAL_ENTRY:
        return C.check(C.accrual(bill))
    if kind == v.OBJECT_PAYMENT_REFERENCE:
        return C.check(C.payment(bill))
    if kind == v.OBJECT_PURCHASE_ORDER:
        bill.kind = v.OBJECT_PURCHASE_ORDER
        return C.check(bill)
    if kind == v.OBJECT_GOODS_RECEIPT:
        gr = C.PostingObject(kind=v.OBJECT_GOODS_RECEIPT, currency=currency, document_number=f"GRN-{seed}",
                             document_date=start, po_reference=bill.po_reference or f"PO-{seed}", vendor=bill.vendor,
                             buyer=bill.buyer, source=dict(bill.source),
                             lines=[C.Line(ln.number, ln.description, ln.code, ln.quantity, ln.unit, po_line=ln.number)
                                    for ln in bill.lines])
        return C.check(gr)
    return bill


__all__ = ["BUYER", "CONFIG", "GOLDEN", "LOOKUPS", "VENDOR_ID", "held_out"]
