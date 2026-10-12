"""The RevOps console's routes, as an operator (campaign session 3, platform consoles).

Session 1 checked the money arithmetic; the console's routes were only smoke-tested. Each is
driven here for its effect: a price book is drafted, edited and published (then frozen); a promo
code switched off cannot be used on a contract and switched on again can; a contract activated
for an organization carries its plan and issues the invoices already due, issuing again adds
none, a voided invoice is reissued under a new number, and ending the contract returns the
organization to Free; the sweep reports what it did; tenants never reach any of it.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.services import quota_service
from tests.engines.conftest import Engines
from tests.engines.test_revops_organization_picker_live import _superadmin

API = "/api/v1/admin/revops"


def _call(engines: Engines, method: str, path: str, body=None):
    admin = engines.tenant.non_member
    return engines.client.request(method, f"{API}{path}", json=body, headers=admin.headers)


def test_tenants_reach_none_of_it(engines: Engines) -> None:
    for path in ("/metrics", "/price-books", "/promo-codes", "/contracts"):
        assert engines.client.get(f"{API}{path}", headers=engines.tenant.owner.headers).status_code in (403, 404)


def test_a_price_book_is_drafted_edited_published_and_then_frozen(engines: Engines) -> None:
    _superadmin(engines)
    engines.plan("business")  # a price book prices published plans only
    code = f"TEST-{uuid.uuid4().hex[:8]}"
    created = _call(engines, "POST", "/price-books", {"code": code, "currency": "USD", "notes": "Q4 list"})
    assert created.status_code == 201 and created.json()["status"] == "DRAFT", created.text
    book = created.json()["id"]

    entry = {"tier_key": "business", "interval": "month", "unit_amount": 29900}
    added = _call(engines, "PUT", f"/price-books/{book}/entries", entry)
    assert added.status_code == 200, added.text
    entries = added.json()["entries"]
    assert [(e["tier_key"], e["billing_interval"]) for e in entries] == [("business", "month")]
    removed = _call(engines, "DELETE", f"/price-books/{book}/entries/{entries[0]['id']}")
    assert removed.status_code == 200 and removed.json()["entries"] == [], removed.text
    assert _call(engines, "PUT", f"/price-books/{book}/entries", entry).status_code == 200

    published = _call(engines, "POST", f"/price-books/{book}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "PUBLISHED" and published.json()["content_digest"]
    read = _call(engines, "GET", f"/price-books/{book}")
    assert read.json()["status"] == "PUBLISHED" and len(read.json()["entries"]) == 1
    frozen = _call(engines, "PUT", f"/price-books/{book}/entries", {**entry, "unit_amount": 1})
    assert frozen.status_code in (409, 422), "a published price book is not edited in place"


def _contract(engines: Engines, *, promo: str | None = None) -> dict:
    today = date.today()
    body = {
        "organization_id": str(engines.org), "contract_number": f"C-{uuid.uuid4().hex[:8]}",
        "tier_key": "enterprise", "seats": 10, "currency": "USD", "billing_interval": "month",
        "amount_per_period": 500_000, "term_start": str(today - timedelta(days=40)),
        "term_end": str(today + timedelta(days=320)),
    }
    if promo:
        body["promo_code"] = promo
    return body


def _plans(engines: Engines) -> None:
    """Publish the plans a contract moves between, and start the organization on Free."""
    engines.plan("enterprise")
    engines.plan("free")


def test_a_switched_off_promo_code_cannot_be_used(engines: Engines) -> None:
    _superadmin(engines)
    _plans(engines)
    code = f"OFF{uuid.uuid4().hex[:6]}".upper()
    promo = _call(engines, "POST", "/promo-codes", {"code": code, "percent_off": 10, "duration": "ONCE"})
    assert promo.status_code == 201, promo.text
    off = _call(engines, "PUT", f"/promo-codes/{promo.json()['id']}/active", {"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False, off.text

    draft = _call(engines, "POST", "/contracts", _contract(engines, promo=code))
    assert draft.status_code == 201, draft.text  # a draft may name it; it is checked when it takes effect
    refused = _call(engines, "POST", f"/contracts/{draft.json()['id']}/activate")
    assert refused.status_code in (409, 422), refused.text

    on = _call(engines, "PUT", f"/promo-codes/{promo.json()['id']}/active", {"is_active": True})
    assert on.status_code == 200 and on.json()["is_active"] is True
    active = _call(engines, "POST", f"/contracts/{draft.json()['id']}/activate")
    assert active.status_code == 200, active.text
    first = sorted(active.json()["invoices"], key=lambda i: i["period_start"])[0]
    assert first["discount_micros"] == first["subtotal_micros"] // 10, first


def test_a_contract_carries_the_plan_and_invoices_what_is_due(engines: Engines) -> None:
    _superadmin(engines)
    _plans(engines)
    created = _call(engines, "POST", "/contracts", _contract(engines))
    assert created.status_code == 201 and created.json()["status"] == "DRAFT", created.text
    contract = created.json()["id"]
    assert _call(engines, "GET", f"/contracts/{contract}").json()["contract_number"] == created.json()["contract_number"]

    active = _call(engines, "POST", f"/contracts/{contract}/activate")
    assert active.status_code == 200 and active.json()["status"] == "ACTIVE", active.text
    quota_service.clear_cache()
    engines.db.expire_all()
    assert quota_service.resolve_tier(engines.db, organization_id=engines.org).key == "enterprise"
    issued = [i for i in active.json()["invoices"] if i["status"] == "ISSUED"]
    assert len(issued) == 2, active.json()["invoices"]  # two monthly periods have begun

    again = _call(engines, "POST", f"/contracts/{contract}/issue")
    assert again.status_code == 200 and len(again.json()["invoices"]) == 2, "issuing again adds nothing"

    first = sorted(issued, key=lambda i: i["period_start"])[0]
    voided = _call(engines, "POST", f"/invoices/{first['id']}/void", {"reason": "wrong PO"})
    assert voided.status_code == 200, voided.text
    reissued = _call(engines, "POST", f"/contracts/{contract}/issue").json()["invoices"]
    numbers = {i["invoice_number"] for i in reissued if i["status"] == "ISSUED"}
    assert f"{first['invoice_number']}R1" in numbers, numbers

    ended = _call(engines, "POST", f"/contracts/{contract}/end", {"reason": "CANCELLED"})
    assert ended.status_code == 200 and ended.json()["status"] == "CANCELLED", ended.text
    quota_service.clear_cache()
    engines.db.expire_all()
    assert quota_service.resolve_tier(engines.db, organization_id=engines.org).key == "free"


def test_the_sweep_reports_what_it_did(engines: Engines) -> None:
    _superadmin(engines)
    swept = _call(engines, "POST", "/sweep")
    assert swept.status_code == 200, swept.text
    assert set(swept.json()) >= {"contracts_ended", "invoices_issued", "reservations_expired"}
    assert all(isinstance(value, int) and value >= 0 for value in swept.json().values())
