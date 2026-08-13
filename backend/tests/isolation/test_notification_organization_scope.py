"""
ARCH-07 Step 10 — E19, E20, R9. Org notification read API isolation suite.
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
    assert marker not in alpha_titles

    beta_response = client.get(
        f"/api/v1/workspaces/{beta_ws.id}/notifications", headers=headers,
    )
    assert beta_response.status_code == 200, beta_response.text
    beta_titles = {row["title"] for row in beta_response.json()}
    assert marker not in beta_titles


def test_has_scope_constraint_permits_workspace_only_and_organization_only(
    db_session, org, alpha_ws, multi_user,
):
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


def test_member_reads_their_own_org_notifications(
    client, db_session, token_for, multi_user, org
):
    marker = "PERSONAL-ORG-NOTE"
    _make_organization_scoped_notification(
        db_session, organization=org, user=multi_user, title=marker
    )

    headers = {"Authorization": f"Bearer {token_for(multi_user)}"}
    response = client.get(
        f"/api/v1/organizations/{org.id}/notifications",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["items"]
    assert any(item["title"] == marker for item in payload["items"])
    assert all(item["workspace_id"] is None for item in payload["items"])