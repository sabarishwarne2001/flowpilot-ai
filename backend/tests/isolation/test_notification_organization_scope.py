"""
ARCH-06 Step 5 — E8: organization-scoped notifications are invisible to
every workspace-scoped read.

§D2's own warning about ARCH-06's highest-risk item: "the moment
workspace_id becomes nullable, every existing workspace-scoped read must be
re-proven still scoped." Step 3 made workspace_id nullable; this is that
re-proof, landing at Step 5 alongside the CHECK constraint that makes the
shape this test exercises a normal, permanent one rather than a transient
in-between state.

WHY THIS TEST CONSTRUCTS THE ROW DIRECTLY RATHER THAN VIA THE API
-----------------------------------------------------------------------
No route exists yet that creates an organization-scoped notification —
`create_notification` (app/crud/notification.py) still requires workspace_id
and has no organization_id parameter at all; teaching it about
organization-level writes is unscheduled work, not this step's. The row this
test needs — workspace_id NULL, organization_id set — can only be produced
by constructing it directly against db_session, which is also the more
honest test: it proves the READ PATH's isolation independent of whichever
future write path eventually produces this shape, rather than coupling this
regression test's survival to a service function that does not exist yet.

WHY THE SAME ORGANIZATION AS BOTH WORKSPACES IS THE RIGHT SETUP, NOT A WEAKER ONE
--------------------------------------------------------------------------------------
`alpha_ws` and `beta_ws` (tests/isolation/conftest.py) both belong to the
SAME `org` fixture. Placing the organization-scoped notification in that
shared organization is deliberately the HARDEST version of this test: a
weaker setup (a notification scoped to some other, unrelated organization)
would pass even if the isolation logic were "hide rows from organizations
you don't belong to" rather than "hide rows with no workspace_id from every
workspace-scoped read" — the property E8 actually requires. Using the
shared organization means the only thing distinguishing this row from a
visible one is workspace_id itself, isolating the exact variable this test
exists to check.

WHY THIS PASSES WITHOUT ANY APPLICATION CODE CHANGE, VERIFIED NOT ASSUMED
--------------------------------------------------------------------------------
`list_notifications` (app/crud/notification.py) filters
`Notification.workspace_id == workspace_id`. SQL equality against NULL is
never true, for either operand, so a row with `workspace_id IS NULL` cannot
satisfy `= :workspace_id` for ANY value substituted in — not "happens not to
match today's data," structurally cannot match, for any workspace_id that
will ever be passed. This test is what turns that reasoning into 200/404
behavior actually observed over HTTP, per E8's own gate description ("no
workspace-scoped read returns a NULL-workspace row" — Isolation suite,
extended), rather than leaving it as an argument in a docstring.
"""

from __future__ import annotations

import pytest

from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationType,
)


def _make_organization_scoped_notification(db, *, organization, user, title):
    """
    Constructs the row shape no route can produce yet: workspace_id NULL,
    organization_id set. See the module docstring for why this bypasses
    create_notification rather than calling it.
    """
    note = Notification(
        title=title,
        message="Organization-level event, no single workspace.",
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.INFO,
        delivery_channel=NotificationChannel.IN_APP,
        workspace_id=None,
        organization_id=organization.id,
        user_id=user.id,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def test_organization_scoped_notification_invisible_to_every_workspace_read(
    client, db_session, token_for, multi_user, org, alpha_ws, beta_ws,
):
    """
    E8. The row belongs to the SAME organization as both workspaces
    (deliberately — see module docstring) and to the SAME user whose token
    is used for both requests, so workspace_id being NULL is the only
    variable this test isolates.
    """
    marker = "ORG-SCOPED-NOTE"
    _make_organization_scoped_notification(
        db_session, organization=org, user=multi_user, title=marker,
    )

    headers = {"Authorization": f"Bearer {token_for(multi_user)}"}

    alpha_response = client.get(
        f"/api/v1/workspaces/{alpha_ws.id}/notifications", headers=headers,
    )
    assert alpha_response.status_code == 200, alpha_response.text
    alpha_titles = {row["title"] for row in alpha_response.json()}
    assert marker not in alpha_titles, (
        "ISOLATION BREACH: an organization-scoped notification "
        "(workspace_id NULL) was returned by a workspace-scoped read."
    )

    beta_response = client.get(
        f"/api/v1/workspaces/{beta_ws.id}/notifications", headers=headers,
    )
    assert beta_response.status_code == 200, beta_response.text
    beta_titles = {row["title"] for row in beta_response.json()}
    assert marker not in beta_titles, (
        "ISOLATION BREACH: an organization-scoped notification "
        "(workspace_id NULL) was returned by a workspace-scoped read."
    )


def test_has_scope_constraint_permits_workspace_only_and_organization_only(
    db_session, org, alpha_ws, multi_user,
):
    """
    The positive control for the has_scope CHECK constraint added in this
    same step: both legal shapes must actually insert, not merely fail to
    violate the constraint in theory. A constraint whose own valid cases
    were never exercised is unverified in exactly the direction that
    matters most — it is trivial to write a CHECK that rejects everything,
    which would also make this table permanently unwritable.
    """
    workspace_only = Notification(
        title="WORKSPACE-ONLY",
        message="m",
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.INFO,
        delivery_channel=NotificationChannel.IN_APP,
        workspace_id=alpha_ws.id,
        organization_id=None,
        user_id=multi_user.id,
    )
    organization_only = Notification(
        title="ORG-ONLY",
        message="m",
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.INFO,
        delivery_channel=NotificationChannel.IN_APP,
        workspace_id=None,
        organization_id=org.id,
        user_id=multi_user.id,
    )
    db_session.add_all([workspace_only, organization_only])
    db_session.commit()

    db_session.refresh(workspace_only)
    db_session.refresh(organization_only)

    assert workspace_only.id is not None
    assert organization_only.id is not None


def test_has_scope_constraint_rejects_neither(db_session, multi_user):
    """
    The negative control. A row naming no scope at all must be rejected at
    the database layer — not merely avoided by disciplined application
    code, which is the exact distinction between a documented convention and
    an enforced invariant.
    """
    from sqlalchemy.exc import IntegrityError

    neither = Notification(
        title="SCOPELESS",
        message="m",
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.INFO,
        delivery_channel=NotificationChannel.IN_APP,
        workspace_id=None,
        organization_id=None,
        user_id=multi_user.id,
    )
    db_session.add(neither)

    with pytest.raises(IntegrityError, match="ck_notifications_has_scope"):
        db_session.commit()

    db_session.rollback()