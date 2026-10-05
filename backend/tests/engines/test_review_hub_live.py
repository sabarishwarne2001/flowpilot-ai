"""Human review hub, live: the whole lifecycle of extraction reviews.

Three invoices reach the hub because their verification agents split on the
invoice number (Business plan: the fixed threshold decides). Then:

* the queue lists all three, with per-kind counts and the version each one
  was read at;
* an item is assigned to a contributor and the assignee list counts it;
* resolving with the reviewer's value writes it into the document, closes the
  item, emits trigger.review.cleared and lowers the queue count;
* a second decision on the same item, or one made against a version
  somebody else already changed, is refused (409), never silently applied;
* a bulk resolve settles the rest in one call, per-item results reported;
* a VIEWER is refused the hub (owner decision N-019 still open: current rule).
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.outbox_event import OutboxEvent
from tests.engines.conftest import Engines

BASE = {"vendor_name": "Acme Supplies Ltd", "total_amount": "120.00", "currency": "INR"}


def _held(engines: Engines, n: int) -> str:
    right = {**BASE, "invoice_number": f"INV-RH-{n}"}
    wrong = {**BASE, "invoice_number": f"PO-RH-{n}"}
    return str(engines.process(f"rh-{n}.pdf", [["ACME SUPPLIES LTD", "TAX INVOICE", f"Invoice No: INV-RH-{n}",
                                                f"PO Reference: PO-RH-{n}", "Total: 120.00 INR"]],
                               marker=f"INV-RH-{n}", classification="Invoice", entities=wrong, agents=[wrong, right]))


def test_the_review_lifecycle(engines: Engines) -> None:
    engines.plan("business")
    assert engines.put("/document-settings/", {"verification_enabled": True, "verification_agents": 2}).status_code == 200
    docs = [_held(engines, n) for n in (1, 2, 3)]

    queue = engines.get("/review", params={"kind": "EXTRACTION"}, as_user=engines.tenant.contributor).json()
    assert queue["counts_by_kind"]["EXTRACTION"] == 3, queue["counts_by_kind"]
    items = {str(i["work_item_id"]): i for i in queue["items"]}
    assert set(items) == set(docs)
    first = items[docs[0]]
    assert first["review_reason"] == "DISAGREEMENT" and first["status"] == "OPEN"

    assert engines.get("/review", as_user=engines.tenant.viewer).status_code == 403

    assigned = engines.post(f"/review/EXTRACTION/{first['item_id']}/assign",
                            {"assignee_user_id": str(engines.tenant.contributor.user.id)})
    assert assigned.status_code == 204, assigned.text
    assignees = {a["user_id"]: a for a in engines.get("/review/assignees").json()}
    assert assignees[str(engines.tenant.contributor.user.id)]["open_items"] == 1, assignees

    resolved = engines.post(f"/review/EXTRACTION/{first['item_id']}/resolve",
                            {"values": {"invoice_number": "INV-RH-1"}, "expected_version": first["version"]},
                            as_user=engines.tenant.contributor)
    assert resolved.status_code == 200, resolved.text
    assert engines.item(docs[0]).extracted_entities["invoice_number"] == "INV-RH-1"
    after = engines.get("/review", params={"kind": "EXTRACTION"}).json()
    assert after["counts_by_kind"]["EXTRACTION"] == 2

    again = engines.post(f"/review/EXTRACTION/{first['item_id']}/resolve", {"values": {"invoice_number": "X"}},
                         as_user=engines.tenant.ws_admin)
    assert again.status_code in (404, 409), again.text
    assert engines.item(docs[0]).extracted_entities["invoice_number"] == "INV-RH-1"

    # Somebody else acts on the second item after the contributor read it; the contributor's decision,
    # made against the version they read, must be refused rather than overwrite theirs.
    second = items[docs[1]]
    engines.post(f"/review/EXTRACTION/{second['item_id']}/assign", {"assignee_user_id": str(engines.tenant.ws_admin.user.id)})
    reread = {str(i["work_item_id"]): i for i in engines.get("/review", params={"kind": "EXTRACTION"}).json()["items"]}
    if reread[docs[1]]["version"] != second["version"]:
        stale = engines.post(f"/review/EXTRACTION/{second['item_id']}/resolve",
                             {"values": {"invoice_number": "INV-RH-2"}, "expected_version": second["version"]},
                             as_user=engines.tenant.contributor)
        assert stale.status_code == 409, stale.text
    else:  # assignment does not version the item: prove the guard with a version from the future
        stale = engines.post(f"/review/EXTRACTION/{second['item_id']}/resolve",
                             {"values": {"invoice_number": "INV-RH-2"}, "expected_version": second["version"] + 5},
                             as_user=engines.tenant.contributor)
        assert stale.status_code == 409, stale.text

    bulk = engines.post("/review/bulk", {
        "action": "resolve", "kind": "EXTRACTION", "idempotency_key": "rh-bulk-0001",
        "ids": [items[docs[1]]["item_id"], items[docs[2]]["item_id"]],
        "payload": {"values": {"invoice_number": "INV-RH-BULK"}},
    }, as_user=engines.tenant.contributor)
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["ok"] == 2, bulk.json()
    assert engines.get("/review", params={"kind": "EXTRACTION"}).json()["counts_by_kind"]["EXTRACTION"] == 0

    engines.refresh()
    cleared = [e for e in engines.db.execute(select(OutboxEvent.event_type)).scalars() if e == "trigger.review.cleared"]
    assert len(cleared) == 3, cleared
