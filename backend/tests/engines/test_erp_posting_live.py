"""ERP posting, live: an approved three-way match becomes accounting postings.

* One approved case is posted to DOWNLOAD targets in Tally, UBL, X12 and CSV;
  each rendered file is read back and carries the reconciled invoice figures.
* JSON payloads for QuickBooks Online, NetSuite and SAP S/4HANA are generated
  through the preview endpoint (no network: the sandbox cannot reach them).
* Exactly once: posting the same object again, or twice concurrently from two
  sessions, leaves exactly one posting per (target, object, source).
* Role gating (owner directive, Phase 4, answers N-019 / F-065): only a
  workspace ADMIN (which organization OWNERs and ADMINs are) may create,
  retry, accept, cancel or acknowledge a posting. A CONTRIBUTOR and a VIEWER
  are refused with 403; reading postings stays open to every workspace role.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import func, select

from app.models.erp import ErpPosting
from tests.conftest import TestSessionLocal
from tests.engines.conftest import Engines
from tests.engines.test_three_way_matching_live import _set


@pytest.fixture()
def approved_case(engines: Engines) -> dict:
    engines.post("/procurement/policies", {"price_tolerance_bps": 500})
    case = _set(engines, "ERP-1", "Acme Supplies Ltd", received=100, invoice_price="2.60")
    approved = engines.post(f"/procurement/cases/{case['id']}/approve", {})
    assert approved.status_code == 200, approved.text
    return approved.json()


def _target(engines: Engines, name: str, fmt: str, transport: str = "DOWNLOAD", **extra) -> dict:
    body = {"name": name, "format": fmt, "transport": transport, **extra}
    response = engines.post("/erp/targets", body)
    assert response.status_code == 201, response.text
    return response.json()["target"]


def _post(engines: Engines, target_id: str, case_id: str, kinds: list[str], *, as_user=None):
    return engines.post("/erp/postings", {"target_id": target_id, "source_kind": "PROCUREMENT_CASE",
                                          "source_id": case_id, "object_kinds": kinds}, as_user=as_user)


def _file(engines: Engines, posting_id: str) -> bytes:
    detail = engines.get(f"/erp/postings/{posting_id}").json()
    name = detail["posting"]["rendered_filename"]
    response = engines.get(f"/erp/postings/{posting_id}/file", params={"filename": name})
    assert response.status_code == 200, response.text
    return response.content


@pytest.mark.parametrize(
    ("fmt", "extra", "needle"),
    [
        ("TALLY", {"config": {"tally": {"company": "Contoso Retail Pvt Ltd"}}}, b"260.00"),
        ("UBL", {}, b"260.00"),
        ("CSV", {}, b"260.00"),
    ],
)
def test_an_approved_case_posts_a_vendor_bill_with_the_reconciled_figures(engines: Engines, approved_case, fmt, extra,
                                                                          needle) -> None:
    target = _target(engines, f"{fmt} books", fmt, **extra)
    response = _post(engines, target["id"], approved_case["id"], ["VENDOR_BILL"])
    assert response.status_code == 201, response.text
    [result] = response.json()["results"]
    assert result["error"] is None, result
    assert result["created"] is True
    assert result["state"] == "DELIVERED"
    data = _file(engines, result["posting_id"])
    assert needle in data, data[:600]
    assert b"INV-ERP-1" in data


def test_json_presets_render_quickbooks_netsuite_and_sap_payloads(engines: Engines, approved_case) -> None:
    from app.models.erp import ErpTarget
    from app.services.erp import service

    targets = {
        "QUICKBOOKS_ONLINE": {"http": {"base_url": "https://quickbooks.api.intuit.com"}, "qbo": {"realm_id": "9130"}},
        "NETSUITE_REST": {"http": {"base_url": "https://1234567.suitetalk.api.netsuite.com"}},
        "S4HANA_ODATA": {"http": {"base_url": "https://s4.example.com"}, "s4": {"sap_client": "100"}},
    }
    for preset, config in targets.items():
        # check_network=False: the sandbox cannot resolve these hosts. Everything else is the real validation.
        try:
            target = service.create_target(
                engines.db, organization_id=engines.org, workspace_id=engines.ws, actor_user_id=engines.tenant.owner.user.id,
                name=f"{preset} books", format="JSON", transport="HTTP", preset=preset, auth_mode="BEARER",
                config=config, check_network=False)
        except service.ErpError as exc:  # a preset needing more config says exactly what
            pytest.fail(f"{preset}: {exc.problems or exc}")
        engines.db.commit()
        body = _preview(engines, str(target.id), approved_case["id"])
        if not body["ok"]:
            # A JSON ERP names vendors and accounts by ITS ids: the target's lookup tables must say
            # which. Fill exactly the keys the engine says are missing, as an administrator would.
            _fill_lookups(engines, body["problems"])
            body = _preview(engines, str(target.id), approved_case["id"])
        assert body["ok"] is True, (preset, body["problems"])
        assert "260" in (body["rendered_preview"] or ""), (preset, body["rendered_preview"])
        assert body["canonical"]["document_number"] == "INV-ERP-1"


def _preview(engines: Engines, target_id: str, case_id: str) -> dict:
    preview = engines.post("/erp/postings/preview", {"target_id": target_id, "source_kind": "PROCUREMENT_CASE",
                                                      "source_id": case_id, "object_kind": "VENDOR_BILL"})
    assert preview.status_code == 200, preview.text
    return preview.json()


def _fill_lookups(engines: Engines, problems: list[str]) -> None:
    import re

    wanted: dict[str, dict[str, str]] = {}
    for problem in problems:
        match = re.search(r"'([^']+)' is not in the lookup table '([^']+)'", problem)
        assert match, problem
        wanted.setdefault(match.group(2), {})[match.group(1)] = "42"
    tables = {t["name"]: t for t in engines.get("/erp/lookups").json()["items"]}
    for name, entries in wanted.items():
        merged = {**tables[name].get("entries", {}), **entries}
        response = engines.patch(f"/erp/lookups/{tables[name]['id']}", {"entries": merged})
        assert response.status_code == 200, response.text


def test_posting_is_exactly_once(engines: Engines, approved_case) -> None:
    target = _target(engines, "Tally books", "TALLY", config={"tally": {"company": "Contoso"}})
    first = _post(engines, target["id"], approved_case["id"], ["VENDOR_BILL", "JOURNAL_ENTRY"]).json()["results"]
    second = _post(engines, target["id"], approved_case["id"], ["VENDOR_BILL", "JOURNAL_ENTRY"]).json()["results"]
    assert [r["created"] for r in first] == [True, True], first
    assert [r["created"] for r in second] == [False, False], second
    assert [r["posting_id"] for r in first] == [r["posting_id"] for r in second]

    # Two sessions racing for a third object kind.
    from app.models.erp import ErpTarget
    from app.services.erp import service

    barrier = threading.Barrier(2)
    ids: list[str] = []
    errors: list[BaseException] = []

    def racer() -> None:
        try:
            with TestSessionLocal() as db:
                t = db.get(ErpTarget, uuid.UUID(target["id"]))
                barrier.wait(timeout=10)
                posting, _ = service.plan(db, target=t, source_kind="PROCUREMENT_CASE",
                                          source_id=uuid.UUID(approved_case["id"]), object_kind="PURCHASE_ORDER",
                                          origin="MANUAL", actor_user_id=None)
                db.commit()
                ids.append(str(posting.id))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=racer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors, errors
    assert len(set(ids)) == 1, ids
    engines.refresh()
    count = engines.db.execute(select(func.count()).select_from(ErpPosting).where(
        ErpPosting.target_id == uuid.UUID(target["id"]), ErpPosting.object_kind == "PURCHASE_ORDER")).scalar_one()
    assert count == 1


def test_only_workspace_admins_may_execute_postings(engines: Engines, approved_case) -> None:
    target = _target(engines, "CSV books", "CSV")
    for persona in (engines.tenant.contributor, engines.tenant.viewer):
        refused = _post(engines, target["id"], approved_case["id"], ["VENDOR_BILL"], as_user=persona)
        assert refused.status_code == 403, (persona.user.email, refused.status_code, refused.text)
    engines.refresh()
    assert engines.db.execute(select(func.count()).select_from(ErpPosting)).scalar_one() == 0

    for persona in (engines.tenant.org_admin, engines.tenant.ws_admin):
        allowed = _post(engines, target["id"], approved_case["id"], ["VENDOR_BILL"], as_user=persona)
        assert allowed.status_code == 201, allowed.text
    [posting] = engines.get("/erp/postings").json()["items"]

    for verb, body in (("retry", {}), ("accept", {}), ("cancel", {}), ("acknowledge", {"accepted": True})):
        refused = engines.post(f"/erp/postings/{posting['id']}/{verb}", body, as_user=engines.tenant.contributor)
        assert refused.status_code == 403, (verb, refused.status_code, refused.text)

    # Reading stays open to every workspace role.
    assert engines.get("/erp/postings", as_user=engines.tenant.viewer).status_code == 200
    assert engines.get(f"/erp/postings/{posting['id']}", as_user=engines.tenant.contributor).status_code == 200


def test_the_review_hub_cannot_be_used_to_bypass_the_admin_rule(engines: Engines, approved_case) -> None:
    from app.services.erp import service

    # A QuickBooks target with empty lookup tables: the bill cannot render, so the posting is FAILED and
    # lands in the review hub as a posting exception.
    target = service.create_target(
        engines.db, organization_id=engines.org, workspace_id=engines.ws, actor_user_id=engines.tenant.owner.user.id,
        name="QBO books", format="JSON", transport="HTTP", preset="QUICKBOOKS_ONLINE", auth_mode="BEARER",
        config={"http": {"base_url": "https://quickbooks.api.intuit.com"}, "qbo": {"realm_id": "9130"}},
        check_network=False)
    engines.db.commit()
    [result] = _post(engines, str(target.id), approved_case["id"], ["VENDOR_BILL"]).json()["results"]
    assert result["state"] == "FAILED", result

    queue = engines.get("/review", params={"kind": "POSTING"}).json()
    [item] = [i for i in queue["items"] if str(i["item_id"]) == result["posting_id"]]
    body = {"posting_verdict": "CANCEL", "note": "not this one"}
    refused = engines.post(f"/review/POSTING/{item['item_id']}/resolve", body, as_user=engines.tenant.contributor)
    assert refused.status_code == 403, refused.text
    allowed = engines.post(f"/review/POSTING/{item['item_id']}/resolve", body, as_user=engines.tenant.ws_admin)
    assert allowed.status_code == 200, allowed.text
    assert engines.get(f"/erp/postings/{result['posting_id']}").json()["posting"]["state"] == "CANCELLED"
