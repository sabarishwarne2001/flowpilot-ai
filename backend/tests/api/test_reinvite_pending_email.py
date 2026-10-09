"""F-209 — inviting an address that already had a pending invitation was a 500.

    pytest tests/api/test_reinvite_pending_email.py -q

Found by the live API sweep. `create_invitation` supersedes an earlier pending
invitation to the same address, but it called
`invitation_crud.update_invitation_status`, which does not exist, so the second
"Invite" for the same person (to change the role, or because the first email
never arrived) crashed with AttributeError. The new invitation must replace the
old one: the old one is REVOKED, its link stops working, and exactly one stays
pending.
"""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization_invitation import InvitationStatus, OrganizationInvitation
from tests.api.test_organization_invitations import (  # noqa: F401  (fixtures)
    org_admin_context,
    test_org,
)


def _invite(client, context, role: str):
    with patch("app.api.v1.organization_invitations.invitation_mail.send_invitation"):
        return client.post(
            f"/api/v1/organizations/{context['organization_id']}/invitations",
            json={"email": "Repeat@Example.com", "organization_role": role, "grants": []},
            headers=context["auth_headers"],
        )


def test_a_second_invitation_to_the_same_address_supersedes_the_first(
    client, db_session: Session, org_admin_context
) -> None:
    first = _invite(client, org_admin_context, "MEMBER")
    assert first.status_code == 201, first.text

    second = _invite(client, org_admin_context, "BILLING")
    assert second.status_code == 201, second.text

    db_session.expire_all()
    rows = db_session.execute(
        select(OrganizationInvitation)
        .where(OrganizationInvitation.organization_id == org_admin_context["organization_id"])
        .where(OrganizationInvitation.email == "repeat@example.com")
        .order_by(OrganizationInvitation.created_at)
    ).scalars().all()
    assert [r.status for r in rows] == [InvitationStatus.REVOKED, InvitationStatus.PENDING]
    assert rows[0].revoked_at is not None
    assert str(rows[-1].organization_role.value if hasattr(rows[-1].organization_role, "value") else rows[-1].organization_role) == "BILLING"
