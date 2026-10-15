"""A change re-plans a book through a `helm.plan` job debounced per book and minute (real Postgres).

Once that minute's job has run, a later change in the same minute must get a run of its own: folded
into the finished job it would wait for the hourly sweep, and a payment would keep asking for the
approvals of an approval chain that was just removed.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.helm import HelmPlan
from app.services.helm import vocabulary as v
from tests.engines.conftest import drain
from tests.security.plans import put_on_plan
from tests.services.helm import factories

API = "/api/v1"


def _url(tenant, path: str) -> str:
    return f"{API}/organizations/{tenant.organization.id}/helm{path}"


@pytest.fixture()
def book(client, db_session, tenant, test_sessions):
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    response = client.post(_url(tenant, "/books"), json={"workspace_id": str(tenant.workspace.id)},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    book = response.json()
    response = client.patch(_url(tenant, f"/books/{book['id']}/settings"),
                            json={"identities": [{"name": "Meridian Wholesale", "tax_id": "27AAACM1234F1Z5"}]},
                            headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    response = client.post(_url(tenant, f"/books/{book['id']}/cash-positions"),
                           json={"currency": "INR", "amount": "5000000", "as_of": date.today().isoformat()},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    return book


def test_a_second_change_in_the_same_minute_is_planned_too(client, db_session, tenant, book):
    factories.invoice(db_session, workspace=tenant.workspace, organization_id=tenant.organization.id,
                      number="DEB-1", supplier="Supplier DEB-1", amount=500_000,
                      on=date.today() - timedelta(days=20), terms="Net 30")
    db_session.commit()
    response = client.post(_url(tenant, f"/books/{book['id']}/mandates"),
                           json={"template": "pay_on_time", "activate": True}, headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    settings = _url(tenant, f"/books/{book['id']}/settings")

    def versions() -> int:
        db_session.expire_all()
        return len(db_session.execute(select(HelmPlan.id).where(HelmPlan.book_id == uuid.UUID(book["id"]))).all())

    assert client.patch(settings, json={"run_weekdays": [2]}, headers=tenant.owner.headers).status_code == 200
    drain(only=[v.JOB_PLAN])
    after_first = versions()
    assert client.patch(settings, json={"run_weekdays": [4]}, headers=tenant.owner.headers).status_code == 200
    drain(only=[v.JOB_PLAN])
    assert versions() == after_first + 1
