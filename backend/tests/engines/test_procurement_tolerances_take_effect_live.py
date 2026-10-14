"""Every procurement tolerance changes what three-way matching decides (campaign session 4).

    pytest tests/engines/test_procurement_tolerances_take_effect_live.py -q

A workspace's tolerance policy has five numbers. Only the relative price tolerance (basis points)
was proven (test_three_way_matching_live.py); the absolute price amount, the quantity tolerance,
the pair-cost ceiling and the candidate window each had a reader and no test of their effect.
Here each is published through the endpoint the policy editor calls, and the same document set
(PO for 100 bolts at 2.50, a goods receipt dated eight days after the PO, an invoice) is matched
on either side of the configured number: the outcome moves exactly at the boundary.

The documents run through the real pipeline; the model's reading is recorded (conftest).
"""

from __future__ import annotations

from decimal import Decimal

from tests.engines.conftest import Engines
from tests.engines.test_three_way_matching_live import _set


def _publish(engines: Engines, **numbers) -> dict:
    response = engines.post("/procurement/policies", numbers)
    assert response.status_code == 201, response.text
    body = response.json()
    for key, value in numbers.items():
        assert Decimal(str(body[key])) == Decimal(str(value)), (key, body)
    return body


def _only_line(case: dict) -> dict:
    [line] = case["lines"]
    return line


def test_the_price_amount_tolerance_decides_a_price_variance(engines: Engines) -> None:
    # The invoice is 0.10 a unit over the PO (100,000 micros); no relative allowance.
    _publish(engines, price_tolerance_micros=100_000, price_tolerance_bps=0)
    within = _set(engines, "TOL-PA1", "Acme Supplies Ltd", received=100, invoice_price="2.60")
    assert within["status"] == "MATCHED", within
    assert _only_line(within)["outcome"] == "MATCHED"
    assert _only_line(within)["price_delta_micros"] == 100_000

    _publish(engines, price_tolerance_micros=99_999, price_tolerance_bps=0)
    over = _set(engines, "TOL-PA2", "Globex Industrial", received=100, invoice_price="2.60")
    assert over["status"] == "NEEDS_REVIEW", over
    assert _only_line(over)["outcome"] == "PRICE_VARIANCE"


def test_the_quantity_tolerance_decides_a_quantity_variance(engines: Engines) -> None:
    # 100 ordered and invoiced, 98 received: two units short.
    _publish(engines, quantity_tolerance="2")
    within = _set(engines, "TOL-QT1", "Initech Hardware", received=98, invoice_price="2.50")
    assert within["status"] == "MATCHED", within
    assert _only_line(within)["outcome"] == "MATCHED"

    _publish(engines, quantity_tolerance="1")
    short = _set(engines, "TOL-QT2", "Umbrella Fasteners", received=98, invoice_price="2.50")
    assert short["status"] == "NEEDS_REVIEW", short
    assert _only_line(short)["outcome"] == "QUANTITY_VARIANCE"


def test_the_candidate_window_decides_whether_the_goods_receipt_is_found(engines: Engines) -> None:
    # The goods receipt is dated eight days after the PO it belongs to, and records a short delivery
    # (90 of 100). Inside the window it is found and the shortfall is an exception; outside it, the case
    # is matched two-way (PO and invoice only), as for a tenant that keeps no goods receipts.
    _publish(engines, candidate_window_days=8)
    found = _set(engines, "TOL-CW1", "Stark Components", received=90, invoice_price="2.50")
    assert found["receipt_work_item_id"] is not None, found
    assert found["status"] == "NEEDS_REVIEW", found
    assert _only_line(found)["outcome"] == "QUANTITY_VARIANCE"

    _publish(engines, candidate_window_days=7)
    missed = _set(engines, "TOL-CW2", "Wayne Supplies", received=90, invoice_price="2.50")
    assert missed["po_work_item_id"] is not None, "the PO is named on the invoice: the window does not apply"
    assert missed["receipt_work_item_id"] is None, missed
    assert _only_line(missed)["receipt_quantity"] is None
    assert missed["status"] == "MATCHED", missed


def test_the_pair_cost_ceiling_decides_whether_lines_pair(engines: Engines) -> None:
    baseline = _set(engines, "TOL-PC1", "Cyberdyne Parts", received=100, invoice_price="2.50")
    line = _only_line(baseline)
    assert line["outcome"] == "MATCHED", baseline
    cost = line["pair_cost"]
    assert cost is not None and 0 < cost < 600_000, line

    # At the line's own pairing cost (the costlier of its PO and receipt pairings) it still pairs.
    _publish(engines, max_pair_cost=cost)
    paired = _set(engines, "TOL-PC2", "Soylent Packaging", received=100, invoice_price="2.50")
    assert [entry["outcome"] for entry in paired["lines"]] == ["MATCHED"], paired["lines"]
    assert _only_line(paired)["pair_cost"] == cost

    # One below: the costlier pairing (the receipt's, which carries no price) is refused, so the invoice
    # line is not received and the receipt's line stands alone; the PO pairing still holds.
    _publish(engines, max_pair_cost=cost - 1)
    split = _set(engines, "TOL-PC3", "Tyrell Logistics", received=100, invoice_price="2.50")
    by_side = {(entry["invoice_line_index"] is not None, entry["receipt_line_index"] is not None): entry["outcome"]
               for entry in split["lines"]}
    assert by_side == {(True, False): "NOT_RECEIVED", (False, True): "NOT_INVOICED"}, split["lines"]
    assert split["status"] == "NEEDS_REVIEW", split

    # Far below: nothing pairs, so the invoice line is also not ordered.
    _publish(engines, max_pair_cost=1)
    unpaired = _set(engines, "TOL-PC4", "Oscorp Fixings", received=100, invoice_price="2.50")
    invoice_rows = [entry for entry in unpaired["lines"] if entry["invoice_line_index"] is not None]
    assert [entry["outcome"] for entry in invoice_rows] == ["NOT_ORDERED"], unpaired["lines"]
    assert "MATCHED" not in {entry["outcome"] for entry in unpaired["lines"]}
