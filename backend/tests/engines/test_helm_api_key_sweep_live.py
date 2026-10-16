"""Every Helm route, for every kind of API key (the isolation sweep for keys), on real Postgres.

The route list is read from the application itself, so a new Helm route joins the sweep without
anyone remembering to add it. Real ids are filled in (the book, a mandate, an action, its
certificate, a bank account, a counterparty), so a refusal is the rule, not a missing row. Each
route is called by:

* a key from another organization (its issuer is a stranger here): refused, 403 or 404;
* a key from another organization whose issuer is ALSO an admin here: refused as a stranger is, 404
  (403 where no key may go at all);
* a key of this organization holding every scope except Helm's: refused, 403, naming a scope;
* a key holding helm:read and helm:write on Business: refused, 402 in words, or 403 where no key
  may go (a body Helm cannot read is refused as incomplete first, 422);
* the same key on Enterprise: never refused for its plan; the routes kept for people (approvals,
  mandates, settings) are refused in words, 403.

Nothing, for any key, is a server error, and no refused key ever gets a 2xx.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.scopes import PEOPLE_ONLY_ROUTES, ROUTE_SCOPE_MAP, ApiKeyScope
from app.models.helm import HelmAction
from app.models.organization import Organization, OrganizationMember, OrganizationRole
from app.services import api_key_service
from app.services.helm import vocabulary as v
from tests.conftest import Fixture, _make_user, _seat
from tests.engines.conftest import drain
from tests.security.plans import put_on_plan
from tests.services.helm import factories

API = "/api/v1"
PREFIX = "/organizations/{organization_id}/helm"
HELM_SCOPES = [ApiKeyScope.HELM_READ.value, ApiKeyScope.HELM_WRITE.value]


def _routes() -> list[tuple[str, str]]:
    from app.main import app

    found = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith(API + PREFIX):
            for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
                found.add((method, path[len(API):]))
    return sorted(found)


def _issue(db: Session, organization_id: uuid.UUID, user_id: uuid.UUID, scopes: list[str]) -> dict[str, str]:
    issuer = db.query(OrganizationMember).filter_by(organization_id=organization_id, user_id=user_id).one()
    _row, token = api_key_service.issue_api_key(db, organization_id=organization_id, actor=issuer,
                                                name=f"sweep {uuid.uuid4().hex[:6]}", scopes=scopes)
    db.commit()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def world(client: TestClient, db_session: Session, tenant: Fixture, test_sessions) -> dict:
    put_on_plan(db_session, tenant.organization, "enterprise")
    other = db_session.get(Organization, tenant.foreign_workspace.organization_id)
    put_on_plan(db_session, other, "enterprise")
    # A person in both organizations: an owner over there, an admin here.
    both = _make_user(db_session, f"both-{uuid.uuid4().hex[:6]}@beta.com")
    _seat(db_session, other, both, OrganizationRole.OWNER)
    _seat(db_session, tenant.organization, both, OrganizationRole.ADMIN)
    db_session.commit()

    org, ws = tenant.organization.id, tenant.workspace
    api = f"{API}/organizations/{org}/helm"
    owner = tenant.owner.headers
    book = client.post(f"{api}/books", json={"workspace_id": str(ws.id)}, headers=owner)
    assert book.status_code == 201, book.text
    book_id = book.json()["id"]
    item = factories.invoice(db_session, workspace=ws, organization_id=org, number="SW-1", supplier="Konkan Packaging",
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
    counterparties = client.get(f"{api}/books/{book_id}/counterparties", headers=owner).json()
    ids = {
        "organization_id": str(org), "book_id": book_id, "mandate_id": mandate.json()["id"],
        "action_id": str(action.id) if action else str(uuid.uuid4()),
        "certificate_id": str(action.certificate_id) if action and action.certificate_id else str(uuid.uuid4()),
        "account_id": account.json()["id"], "workspace_id": str(ws.id), "work_item_id": str(item.id),
        "counterparty_id": (counterparties[0]["id"] if isinstance(counterparties, list) and counterparties
                            else str(uuid.uuid4())),
    }
    others = [s.value for s in ApiKeyScope if not s.value.startswith("helm:")]
    keys = {
        "another organization's key": _issue(db_session, other.id, tenant.other_org_member.user.id, HELM_SCOPES),
        "another organization's key, issuer also here": _issue(db_session, other.id, both.user.id,
                                                               HELM_SCOPES + others),
        "a key without Helm's scopes": _issue(db_session, org, tenant.owner.user.id, others),
        "a Helm key": _issue(db_session, org, tenant.owner.user.id, HELM_SCOPES),
    }
    return {"ids": ids, "keys": keys, "organization": tenant.organization}


def _call(client: TestClient, method: str, path: str, ids: dict[str, str], headers: dict[str, str]):
    url = API + re.sub(r"\{(\w+)\}", lambda m: ids.get(m.group(1), str(uuid.uuid4())), path)
    kwargs: dict = {"headers": headers}
    if method in ("POST", "PATCH", "PUT"):
        if path.endswith("bank-statements") or path.endswith("bank-statements/preview"):
            kwargs["files"] = {"file": ("s.csv", b"Date,Narration,Amount\n01/10/2026,FEE,-15.00\n", "text/csv")}
        else:
            kwargs["json"] = {}
    return client.request(method, url, **kwargs)


def test_every_helm_route_refuses_the_keys_that_may_not_reach_it(client: TestClient, db_session: Session,
                                                                 world: dict) -> None:
    routes = _routes()
    assert len(routes) >= 40, routes
    ids, keys = world["ids"], world["keys"]
    problems: list[str] = []

    def sweep(who: str, allowed: tuple[int, ...]) -> None:
        for method, path in routes:
            response = _call(client, method, path, ids, keys[who])
            code = response.status_code
            if code >= 500 or code not in allowed:
                problems.append(f"{who}: {method} {path} -> {code} {response.text[:160]}")

    sweep("another organization's key", (403, 404))
    sweep("another organization's key, issuer also here", (403, 404))
    sweep("a key without Helm's scopes", (403,))

    put_on_plan(db_session, world["organization"], "business")
    db_session.commit()
    sweep("a Helm key", (402, 403, 422))
    assert problems == [], "\n".join(problems)


def test_on_business_a_helm_key_is_told_in_words_what_to_do(client: TestClient, db_session: Session,
                                                            world: dict) -> None:
    put_on_plan(db_session, world["organization"], "business")
    db_session.commit()
    ids, key = world["ids"], world["keys"]["a Helm key"]
    for method, path in _routes():
        if method != "GET":
            continue
        response = _call(client, method, path, ids, key)
        assert response.status_code == 402, (path, response.status_code, response.text[:200])
        body = response.json()
        assert body["code"] == "CAPABILITY_REQUIRED" and "Enterprise" in body["message"], (path, body)


def test_on_enterprise_a_helm_key_reaches_helm_except_where_people_decide(client: TestClient, world: dict) -> None:
    ids, key = world["ids"], world["keys"]["a Helm key"]
    problems: list[str] = []
    for method, path in _routes():
        response = _call(client, method, path, ids, key)
        code = response.status_code
        where = f"{method} {path} -> {code} {response.text[:200]}"
        if code >= 500 or code == 402:
            problems.append(where)
        elif (method, path) in PEOPLE_ONLY_ROUTES:
            if code != 403 or ("person" not in response.text and "signed in" not in response.text):
                problems.append(where)
        elif method == "GET" and (method, path) in ROUTE_SCOPE_MAP and code == 403:
            problems.append(where)
    assert problems == [], "\n".join(problems)
