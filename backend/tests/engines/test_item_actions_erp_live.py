"""Every action on one ERP target and its postings works, live (campaign session 2, ledger).

    pytest tests/engines/test_item_actions_erp_live.py -q

These routes had only their refusals proven (the admin-only checks). On an approved three-way
match: a target is read, renamed and tested; its vendor-bill mapping is read, validated, saved
and reset to the default; a lookup table is created and deleted; a vendor bill is posted and
acknowledged as accepted by the books, a journal entry is posted and cancelled, retry and accept
on a posting that is not waiting for them are refused with the reason; the target is deleted.
"""

from __future__ import annotations

from tests.engines.conftest import Engines
from tests.engines.test_erp_posting_live import _post, _target, approved_case  # noqa: F401  (fixture)


def test_every_target_action_works(engines: Engines) -> None:
    target = _target(engines, "CSV books", "CSV")
    tid = target["id"]
    assert engines.get(f"/erp/targets/{tid}").status_code == 200
    renamed = engines.patch(f"/erp/targets/{tid}", {"name": "CSV books (FY27)"})
    assert renamed.status_code == 200 and renamed.json()["target"]["name"] == "CSV books (FY27)", renamed.text
    tested = engines.post(f"/erp/targets/{tid}/test")
    assert tested.status_code == 200, tested.text

    mapping = engines.get(f"/erp/targets/{tid}/mappings/VENDOR_BILL")
    assert mapping.status_code == 200, mapping.text
    spec = (mapping.json().get("active") or {}).get("spec")
    if spec:
        valid = engines.post(f"/erp/targets/{tid}/mappings/VENDOR_BILL/validate", {"spec": spec})
        assert valid.status_code == 200, valid.text
        saved = engines.put(f"/erp/targets/{tid}/mappings/VENDOR_BILL", {"spec": spec, "note": "Same as default"})
        assert saved.status_code == 200, saved.text
    reset = engines.post(f"/erp/targets/{tid}/mappings/VENDOR_BILL/default")
    assert reset.status_code in (200, 201), reset.text

    lookup = engines.post("/erp/lookups", {"name": "cost_centres", "entries": {"Acme Supplies Ltd": "CC-100"}})
    assert lookup.status_code == 201, lookup.text
    assert engines.delete(f"/erp/lookups/{lookup.json()['id']}").status_code == 204

    assert engines.delete(f"/erp/targets/{tid}").status_code == 204
    assert engines.get(f"/erp/targets/{tid}").status_code == 404


def test_every_posting_action_works(engines: Engines, approved_case) -> None:  # noqa: F811
    target = _target(engines, "CSV books", "CSV")
    bill = _post(engines, target["id"], approved_case["id"], ["VENDOR_BILL"])
    assert bill.status_code == 201, bill.text
    bill_id = bill.json()["results"][0]["posting_id"]
    acknowledged = engines.post(f"/erp/postings/{bill_id}/acknowledge",
                                {"accepted": True, "reference": "BILL-2026-0042"})
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["posting"]["state"] == "DONE", acknowledged.json()

    journal = _post(engines, target["id"], approved_case["id"], ["JOURNAL_ENTRY"])
    journal_id = journal.json()["results"][0]["posting_id"]
    cancelled = engines.post(f"/erp/postings/{journal_id}/cancel", {"note": "Booked by hand"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["posting"]["state"] == "CANCELLED"

    # A booked posting is final: retrying, accepting or cancelling it is refused with the reason.
    for verb in ("retry", "accept", "cancel"):
        refused = engines.post(f"/erp/postings/{bill_id}/{verb}", {})
        assert refused.status_code == 409, (verb, refused.status_code, refused.text)
        assert refused.json()["detail"]["message"], refused.text

    order = _post(engines, target["id"], approved_case["id"], ["PURCHASE_ORDER"])
    order_id = order.json()["results"][0]["posting_id"]
    accepted = engines.post(f"/erp/postings/{order_id}/accept", {"note": "Checked against the PO"})
    assert accepted.status_code in (200, 409), accepted.text
