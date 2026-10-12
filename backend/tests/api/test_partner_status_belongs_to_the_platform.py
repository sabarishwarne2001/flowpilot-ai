"""A partner's standing is the platform's to set, and a suspended partner stops acting (session 3).

`partners.status` (ACTIVE, SUSPENDED, TERMINATED) was read by nothing, and the one route that set
it was `PATCH /partners/{id}` for the partner's OWN owner: the platform had no way to suspend a
reseller, and a reseller could mark itself whatever it liked (and edit the platform's notes about
it). Now the status and the notes are set by platform staff only; a partner that is not ACTIVE is
read-only (it can still read its book, members and statements) and its marketplace items cannot be
newly installed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import security
from tests.api.test_arch27_endpoints import PARTNERS, _publish, keypair, partner  # noqa: F401 (fixtures)
from tests.security.plans import put_on_plan


def _platform_operator(db_session: Session, tenant) -> dict[str, str]:
    user = tenant.org_admin.user
    user.is_superuser = True
    db_session.commit()
    return {"Authorization": f"Bearer {security.create_access_token(subject=str(user.id))}"}


def test_a_partner_cannot_set_its_own_standing(client: TestClient, tenant, partner) -> None:  # noqa: F811
    url = f"{PARTNERS}/{partner.id}"
    for body in ({"status": "SUSPENDED"}, {"status": "ACTIVE"}, {"notes": "approved by the platform"}):
        response = client.patch(url, headers=tenant.owner.headers, json=body)
        assert response.status_code in (403, 422), (body, response.status_code, response.text)
    renamed = client.patch(url, headers=tenant.owner.headers, json={"name": "Reseller Limited"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Reseller Limited"


def test_the_platform_suspends_a_partner_and_it_becomes_read_only(
    client: TestClient, db_session: Session, tenant, partner, keypair  # noqa: F811
) -> None:
    operator = _platform_operator(db_session, tenant)
    suspended = client.patch(f"{PARTNERS}/{partner.id}", headers=operator, json={"status": "SUSPENDED"})
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["status"] == "SUSPENDED"

    owner = tenant.owner.headers
    # Reading still works: the book, the members, the statements.
    assert client.get(f"{PARTNERS}/{partner.id}", headers=owner).status_code == 200
    assert client.get(f"{PARTNERS}/{partner.id}/book", headers=owner).status_code == 200
    assert client.get(f"{PARTNERS}/{partner.id}/payouts", headers=owner).status_code == 200
    # Acting does not.
    for method, path, body in (
        ("POST", "/book", {"organization_id": str(tenant.organization.id)}),
        ("POST", "/catalog", {"slug": "late-item", "name": "Late item", "visibility": "PUBLIC"}),
        ("POST", "/signing-keys", {"key_id": "late", "algorithm": "ED25519", "public_key_pem": keypair[1]}),
        ("POST", "/payouts", {"period_start": "2026-09-01", "period_end": "2026-09-30"}),
    ):
        response = client.request(method, f"{PARTNERS}/{partner.id}{path}", headers=owner, json=body)
        assert response.status_code == 409, (path, response.status_code, response.text)
        assert "suspended" in response.text.lower()


def test_a_suspended_partners_items_cannot_be_newly_installed(
    client: TestClient, db_session: Session, tenant, partner, keypair  # noqa: F811
) -> None:
    put_on_plan(db_session, tenant.organization, "business")
    _item, published, _nodes, _edges = _publish(client, tenant, partner, keypair)
    assert published.status_code == 201, published.text
    operator = _platform_operator(db_session, tenant)
    assert client.patch(f"{PARTNERS}/{partner.id}", headers=operator, json={"status": "SUSPENDED"}).status_code == 200

    installed = client.post(
        f"/api/v1/organizations/{tenant.organization.id}/marketplace/installations",
        headers=tenant.owner.headers,
        json={"manifest_id": published.json()["id"], "workspace_id": str(tenant.workspace.id)},
    )
    assert installed.status_code in (404, 409), installed.text
