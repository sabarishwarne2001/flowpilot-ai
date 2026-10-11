"""A seat refusal says why, machine-readably (campaign session 1).

    pytest tests/api/test_seat_limit_refusal_api.py -q

See tests/services/test_seat_capacity_every_path.py for the model. The API answers
409 SEAT_LIMIT_EXCEEDED with `details`: the reason, the plan, the capacity, the seats
used and the remedy (buy seats on a paid plan, upgrade on Free).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core import security
from tests.services.test_seat_capacity_every_path import _organization


def test_a_full_free_organization_refuses_an_invitation_through_the_api(client, db_session: Session) -> None:
    org, owner = _organization(db_session, "free", members=2)
    token = security.create_access_token(subject=str(owner.id))

    response = client.post(
        f"/api/v1/organizations/{org.id}/invitations",
        json={"email": "third@seats.example", "organization_role": "MEMBER"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "SEAT_LIMIT_EXCEEDED"
    assert body["details"]["reason"] == "SEAT_LIMIT_REACHED"
    assert body["details"]["seat_capacity"] == 2


def test_scim_provisioning_into_a_full_organization_is_a_scim_error(client, db_session: Session) -> None:
    """SCIM gets a SCIM error body naming the reason, not a crash or a foreign shape."""
    from datetime import datetime, timedelta, timezone

    from app.models.identity import EnterpriseIdpConfig, JitProvisioningMode, VerifiedDomain
    from app.services.identity import scim_service

    org, _ = _organization(db_session, "enterprise", members=2, seats_purchased=2)
    now = datetime.now(timezone.utc)
    domain = VerifiedDomain(
        organization_id=org.id, domain=f"full-{org.slug}.test", status="VERIFIED", challenge_token="t",
        challenge_issued_at=now, challenge_expires_at=now + timedelta(days=30), first_verified_at=now,
        is_sso_binding=True,
    )
    db_session.add(domain)
    db_session.flush()
    config = EnterpriseIdpConfig(
        organization_id=org.id, verified_domain_id=domain.id, protocol="SAML2", display_name="SAML",
        is_active=True, idp_entity_id=f"https://idp.{domain.domain}/m", idp_sso_url=f"https://idp.{domain.domain}/s",
        jit_provisioning_mode=JitProvisioningMode.OPEN,
    )
    db_session.add(config)
    db_session.flush()
    _, token = scim_service.issue_key(db_session, organization_id=org.id, idp_config_id=config.id, display_name="SCIM")
    db_session.commit()

    response = client.post(
        "/scim/v2/Users",
        json={"userName": f"new@{domain.domain}", "emails": [{"value": f"new@{domain.domain}", "primary": True}]},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/scim+json"},
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert "no seat available" in body["detail"]
