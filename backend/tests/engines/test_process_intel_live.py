"""Process intelligence, live: the event log is built from what really happened to documents.

A PO, a goods receipt and an invoice go through the pipeline and the
three-way match; the case is approved. Then an administrator runs the sweep:

* the object-centric event log has events for each DOCUMENT (from jobs,
  outbox and audit rows) and for the PROCUREMENT case, idempotently (a second
  sweep writes nothing new);
* discovery returns the activity graph, its variants and throughput times;
* an object's timeline lists its events in order;
* an SLA policy (cases, review items, postings) can be set by an admin only; a VIEWER may read but not sweep.
"""

from __future__ import annotations

from tests.engines.conftest import Engines
from tests.engines.isolation import assert_workspace_isolated
from tests.engines.test_three_way_matching_live import _set


def test_the_event_log_discovery_and_sla(engines: Engines) -> None:
    engines.post("/procurement/policies", {"price_tolerance_bps": 500})
    case = _set(engines, "PI-1", "Acme Supplies Ltd", received=100, invoice_price="2.50")
    assert engines.post(f"/procurement/cases/{case['id']}/approve", {}).status_code == 200

    assert engines.post("/process/sweep", as_user=engines.tenant.viewer).status_code == 403
    first = engines.post("/process/sweep")
    assert first.status_code == 200, first.text
    overview = engines.get("/process/overview", as_user=engines.tenant.viewer)
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["events"] > 0, body
    types = {t["object_type"]: t for t in body["object_types"]}
    assert "DOCUMENT" in types, types

    # The first sweep's exception agent may propose something, which the next sweep reads as a new
    # event; after that, re-reading the same sources writes nothing twice.
    assert engines.post("/process/sweep").status_code == 200
    settled = engines.get("/process/overview").json()["events"]
    assert engines.post("/process/sweep").status_code == 200
    assert engines.get("/process/overview").json()["events"] == settled

    discovery = engines.get("/process/discovery", params={"object_type": "DOCUMENT", "days": 30}).json()
    assert discovery["variant_count"] >= 1, discovery
    assert discovery["graph"].get("nodes") or discovery["graph"].get("activities"), discovery["graph"]
    assert discovery["throughput_seconds"], discovery

    timeline = engines.get(f"/process/objects/DOCUMENT/{case['invoice_work_item_id']}")
    assert timeline.status_code == 200, timeline.text
    stamps = [e["occurred_at"] for e in timeline.json()["events"]]
    assert stamps and stamps == sorted(stamps)

    policy = {"target_hours": 4, "at_risk_probability": 0.6, "alerts_enabled": True}
    assert engines.put("/process/sla/CASE", policy, as_user=engines.tenant.contributor).status_code == 403
    saved = engines.put("/process/sla/CASE", policy)
    assert saved.status_code == 200, saved.text
    sla = engines.get("/process/sla").json()
    assert any(p["object_type"] == "CASE" and p["target_hours"] == 4 for p in sla["policies"]), sla
    assert_workspace_isolated(engines, collections=("process",))
