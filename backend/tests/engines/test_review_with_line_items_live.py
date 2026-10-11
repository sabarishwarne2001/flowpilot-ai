"""A document with line items can be reviewed (campaign session 2, F-273).

    pytest tests/engines/test_review_with_line_items_live.py -q

Once invoice tables reached the model as rows (F-265), extractions carry `line_items`, a list.
The review screen sent it back with the reviewer's decision and the resolve API refused the
whole decision (lists are document fragments, not values); when the hold asked for every
field (calibrated autonomy, an escalation, extraction memory) the API demanded the list it
refuses. A list or object field is now shown but never part of the decision: it keeps its
extracted value, and the single values are decided as before.
"""

from __future__ import annotations

from tests.engines.conftest import Engines
from tests.engines.test_verification_live import TEXT, TRUTH, _setup, _verification

LINES = [{"line": 1, "description": "Industrial safety gloves", "quantity": 100, "unit_price": "5.00",
          "amount": "500.00"}]


def _held(engines: Engines, review_all: bool):
    _setup(engines, agents=2)
    with_lines = {**TRUTH, "line_items": LINES}
    wrong = {**with_lines, "invoice_number": "PO-777"}
    work_item_id = engines.process("lines.pdf", [TEXT], marker="INV-VER-1", classification="Invoice",
                                   entities=wrong, agents=[wrong, with_lines])
    verification = _verification(engines, work_item_id)
    assert verification.status.value == "DISAGREED", verification.status
    if review_all:
        verification.details = {**(verification.details or {}), "escalation": {"review_all_fields": True}}
        engines.db.commit()
    return work_item_id, verification


def _scalar_decision(engines: Engines, verification, review_all: bool) -> dict:
    fields = [f for f in verification.fields if review_all or not f.agreed]
    return {f.field_path: ("INV-VER-1" if f.field_path == "invoice_number" else f.consensus_value)
            for f in fields if not isinstance(f.consensus_value, (list, dict))}


def test_a_disagreement_beside_line_items_is_resolved_with_values_only(engines: Engines) -> None:
    work_item_id, verification = _held(engines, review_all=False)
    resolved = engines.post(f"/verifications/{verification.id}/resolve",
                            {"values": _scalar_decision(engines, verification, review_all=False)})
    assert resolved.status_code == 200, resolved.text
    entities = engines.item(work_item_id).extracted_entities
    assert entities["invoice_number"] == "INV-VER-1" and entities["line_items"] == LINES


def test_a_hold_that_asks_for_every_field_does_not_ask_for_the_line_items(engines: Engines) -> None:
    work_item_id, verification = _held(engines, review_all=True)
    resolved = engines.post(f"/verifications/{verification.id}/resolve",
                            {"values": _scalar_decision(engines, verification, review_all=True)})
    assert resolved.status_code == 200, resolved.text
    assert engines.item(work_item_id).extracted_entities["line_items"] == LINES
