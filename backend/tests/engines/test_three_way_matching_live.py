"""Three-way matching, live: PO + goods receipt + invoice through the real pipeline.

A workspace publishes a 5% price tolerance. Three suppliers' documents arrive:

* within tolerance (invoice 4% over the PO price, all goods received) -> the
  case is MATCHED with no exceptions and can be approved without a reason;
* over tolerance (8% over)                         -> PRICE_VARIANCE, NEEDS_REVIEW,
  approval refused without a written override reason, accepted with one;
* short delivery (90 of 100 received)               -> a quantity exception.

Every number is checked against hand-computed micros, not against the engine.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.engines.conftest import Engines
from tests.engines.isolation import assert_workspace_isolated


def _po(n: str, vendor: str, qty: int, price: str) -> tuple[list[str], dict]:
    text = [
        f"{vendor.upper()}",
        "PURCHASE ORDER",
        f"PO Number: {n}",
        "Order Date: 2026-08-20",
        "Ship To: Contoso Retail Warehouse",
        f"Steel bolts M8   {qty}   {price}",
    ]
    total = str(Decimal(price) * qty)
    entities = {"vendor_name": vendor, "po_number": n, "date": "2026-08-20", "currency": "USD",
                "total_amount": total,
                "line_items": [{"description": "Steel bolts M8", "quantity": qty, "unit_price": price,
                                "amount": total}]}
    return text, entities


def _grn(n: str, po: str, vendor: str, qty: int) -> tuple[list[str], dict]:
    text = [
        f"{vendor.upper()}",
        "GOODS RECEIPT NOTE",
        f"GRN Number: {n}",
        f"PO Number: {po}",
        "Receipt Date: 2026-08-28",
        f"Steel bolts M8   quantity received {qty}",
        "Received by: Dock 4",
    ]
    entities = {"vendor_name": vendor, "grn_number": n, "po_number": po, "date": "2026-08-28",
                "line_items": [{"description": "Steel bolts M8", "quantity": qty}]}
    return text, entities


def _invoice(n: str, po: str, vendor: str, qty: int, price: str) -> tuple[list[str], dict]:
    total = str(Decimal(price) * qty)
    text = [
        f"{vendor.upper()}",
        "TAX INVOICE",
        f"Invoice Number: {n}",
        f"PO Number: {po}",
        "Invoice Date: 2026-09-01",
        "Bill To: Contoso Retail",
        f"Steel bolts M8   {qty}   {price}   {total}",
        f"Total: {total} USD",
    ]
    entities = {"vendor_name": vendor, "invoice_number": n, "po_number": po, "date": "2026-09-01",
                "currency": "USD", "total_amount": total,
                "line_items": [{"description": "Steel bolts M8", "quantity": qty, "unit_price": price,
                                "amount": total}]}
    return text, entities


def _set(engines: Engines, tag: str, vendor: str, *, received: int, invoice_price: str):
    po_n, grn_n, inv_n = f"PO-{tag}", f"GRN-{tag}", f"INV-{tag}"
    for kind, (text, entities) in (
        ("Purchase Order", _po(po_n, vendor, 100, "2.50")),
        ("Goods Receipt", _grn(grn_n, po_n, vendor, received)),
        ("Invoice", _invoice(inv_n, po_n, vendor, 100, invoice_price)),
    ):
        marker = text[2].split(": ")[1]
        engines.process(f"{marker}.pdf", [text], marker=marker, classification=kind, entities=entities)
    cases = engines.get("/procurement/cases").json()
    invoice_ids = {c["invoice_work_item_id"] for c in cases}
    found = [c for c in cases if c["vendor_key"] and vendor.split()[0].lower() in c["vendor_key"]]
    assert found, (tag, cases)
    return engines.get(f"/procurement/cases/{found[0]['id']}").json()


@pytest.fixture()
def policy(engines: Engines) -> dict:
    response = engines.post("/procurement/policies", {"price_tolerance_bps": 500})
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_within_tolerance_matches_and_approves_cleanly(engines: Engines, policy) -> None:
    case = _set(engines, "TWM-OK", "Acme Supplies Ltd", received=100, invoice_price="2.60")
    assert case["status"] == "MATCHED", case
    assert case["exception_count"] == 0
    [line] = case["lines"]
    assert line["outcome"] == "MATCHED"
    assert line["po_unit_price_micros"] == 2_500_000
    assert line["invoice_unit_price_micros"] == 2_600_000
    assert line["price_delta_micros"] == 100_000
    assert line["receipt_quantity"] is not None and Decimal(str(line["receipt_quantity"])) == 100

    approved = engines.post(f"/procurement/cases/{case['id']}/approve", {})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    assert_workspace_isolated(engines, collections=("procurement",))


def test_over_tolerance_needs_a_reason_to_approve(engines: Engines, policy) -> None:
    case = _set(engines, "TWM-HI", "Globex Industrial", received=100, invoice_price="2.70")
    assert case["status"] == "NEEDS_REVIEW", case
    assert case["exception_count"] == 1
    [line] = case["lines"]
    assert line["outcome"] == "PRICE_VARIANCE"
    assert line["price_delta_micros"] == 200_000

    refused = engines.post(f"/procurement/cases/{case['id']}/approve", {})
    assert refused.status_code in (400, 409, 422), refused.text
    viewer = engines.post(f"/procurement/cases/{case['id']}/approve",
                          {"override_reason": "Price rise agreed by phone"}, as_user=engines.tenant.viewer)
    assert viewer.status_code == 403
    approved = engines.post(f"/procurement/cases/{case['id']}/approve",
                            {"override_reason": "Price rise agreed with supplier on 2026-08-30"})
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "APPROVED"
    assert body["resolution_reason"].startswith("Price rise agreed")


def test_short_delivery_is_an_exception(engines: Engines, policy) -> None:
    case = _set(engines, "TWM-SHORT", "Initech Hardware", received=90, invoice_price="2.50")
    assert case["status"] == "NEEDS_REVIEW", case
    [line] = case["lines"]
    assert line["outcome"] in ("QUANTITY_VARIANCE", "NOT_RECEIVED"), line
    assert Decimal(str(line["receipt_quantity"])) == 90
