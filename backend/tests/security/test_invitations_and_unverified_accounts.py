"""F-032 — pending invitations are not shown to an account that has not proved its mailbox.

    pytest tests/security/test_invitations_and_unverified_accounts.py -q

`POST /auth/register` never says whether an address is already taken, so ANYONE
can create an account for `victim@corp.com`, unverified, before the victim does
(pre-hijacking). `GET /me/invitations` listed the pending invitations sent to
the account's email address, with the organization name, the role, the
inviter's email and the workspace names, without checking that the address had
been verified. The attacker never needed the invitation token or the victim's
mailbox to read all of it.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import Fixture, _make_user


def _invite(client: TestClient, tenant: Fixture, email: str) -> None:
    response = client.post(
        f"/api/v1/organizations/{tenant.organization.id}/invitations",
        headers=tenant.owner.headers,
        json={"email": email, "organization_role": "ADMIN", "grants": []},
    )
    assert response.status_code in (200, 201, 202), response.text[:300]


def _account(db: Session, email: str, *, verified: bool):
    persona = _make_user(db, email)
    if not verified:
        persona.user.email_verified_at = None
    db.commit()
    return persona


def test_an_unverified_account_sees_no_pending_invitations(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    victim = f"victim-{uuid.uuid4().hex[:8]}@corp.example"
    _invite(client, tenant, victim)
    squatter = _account(db_session, victim, verified=False)

    response = client.get("/api/v1/me/invitations", headers=squatter.headers)

    assert response.status_code in (200, 403)
    if response.status_code == 200:
        assert response.json()["items"] == []
    assert tenant.organization.name not in response.text
    assert tenant.owner.user.email not in response.text


def test_the_rightful_verified_owner_of_the_address_still_sees_it(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    """Control: the listing works once the mailbox has been proved."""
    victim = f"victim-{uuid.uuid4().hex[:8]}@corp.example"
    _invite(client, tenant, victim)
    owner_of_address = _account(db_session, victim, verified=True)

    response = client.get("/api/v1/me/invitations", headers=owner_of_address.headers)

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["organization_name"] for item in items] == [tenant.organization.name]
