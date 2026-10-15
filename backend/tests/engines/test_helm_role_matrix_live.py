"""Every Helm route, for every kind of person (box 8: the isolation sweep and the role matrix).

The route list is read from the application itself, so a new Helm route joins the sweep without
anyone remembering to add it. For each route the sweep asks as: nobody signed in; the owner of
another organization; a member (Helm is for owners, admins and billing); a billing member with no
workspace grant; an admin; the owner. Real ids are filled in (the book, a mandate, an action, its
certificate, a bank account and line), so a refusal is the rule, not a missing row. The rules:

* nobody signed in is always refused (401);
* another organization is always refused, and never learns that the row exists (403 or 404);
* a member is always refused (403), apart from the Overview card, which says Helm is not theirs;
* billing never changes mandates, settings, books or the sample company;
* nothing, for anyone, is a server error.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.helm import HelmAction, HelmBook
from app.models.organization import OrganizationRole
from app.services.helm import vocabulary as v
from tests.conftest import _make_user, _seat
from tests.engines.conftest import drain
from tests.security.plans import put_on_plan
from tests.services.helm import factories

PREFIX = "/api/v1/organizations/{organization_id}/helm"
#: Changes only an owner or an admin may make (ARCHITECTURE §9).
DECIDER_ONLY = (
    ("POST", "/books"), ("PATCH", "/books/{book_id}/settings"), ("POST", "/books/{book_id}/mandates"),
    ("PATCH", "/books/{book_id}/mandates/{mandate_id}"), ("POST", "/books/{book_id}/mandates/draft"),
    ("PATCH", "/books/{book_id}/counterparties/{counterparty_id}"), ("POST", "/sample"), ("DELETE", "/sample"),
)


def _routes():
    from app.main import app

    found = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith(PREFIX):
            continue
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            found.append((method, path[len(PREFIX):]))
    return sorted(set(found))


@pytest.fixture()
def world(client, db_session, tenant, test_sessions):
    put_on_plan(db_session, tenant.organization, "business")
    billing = _make_user(db_session, f"billing-{uuid.uuid4().hex[:6]}@acme.com")
    _seat(db_session, tenant.organization, billing, OrganizationRole.BILLING)
    db_session.commit()
    org, ws = tenant.organization.id, tenant.workspace
    api = f"/api/v1/organizations/{org}/helm"
    owner = tenant.owner.headers
    book = client.post(f"{api}/books", json={"workspace_id": str(ws.id)}, headers=owner)
    assert book.status_code == 201, book.text
    book_id = book.json()["id"]
    assert client.patch(f"{api}/books/{book_id}/settings", headers=owner, json={
        "identities": [{"name": "Acme Inc.", "tax_id": None}]}).status_code == 200
    item = factories.invoice(db_session, workspace=ws, organization_id=org, number="RM-1", supplier="Konkan Packaging",
                             amount=50_000, on=date.today() - timedelta(days=20))
    db_session.commit()
    drain(only=[v.JOB_LEDGER_BUILD])
    mandate = client.post(f"{api}/books/{book_id}/mandates", headers=owner,
                          json={"template": "pay_on_time", "activate": True})
    assert mandate.status_code == 201, mandate.text
    assert client.post(f"{api}/books/{book_id}/plan", headers=owner).status_code == 200
    db_session.expire_all()
    action = db_session.execute(select(HelmAction).where(HelmAction.book_id == uuid.UUID(book_id))).scalars().first()
    account = client.post(f"{api}/books/{book_id}/bank-accounts", headers=owner,
                          json={"name": "HDFC current", "number": "50200012346819", "currency": "INR"})
    assert account.status_code == 201, account.text
    counterparty = client.get(f"{api}/books/{book_id}/counterparties", headers=owner).json()
    ids = {
        "organization_id": str(org), "book_id": book_id, "mandate_id": mandate.json()["id"],
        "action_id": str(action.id) if action else str(uuid.uuid4()),
        "certificate_id": str(action.certificate_id) if action and action.certificate_id else str(uuid.uuid4()),
        "account_id": account.json()["id"], "workspace_id": str(ws.id), "work_item_id": str(item.id),
        "counterparty_id": (counterparty[0]["id"] if isinstance(counterparty, list) and counterparty
                            else str(uuid.uuid4())),
    }
    people = {
        "nobody": {}, "other organization": tenant.other_org_member.headers, "member": tenant.contributor.headers,
        "billing": billing.headers, "admin": tenant.org_admin.headers, "owner": owner,
    }
    return {"ids": ids, "people": people}


def _fill(path: str, ids: dict[str, str]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: ids.get(m.group(1), str(uuid.uuid4())), path)


def _call(client, method: str, path: str, headers: dict):
    kwargs: dict = {"headers": headers}
    if method in ("POST", "PATCH", "PUT"):
        if path.endswith("bank-statements") or path.endswith("bank-statements/preview"):
            kwargs["files"] = {"file": ("s.csv", b"Date,Narration,Amount\n01/10/2026,FEE,-15.00\n", "text/csv")}
        else:
            kwargs["json"] = {}
    return client.request(method, path, **kwargs)


def test_every_helm_route_keeps_to_the_role_matrix(client, world):
    routes = _routes()
    assert len(routes) >= 40, routes  # the sweep reads the application's own route list
    problems: list[str] = []
    for person, headers in world["people"].items():
        for method, path in routes:
            url = "/api/v1" + _fill("/organizations/{organization_id}/helm" + path, world["ids"])
            response = _call(client, method, url, headers)
            code = response.status_code
            where = f"{person}: {method} {path} -> {code} {response.text[:160]}"
            if code >= 500:
                problems.append(where)
            elif person == "nobody" and code != 401:
                problems.append(where)
            elif person == "other organization" and code not in (403, 404):
                problems.append(where)
            # An empty body is refused as incomplete (422) before the route asks who is calling; either
            # way it is refused, and a well-formed body is refused in words (the test below).
            elif person == "member" and code not in (403, 422) and path != "/workspaces/{workspace_id}/card":
                problems.append(where)
            elif person == "billing" and (method, path) in DECIDER_ONLY and code not in (403, 422):
                problems.append(where)
    assert problems == [], "\n".join(problems)


def test_well_formed_changes_are_refused_in_words_to_those_who_may_not_make_them(client, world):
    ids, people = world["ids"], world["people"]
    api = f"/api/v1/organizations/{ids['organization_id']}/helm"
    attempts = [
        ("member", "POST", f"{api}/books/{ids['book_id']}/mandates", {"template": "pay_on_time"}),
        ("member", "POST", f"{api}/books/{ids['book_id']}/cash-positions",
         {"currency": "INR", "amount": "100000", "as_of": date.today().isoformat()}),
        ("member", "POST", f"{api}/actions/{ids['action_id']}/reject", {"reason": "no"}),
        ("member", "POST", f"{api}/books/{ids['book_id']}/bank-accounts", {"name": "X", "currency": "INR"}),
        ("billing", "POST", f"{api}/books/{ids['book_id']}/mandates", {"template": "pay_on_time"}),
        ("billing", "POST", f"{api}/books/{ids['book_id']}/mandates/draft", {"sentence": "pay everyone on time"}),
        ("billing", "PATCH", f"{api}/books/{ids['book_id']}/settings", {"horizon_days": 45}),
        ("billing", "PATCH", f"{api}/books/{ids['book_id']}/mandates/{ids['mandate_id']}", {"title": "Mine now"}),
        ("billing", "POST", f"{api}/sample", None),
    ]
    problems = []
    for person, method, url, body in attempts:
        response = client.request(method, url, headers=people[person], **({"json": body} if body is not None else {}))
        if response.status_code != 403 or "detail" not in response.text:
            problems.append(f"{person}: {method} {url} -> {response.status_code} {response.text[:200]}")
    assert problems == [], "\n".join(problems)


def test_a_member_sees_no_helm_figures_on_the_overview_card(client, world):
    ids = world["ids"]
    card = client.get(f"/api/v1/organizations/{ids['organization_id']}/helm/workspaces/{ids['workspace_id']}/card",
                      headers=world["people"]["member"])
    assert card.status_code in (200, 403), card.text
    if card.status_code == 200:
        body = card.json()
        assert not body.get("instruments") and not body.get("value_held"), body


def test_another_organizations_book_is_never_listed(client, db_session, world):
    other = world["people"]["other organization"]
    ids = world["ids"]
    listing = client.get(f"/api/v1/organizations/{ids['organization_id']}/helm", headers=other)
    assert listing.status_code in (403, 404)
    assert db_session.get(HelmBook, uuid.UUID(ids["book_id"])) is not None
