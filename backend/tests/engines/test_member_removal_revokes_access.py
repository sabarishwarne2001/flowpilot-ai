"""Removing a member ends every way they had in, at once (campaign session 1, E.3).

    pytest tests/engines/test_member_removal_revokes_access.py -q

An organization admin holds three kinds of access besides their session: an API key
they issued, a calendar feed (a public URL whose token is the credential), and their
own signed-in session. After the owner removes them, on the very next request: the
API key is refused, the feed answers 404, and their session reaches nothing in the
organization (the membership is resolved per request; nothing is cached).
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.organization import OrganizationMember
from tests.engines.conftest import Engines


def test_a_removed_admin_loses_the_api_key_the_feed_and_the_session(engines: Engines) -> None:
    admin = engines.tenant.org_admin
    issued = engines.client.post(
        f"/api/v1/organizations/{engines.org}/api-keys",
        json={"name": "admin's key", "scopes": ["workspaces:read", "work_items:read"]},
        headers=admin.headers,
    )
    assert issued.status_code == 201, issued.text
    key = issued.json()["token"]
    feed = engines.post("/calendar-feeds", {"label": "Mine", "scope": "ALL"}, as_user=admin)
    assert feed.status_code in (200, 201), feed.text
    feed_token = feed.json()["token"]

    # Every way in works before.
    assert engines.client.get(engines.url("/work-items"), headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert engines.client.get(f"/api/v1/public/calendar-feeds/{feed_token}.ics").status_code == 200
    assert engines.client.get(engines.url("/work-items"), headers=admin.headers).status_code == 200

    membership_id = engines.db.execute(
        select(OrganizationMember.id).where(
            OrganizationMember.organization_id == engines.org, OrganizationMember.user_id == admin.user.id
        )
    ).scalar_one()
    engines.db.commit()
    removed = engines.client.post(
        f"/api/v1/organizations/{engines.org}/members/{membership_id}/deactivate",
        headers=engines.tenant.owner.headers,
    )
    assert removed.status_code in (200, 204), removed.text

    # And none of them works after, on the very next request.
    assert engines.client.get(engines.url("/work-items"), headers={"Authorization": f"Bearer {key}"}).status_code == 401
    assert engines.client.get(f"/api/v1/public/calendar-feeds/{feed_token}.ics").status_code == 404
    assert engines.client.get(engines.url("/work-items"), headers=admin.headers).status_code == 404
    assert engines.client.get(
        f"/api/v1/organizations/{engines.org}/members", headers=admin.headers
    ).status_code in (403, 404)


def test_a_role_change_takes_effect_on_the_next_request(engines: Engines) -> None:
    """An admin demoted to member loses admin powers immediately: no cached role."""
    admin = engines.tenant.org_admin
    assert engines.client.get(f"/api/v1/organizations/{engines.org}/audit-logs", headers=admin.headers).status_code == 200
    membership_id = engines.db.execute(
        select(OrganizationMember.id).where(
            OrganizationMember.organization_id == engines.org, OrganizationMember.user_id == admin.user.id
        )
    ).scalar_one()
    engines.db.commit()
    changed = engines.client.patch(
        f"/api/v1/organizations/{engines.org}/members/{membership_id}",
        json={"role": "MEMBER"},
        headers=engines.tenant.owner.headers,
    )
    assert changed.status_code == 200, changed.text
    assert engines.client.get(
        f"/api/v1/organizations/{engines.org}/audit-logs", headers=admin.headers
    ).status_code in (403, 404)
