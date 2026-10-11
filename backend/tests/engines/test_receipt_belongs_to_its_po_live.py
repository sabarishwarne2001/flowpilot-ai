"""A goods receipt that names a purchase order is matched only to that order (campaign session 2, F-274).

    pytest tests/engines/test_receipt_belongs_to_its_po_live.py -q

The matcher found a goods receipt by supplier and date window alone, on the assumption that a
receipt names no order. Most do name one. With two orders from one supplier in the same weeks, the
receipt for order A was attached to order B's invoice: false "not invoiced" exceptions on B, and
the same delivery counted against two invoices. A receipt that names an order now goes only with
that order; one that names none is still found by supplier and dates.
"""

from __future__ import annotations

from tests.engines.conftest import Engines
from tests.engines.test_three_way_matching_live import _grn, _invoice, _po

VENDOR = "Acme Supplies Ltd"


def _process(engines: Engines, kind: str, text: list[str], entities: dict) -> str:
    marker = text[2].split(": ")[1]
    return str(engines.process(f"{marker}.pdf", [text], marker=marker, classification=kind, entities=entities))


def _case_for(engines: Engines, invoice_id: str) -> dict:
    cases = engines.get("/procurement/cases").json()
    [case] = [c for c in cases if str(c["invoice_work_item_id"]) == invoice_id]
    return engines.get(f"/procurement/cases/{case['id']}").json()


def test_a_receipt_naming_one_order_is_not_attached_to_another(engines: Engines) -> None:
    _process(engines, "Purchase Order", *_po("PO-RCPT-A", VENDOR, 100, "2.50"))
    receipt_a = _process(engines, "Goods Receipt", *_grn("GRN-RCPT-A", "PO-RCPT-A", VENDOR, 100))
    _process(engines, "Purchase Order", *_po("PO-RCPT-B", VENDOR, 40, "2.50"))

    invoice_b = _process(engines, "Invoice", *_invoice("INV-RCPT-B", "PO-RCPT-B", VENDOR, 40, "2.50"))
    case_b = _case_for(engines, invoice_b)
    assert case_b.get("receipt_work_item_id") != receipt_a, "order A's receipt was attached to order B's invoice"

    invoice_a = _process(engines, "Invoice", *_invoice("INV-RCPT-A", "PO-RCPT-A", VENDOR, 100, "2.50"))
    case_a = _case_for(engines, invoice_a)
    assert str(case_a.get("receipt_work_item_id")) == receipt_a, case_a
