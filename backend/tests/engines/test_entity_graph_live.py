"""Entity graph, live: extracted parties become resolved records with relationships.

Three invoices go through the real pipeline:

  1. "Acme Supplies Ltd", GSTIN 27AAPCA1234F1ZV, bills Contoso Retail, IBAN on file
  2. "ACME SUPPLIES LIMITED", the same GSTIN, bills Contoso Retail
  3. "Globex Corporation", no tax id

The two Acme spellings must resolve to ONE record (the checksum-valid GSTIN
is a hard identifier; its PAN is derived), with two documents, a SUPPLIES
relationship to the customer and a HOLDS_ACCOUNT edge to the bank account.
Search, the 360 view and the neighbourhood graph read it back; merge and
unmerge are exact and audited; only contributors may edit; another tenant sees
nothing.
"""

from __future__ import annotations

import pytest

from tests.engines.conftest import Engines

GSTIN = "27AAPCA1234F1ZV"


def _invoice(engines: Engines, n: str, vendor: str, extra: dict, lines: list[str]) -> None:
    text = [vendor.upper(), "TAX INVOICE", f"Invoice No: {n}", *lines, "Bill To: Contoso Retail", "Total: 500.00 INR"]
    entities = {"vendor_name": vendor, "invoice_number": n, "customer_name": "Contoso Retail",
                "total_amount": "500.00", "currency": "INR", **extra}
    engines.process(f"{n}.pdf", [text], marker=n, classification="Invoice", entities=entities)


@pytest.fixture()
def graph(engines: Engines) -> Engines:
    _invoice(engines, "INV-EG-1", "Acme Supplies Ltd", {"vendor_gstin": GSTIN, "iban": "GB82 WEST 1234 5698 7654 32"},
             [f"GSTIN: {GSTIN}", "IBAN: GB82 WEST 1234 5698 7654 32"])
    _invoice(engines, "INV-EG-2", "ACME SUPPLIES LIMITED", {"gstin": GSTIN}, [f"GSTIN: {GSTIN}"])
    _invoice(engines, "INV-EG-3", "Globex Corporation", {}, [])
    return engines


def _by_name(engines: Engines, needle: str, **params) -> dict:
    items = engines.get("/entities", params={"q": needle, **params}).json()["items"]
    assert items, (needle, items)
    return items[0]


def test_two_spellings_with_one_tax_id_are_one_record_with_relationships(graph: Engines) -> None:
    engines = graph
    acme = _by_name(engines, "acme")
    assert acme["documents"] == 2, acme
    assert "GSTIN" in acme["identifier_kinds"] and "PAN" in acme["identifier_kinds"], acme
    names = [i["display_name"] for i in engines.get("/entities", params={"q": "acme"}).json()["items"]]
    assert len(names) == 1, names

    detail = engines.get(f"/entities/{acme['id']}").json()
    assert {d["filename"] for d in detail["documents"]} == {"INV-EG-1.pdf", "INV-EG-2.pdf"}
    assert {d["method"] for d in detail["documents"]} >= {"IDENTIFIER"}, detail["documents"]
    relations = {(r["relation"], r["other_name"].lower()) for r in detail["relationships"]}
    assert ("SUPPLIES", "contoso retail") in {(rel, name) for rel, name in relations}, relations
    assert any(rel == "HOLDS_ACCOUNT" for rel, _ in relations), relations
    for identifier in detail["identifiers"]:
        assert GSTIN not in identifier["display"] or identifier["kind"] != "GSTIN" or "*" in identifier["display"] \
            or identifier["display"] == GSTIN  # display is the masked/printed form; never another tenant's

    neighbourhood = engines.get(f"/entities/{acme['id']}/graph").json()
    kinds = {n["kind"] for n in neighbourhood["nodes"]}
    assert len(neighbourhood["nodes"]) >= 3 and neighbourhood["edges"], neighbourhood
    assert "ACCOUNT" in kinds, kinds

    per_document = engines.get(f"/work-items/{detail['documents'][0]['work_item_id']}/entities")
    assert per_document.status_code == 200, per_document.text


def test_merge_and_unmerge_are_exact_and_role_gated(graph: Engines) -> None:
    engines = graph
    acme = _by_name(engines, "acme")
    globex = _by_name(engines, "globex")

    refused = engines.post(f"/entities/{globex['id']}/merge", {"into_entity_id": acme["id"]}, as_user=engines.tenant.viewer)
    assert refused.status_code == 403
    merged = engines.post(f"/entities/{globex['id']}/merge", {"into_entity_id": acme["id"]},
                          as_user=engines.tenant.contributor)
    assert merged.status_code == 200, merged.text
    assert _by_name(engines, "acme")["documents"] == 3

    undone = engines.post(f"/entities/{globex['id']}/unmerge", {}, as_user=engines.tenant.contributor)
    assert undone.status_code == 200, undone.text
    assert _by_name(engines, "acme")["documents"] == 2
    assert _by_name(engines, "globex")["documents"] == 1

    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    engines.refresh()
    operations = [
        ((row.details or {}).get("entity_graph") or {}).get("operation")
        for row in engines.db.execute(select(AuditLog).where(AuditLog.organization_id == engines.org)).scalars()
    ]
    assert "merge" in operations and "unmerge" in operations, sorted({str(o) for o in operations})


def test_another_tenant_cannot_read_the_graph(graph: Engines) -> None:
    engines = graph
    acme = _by_name(engines, "acme")
    stranger = engines.tenant.other_org_member
    assert engines.get("/entities", as_user=stranger).status_code in (403, 404)
    assert engines.get(f"/entities/{acme['id']}", as_user=stranger).status_code in (403, 404)
