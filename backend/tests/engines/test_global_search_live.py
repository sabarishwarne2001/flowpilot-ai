"""Global search (Ctrl+K), live: documents, entities and cases across workspaces.

Workspace A holds an Acme purchase order and invoice (INV-SRCH-A1) assembled
into a case; workspace B, in the same organization, holds another invoice
(INV-SRCH-B1). Then:

* the organization OWNER finds both invoices by number, each labelled with
  its workspace, with the matching field on the second line;
* a CONTRIBUTOR granted only workspace A finds A's invoice and never B's;
* "Acme" finds the entity record and the case;
* the query is literal: "%" does not match everything;
* one character is refused (422); another organization's member is refused.
"""

from __future__ import annotations

from tests.engines.conftest import Engines, drain, make_pdf
from tests.engines.test_cases_live import _case, _invoice, _po, _template


def _search(engines: Engines, q: str, as_user=None):
    return engines.get("/search", org=True, params={"q": q}, as_user=as_user)


def test_search_finds_documents_entities_and_cases_only_where_you_may_look(engines: Engines) -> None:
    template = _template(engines, "po-invoice")
    po = _po(engines, "PO-SRCH-A1", "Acme Supplies Ltd", "500.00")
    invoice = _invoice(engines, "INV-SRCH-A1", "Acme Supplies Ltd", "500.00", "PO-SRCH-A1")
    case = _case(engines, template, "Acme August bundle", [po, invoice])

    created = engines.post("/workspaces", {"workspace_name": "Branch office"}, org=True)
    assert created.status_code == 201, created.text
    branch = created.json()["id"]
    engines.llm.record("INV-SRCH-B1", "Invoice", {"invoice_number": "INV-SRCH-B1", "vendor_name": "Globex"})
    uploaded = engines.client.post(f"/api/v1/workspaces/{branch}/work-items",
                                   files={"file": ("b.pdf", make_pdf([["TAX INVOICE", "Invoice No: INV-SRCH-B1"]]),
                                                   "application/pdf")},
                                   headers=engines.tenant.owner.headers)
    assert uploaded.status_code == 201, uploaded.text
    drain()

    everything = _search(engines, "inv-srch")
    assert everything.status_code == 200, everything.text
    body = everything.json()
    found = {(d["title"], d["workspace_id"]) for d in body["documents"]}
    assert ("INV-SRCH-A1.pdf", str(engines.ws)) in found and ("b.pdf", branch) in found, body
    assert body["workspaces_searched"] == 2
    a_hit = next(d for d in body["documents"] if d["title"] == "INV-SRCH-A1.pdf")
    assert a_hit["subtitle"] == "invoice number: INV-SRCH-A1", a_hit
    assert a_hit["workspace_slug"] and a_hit["workspace_name"]

    mine_only = _search(engines, "INV-SRCH", as_user=engines.tenant.contributor).json()
    assert mine_only["workspaces_searched"] == 1
    assert {d["title"] for d in mine_only["documents"]} == {"INV-SRCH-A1.pdf"}, mine_only
    assert all(d["workspace_id"] == str(engines.ws) for d in mine_only["documents"])

    acme = _search(engines, "acme").json()
    assert any(e["title"].lower().startswith("acme") for e in acme["entities"]), acme["entities"]
    assert [c["id"] for c in acme["cases"]] == [case["case"]["id"]], acme["cases"]

    assert _search(engines, "%").status_code == 422  # one character
    literal = _search(engines, "%%").json()
    assert literal["documents"] == [] and literal["entities"] == [] and literal["cases"] == []
    assert _search(engines, "_x").json()["documents"] == []

    outsider = engines.client.get(engines.org_url("/search"), params={"q": "INV-SRCH"},
                                  headers=engines.tenant.other_org_member.headers)
    assert outsider.status_code in (403, 404), outsider.text
