"""F-055 — the BILLING role can read its own organization notifications.

The organization header's bell asks for the caller's organization-scoped
notifications on every organization page. The feed only ever returns the
caller's OWN notifications, but it admitted OWNER, ADMIN and MEMBER only, so a
BILLING member (the person dunning and payment alerts are for) got a 403 on
every page, and the sidebar's Notifications page failed for them.
"""

from __future__ import annotations

from sqlalchemy import update

from app.models.organization import OrganizationMember, OrganizationRole


def _make_billing(db_session, tenant) -> None:
    db_session.execute(
        update(OrganizationMember)
        .where(
            OrganizationMember.organization_id == tenant.organization.id,
            OrganizationMember.user_id == tenant.org_admin.user.id,
        )
        .values(role=OrganizationRole.BILLING)
    )
    db_session.commit()


def test_a_billing_member_reads_its_organization_notifications(client, db_session, tenant) -> None:
    _make_billing(db_session, tenant)
    response = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/notifications",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


def test_a_billing_member_cannot_mark_someone_elses_notification(client, db_session, tenant) -> None:
    import uuid

    _make_billing(db_session, tenant)
    response = client.patch(
        f"/api/v1/organizations/{tenant.organization.id}/notifications/{uuid.uuid4()}",
        json={"is_read": True},
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 404


def test_an_outsider_still_gets_nothing(client, tenant) -> None:
    response = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/notifications",
        headers=tenant.other_org_member.headers,
    )
    assert response.status_code in (403, 404)
