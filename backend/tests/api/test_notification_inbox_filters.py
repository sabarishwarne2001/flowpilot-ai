"""N-020 item 2 — the notification inbox filters by category and can mark a notification unread."""

from __future__ import annotations

from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationType,
)


def _note(db, tenant, *, title, kind, is_read=False, organization_only=False):
    note = Notification(
        title=title,
        message=f"{title} message",
        notification_type=kind,
        priority=NotificationPriority.INFO,
        delivery_channel=NotificationChannel.IN_APP,
        workspace_id=None if organization_only else tenant.workspace.id,
        organization_id=tenant.organization.id,
        user_id=tenant.contributor.user.id,
        is_read=is_read,
    )
    db.add(note)
    db.commit()
    return note


def _titles(response) -> set[str]:
    assert response.status_code == 200, response.text
    body = response.json()
    rows = body["items"] if isinstance(body, dict) else body
    return {row["title"] for row in rows}


def test_workspace_inbox_filters_by_category_and_read_state(client, db_session, tenant):
    _note(db_session, tenant, title="Doc done", kind=NotificationType.DOCUMENT)
    _note(db_session, tenant, title="Rule ran", kind=NotificationType.AUTOMATION, is_read=True)
    _note(db_session, tenant, title="New sign-in", kind=NotificationType.SECURITY)
    url = f"/api/v1/workspaces/{tenant.workspace.id}/notifications"
    headers = tenant.contributor.headers

    assert _titles(client.get(url, headers=headers)) == {"Doc done", "Rule ran", "New sign-in"}
    assert _titles(client.get(url, params={"notification_type": "DOCUMENT"}, headers=headers)) == {"Doc done"}
    assert _titles(
        client.get(url, params={"notification_type": "AUTOMATION", "is_read": "false"}, headers=headers)
    ) == set()
    assert client.get(url, params={"notification_type": "NOPE"}, headers=headers).status_code == 422


def test_a_read_notification_can_be_marked_unread_again(client, db_session, tenant):
    note = _note(db_session, tenant, title="Rule ran", kind=NotificationType.AUTOMATION, is_read=True)
    url = f"/api/v1/workspaces/{tenant.workspace.id}/notifications"

    response = client.patch(f"{url}/{note.id}", json={"is_read": False}, headers=tenant.contributor.headers)

    assert response.status_code == 200, response.text
    assert response.json()["is_read"] is False
    assert _titles(client.get(url, params={"is_read": "false"}, headers=tenant.contributor.headers)) == {"Rule ran"}


def test_organization_feed_filters_by_category(client, db_session, tenant):
    _note(db_session, tenant, title="Seat limit", kind=NotificationType.SYSTEM, organization_only=True)
    _note(db_session, tenant, title="Owner change", kind=NotificationType.SECURITY, organization_only=True)
    url = f"/api/v1/organizations/{tenant.organization.id}/notifications"

    filtered = client.get(url, params={"notification_type": "SECURITY"}, headers=tenant.contributor.headers)

    assert _titles(filtered) == {"Owner change"}
    assert filtered.json()["total"] == 1
