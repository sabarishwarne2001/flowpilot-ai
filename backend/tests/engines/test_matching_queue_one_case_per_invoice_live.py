"""The matching queue holds one case per invoice, and flags the copies (campaign session 2, F-264).

    pytest tests/engines/test_matching_queue_one_case_per_invoice_live.py -q

The same invoice often arrives twice: emailed and then posted, rescanned, re-sent by a
supplier chasing payment. Each copy was scored as its own invoice, so the queue showed one
case per copy, a reviewer could approve the same bill twice, and nothing said "this is a
second copy". Decided (N-055): one case per invoice (same vendor and invoice number, or the
same bytes), the first to arrive; later copies open no case and are listed on it as
duplicates, so the reviewer sees the duplicate-payment risk where the decision is made.
"""

from __future__ import annotations

from tests.engines.conftest import Engines, drain
from tests.engines.test_three_way_matching_live import _grn, _invoice, _po


def _live_cases(engines: Engines) -> list[dict]:
    response = engines.get("/procurement/cases")
    assert response.status_code == 200, response.text
    return response.json()


def _run(engines: Engines, tag: str, vendor: str) -> None:
    for kind, (text, entities) in (
        ("Purchase Order", _po(f"PO-{tag}", vendor, 100, "2.50")),
        ("Goods Receipt", _grn(f"GRN-{tag}", f"PO-{tag}", vendor, 100)),
        ("Invoice", _invoice(f"INV-{tag}", f"PO-{tag}", vendor, 100, "2.50")),
    ):
        marker = text[2].split(": ")[1]
        engines.process(f"{marker}.pdf", [text], marker=marker, classification=kind, entities=entities)


def test_a_second_copy_of_an_invoice_opens_no_case_and_is_flagged_on_the_first(engines: Engines) -> None:
    vendor = "Initech Fasteners"
    _run(engines, "DUP1", vendor)
    [case] = [c for c in _live_cases(engines) if "initech" in (c["vendor_key"] or "")]

    # The same invoice again, rescanned: different bytes and name, same vendor and number.
    text, entities = _invoice("INV-DUP1", "PO-DUP1", vendor, 100, "2.50")
    copy_id = engines.process("INV-DUP1-rescan.pdf", [[*text, "Scanned copy"]], marker="INV-DUP1",
                              classification="Invoice", entities=entities)
    drain(only=["procurement.score"])

    cases = [c for c in _live_cases(engines) if "initech" in (c["vendor_key"] or "")]
    assert len(cases) == 1, f"the queue shows one case per copy: {[c['invoice_work_item_id'] for c in cases]}"
    assert cases[0]["id"] == case["id"], "the first copy's case stays the case"

    detail = engines.get(f"/procurement/cases/{case['id']}").json()
    copies = detail.get("duplicate_copies")
    assert copies is not None, "the case does not say a copy arrived"
    assert [c["work_item_id"] for c in copies] == [str(copy_id)]
    assert copies[0]["original_filename"] == "INV-DUP1-rescan.pdf"
    assert cases[0].get("duplicate_count") == 1


def test_a_different_invoice_from_the_same_vendor_is_its_own_case(engines: Engines) -> None:
    vendor = "Initech Fasteners"
    _run(engines, "DUP2", vendor)
    _run(engines, "DUP3", vendor)
    cases = [c for c in _live_cases(engines) if "initech" in (c["vendor_key"] or "")]
    assert len(cases) == 2
    assert all(c.get("duplicate_count") == 0 for c in cases)


def test_a_case_a_copy_opened_before_this_change_leaves_the_queue(engines: Engines, monkeypatch) -> None:
    """Deploy-time: copies scored by the old code have their own live cases. Any scoring of the group retires them."""
    from app.services.procurement_matching import case_service

    vendor = "Initech Fasteners"
    _run(engines, "DUP4", vendor)
    text, entities = _invoice("INV-DUP4", "PO-DUP4", vendor, 100, "2.50")
    with monkeypatch.context() as old_code:
        old_code.setattr(case_service, "first_copy_of", lambda *a, **k: None)
        old_code.setattr(case_service, "retire_later_copy_cases", lambda *a, **k: [])
        engines.process("INV-DUP4-email.pdf", [[*text, "Emailed copy"]], marker="INV-DUP4",
                        classification="Invoice", entities=entities)
    assert len([c for c in _live_cases(engines) if "initech" in (c["vendor_key"] or "")]) == 2

    engines.process("INV-DUP4-post.pdf", [[*text, "Posted copy"]], marker="INV-DUP4",
                    classification="Invoice", entities=entities)

    cases = [c for c in _live_cases(engines) if "initech" in (c["vendor_key"] or "")]
    assert len(cases) == 1, "a copy's old case is still in the queue"
    assert cases[0]["duplicate_count"] == 2
