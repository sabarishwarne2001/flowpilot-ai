"""Document corroborator, live: two versions of one agreement compared clause by clause.

Version A and version B of a supply agreement agree on most clauses but B
changes three material things: payment from 30 to 60 days, the liability
cap from INR 10,00,000 to INR 1,00,000, and the governing law from India to
Singapore; B also drops the audit clause. The comparison must:

* align the clauses and report each change as a discrepancy with both values
  and its evidence, with a materiality score and severity;
* count the material differences and expose them in the matrix;
* emit the automation trigger once and offer a CSV export;
* let a reviewer confirm / dismiss a discrepancy, and refuse a VIEWER.
"""

from __future__ import annotations

from tests.engines.conftest import Engines, drain

COMMON = [
    "1. Parties. This Supply Agreement is between Contoso Retail Private Limited and Acme Supplies Ltd.",
    "2. Supply. The Supplier shall supply steel fasteners as described in each purchase order.",
]


def _version(marker: str, days: int, cap: str, law: str, audit: bool) -> list[str]:
    lines = [f"SUPPLY AGREEMENT {marker}", *COMMON,
             f"3. Payment. The Customer shall pay each invoice within {days} days of receipt.",
             f"4. Liability. The Supplier's aggregate liability shall not exceed INR {cap}.",
             f"5. Governing law. This Agreement is governed by the laws of {law}."]
    if audit:
        lines.append("6. Audit. The Customer may audit the Supplier's records once in each calendar year.")
    return lines


def test_two_versions_are_compared_clause_by_clause(engines: Engines) -> None:
    a = engines.process("v1.pdf", [_version("CORR-V1", 30, "10,00,000", "India", True)], marker="CORR-V1",
                        classification="Contract", entities={"party_names": ["Contoso Retail", "Acme Supplies Ltd"],
                                                             "governing_law": "India"})
    b = engines.process("v2.pdf", [_version("CORR-V2", 60, "1,00,000", "Singapore", False)], marker="CORR-V2",
                        classification="Contract", entities={"party_names": ["Contoso Retail", "Acme Supplies Ltd"],
                                                             "governing_law": "Singapore"})

    assert engines.post("/corroboration/runs", {"work_item_ids": [str(a), str(b)]},
                        as_user=engines.tenant.viewer).status_code == 403
    requested = engines.post("/corroboration/runs", {"work_item_ids": [str(a), str(b)]},
                             as_user=engines.tenant.contributor)
    assert requested.status_code in (200, 202), requested.text
    drain()
    run_id = requested.json()["run"]["id"]
    detail = engines.get(f"/corroboration/runs/{run_id}").json()
    assert detail["run"]["status"] == "COMPLETED", detail["run"]

    discrepancies = detail["discrepancies"]
    text = " | ".join(f"{d['kind']}: {d['summary']} {d['values']}" for d in discrepancies)
    assert any("60" in str(d["values"]) and "30" in str(d["values"]) for d in discrepancies), text
    assert any("1,00,000" in str(d["values"]) or "100000" in str(d["values"]) for d in discrepancies), text
    assert any("singapore" in str(d["values"]).lower() for d in discrepancies), text
    assert any(d["kind"] == "CLAUSE_MISSING" for d in discrepancies), text
    assert detail["run"]["material_count"] >= 3, detail["run"]
    assert all(0 <= d["materiality"] <= 1 for d in discrepancies)
    assert all(d["evidence"] for d in discrepancies if d["layer"] == "CLAUSE"), text

    # Asking again for the same documents returns the cached run.
    again = engines.post("/corroboration/runs", {"work_item_ids": [str(b), str(a)]})
    assert again.json()["cached"] is True and again.json()["run"]["id"] == run_id

    export = engines.get(f"/corroboration/runs/{run_id}/export", params={"format": "csv"})
    assert export.status_code == 200 and "Singapore" in export.text, export.text[:300]

    target = discrepancies[0]["id"]
    refused = engines.patch(f"/corroboration/runs/{run_id}/discrepancies/{target}", {"status": "CONFIRMED"},
                            as_user=engines.tenant.viewer)
    assert refused.status_code == 403
    decided = engines.patch(f"/corroboration/runs/{run_id}/discrepancies/{target}",
                            {"status": "CONFIRMED", "note": "Commercial terms changed in v2"},
                            as_user=engines.tenant.contributor)
    assert decided.status_code == 200, decided.text

    from sqlalchemy import select

    from app.models.outbox_event import OutboxEvent

    engines.refresh()
    triggers = [e for e in engines.db.execute(select(OutboxEvent.event_type)).scalars()
                if e == "trigger.corroboration.discrepancies"]
    assert len(triggers) == 1, triggers
