"""ARCH-0G §4.5 — the three carried-forward gaps.

Tests for:
1. GET /organizations/{id}/usage-limits
2. GET /me/email-change/request
3. PATCH /organizations/{id}/notifications/{id}
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import security
from app.main import app
from app.models.email_change_request import EmailChangeRequest, EmailChangeStatus
from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
)
from app.models.organization import Organization, OrganizationMember, OrganizationRole
from app.models.spend_limit import SpendLimitPeriod
from app.models.user import User
from app.models.workspace import Workspace
from app.services import email_change_service as ecs
from app.services import spend_control_service


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def db(db_session: Session) -> Session:
    return db_session


def _create_user(db: Session, email: str | None = None) -> User:
    user = User(
        email=email or f"user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=security.get_password_hash("Password123!"),
        is_active=True,
    )
    if hasattr(User, "email_verified_at"):
        user.email_verified_at = datetime.now(timezone.utc)
    db.add(user)
    db.flush([user])
    return user


def _auth_header(user: User) -> dict[str, str]:
    token = security.create_access_token(subject=user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def org_setup(db: Session) -> dict:
    org = Organization(name="ARCH0G Org", slug=f"arch0g-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush([org])

    workspace = Workspace(
        organization_id=org.id,
        workspace_name="ARCH0G Workspace",
        slug=f"ws-{uuid.uuid4().hex[:8]}",
    )
    db.add(workspace)
    db.flush([workspace])

    admin = _create_user(db)
    member = _create_user(db)

    admin_membership = OrganizationMember(
        organization_id=org.id,
        user_id=admin.id,
        role=OrganizationRole.ADMIN,
        status="ACTIVE",
    )
    member_membership = OrganizationMember(
        organization_id=org.id,
        user_id=member.id,
        role=OrganizationRole.MEMBER,
        status="ACTIVE",
    )
    db.add(admin_membership)
    db.add(member_membership)

    foreign_org = Organization(name="Foreign Org", slug=f"foreign-{uuid.uuid4().hex[:8]}")
    db.add(foreign_org)
    db.flush([foreign_org])

    db.commit()

    return {
        "org": org,
        "workspace": workspace,
        "admin": admin,
        "member": member,
        "foreign_org": foreign_org,
        "admin_headers": _auth_header(admin),
        "member_headers": _auth_header(member),
    }


# =============================================================================
# 1. GET /organizations/{id}/usage-limits
# =============================================================================


def test_usage_limits_lists_only_this_organizations_rows(
    client: TestClient, db: Session, org_setup: dict
):
    org = org_setup["org"]
    foreign_org = org_setup["foreign_org"]

    spend_control_service.set_limit(
        db,
        organization_id=org.id,
        limit_key="*",
        period=SpendLimitPeriod.MONTH,
        max_cost_micros=5_000_000,
        note="monthly total",
    )
    spend_control_service.set_limit(
        db,
        organization_id=org.id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.DAY,
        max_quantity=Decimal("500"),
    )
    spend_control_service.set_limit(
        db,
        organization_id=foreign_org.id,
        limit_key="*",
        period=SpendLimitPeriod.MONTH,
        max_cost_micros=99_999_999,
        note="other tenant",
    )
    db.commit()

    response = client.get(
        f"/api/v1/organizations/{org.id}/usage-limits",
        headers=org_setup["admin_headers"],
    )
    assert response.status_code == 200

    body = response.json()
    assert {row["limit_key"] for row in body} == {"*", "ocr.page"}
    assert all(row["organization_id"] == str(org.id) for row in body)
    assert "99999999" not in response.text
    assert "other tenant" not in response.text


def test_usage_limits_round_trips_what_the_put_wrote(
    client: TestClient, org_setup: dict
):
    org = org_setup["org"]
    written = client.put(
        f"/api/v1/organizations/{org.id}/usage-limits",
        headers=org_setup["admin_headers"],
        json={
            "limit_key": "llm.output_token",
            "period": "MONTH",
            "max_quantity": "1000000",
            "hard_stop": True,
        },
    )
    assert written.status_code == 200

    listed = client.get(
        f"/api/v1/organizations/{org.id}/usage-limits",
        headers=org_setup["admin_headers"],
    )
    assert listed.status_code == 200
    assert written.json()["id"] in {row["id"] for row in listed.json()}


def test_superseded_limits_are_hidden_unless_asked_for(
    client: TestClient, org_setup: dict
):
    org = org_setup["org"]
    url = f"/api/v1/organizations/{org.id}/usage-limits"
    payload = {
        "limit_key": "ocr.page",
        "period": "MONTH",
        "max_quantity": "100",
        "hard_stop": True,
    }
    first = client.put(url, headers=org_setup["admin_headers"], json=payload)
    second = client.put(
        url,
        headers=org_setup["admin_headers"],
        json={**payload, "max_quantity": "200"},
    )
    assert first.json()["id"] != second.json()["id"]

    active = client.get(url, headers=org_setup["admin_headers"]).json()
    assert {row["id"] for row in active} == {second.json()["id"]}

    everything = client.get(
        url,
        params={"include_inactive": True},
        headers=org_setup["admin_headers"],
    ).json()
    assert {first.json()["id"], second.json()["id"]} <= {
        row["id"] for row in everything
    }


def test_usage_limits_cross_tenant_read_is_404(
    client: TestClient, org_setup: dict
):
    foreign_org = org_setup["foreign_org"]
    response = client.get(
        f"/api/v1/organizations/{foreign_org.id}/usage-limits",
        headers=org_setup["admin_headers"],
    )
    assert response.status_code == 404


def test_usage_limits_requires_org_admin(
    client: TestClient, org_setup: dict
):
    org = org_setup["org"]
    response = client.get(
        f"/api/v1/organizations/{org.id}/usage-limits",
        headers=org_setup["member_headers"],
    )
    assert response.status_code in (403, 404)


# =============================================================================
# 2. GET /me/email-change/request
# =============================================================================


def _pending_email_change(db: Session, *, user: User, new_email: str, expires_in: timedelta):
    req = EmailChangeRequest(
        user_id=user.id,
        new_email=new_email,
        token_hash=ecs.hash_token(uuid.uuid4().hex),
        status=EmailChangeStatus.PENDING,
        expires_at=datetime.now(timezone.utc) + expires_in,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


def test_pending_email_change_is_readable_after_reload(
    client: TestClient, db: Session, org_setup: dict
):
    admin = org_setup["admin"]
    req = _pending_email_change(
        db,
        user=admin,
        new_email="new@acme.com",
        expires_in=timedelta(hours=1),
    )

    response = client.get(
        "/api/v1/me/email-change/request",
        headers=org_setup["admin_headers"],
    )
    assert response.status_code == 200

    body = response.json()
    assert body["id"] == str(req.id)
    assert body["new_email"] == "new@acme.com"
    assert "expires_at" in body and "requested_at" in body


def test_pending_email_change_never_exposes_the_token(
    client: TestClient, db: Session, org_setup: dict
):
    admin = org_setup["admin"]
    req = _pending_email_change(
        db,
        user=admin,
        new_email="new@acme.com",
        expires_in=timedelta(hours=1),
    )

    response = client.get(
        "/api/v1/me/email-change/request",
        headers=org_setup["admin_headers"],
    )
    assert req.token_hash not in response.text
    assert set(response.json()) == {"id", "new_email", "requested_at", "expires_at"}


def test_expired_request_reads_as_absent(
    client: TestClient, db: Session, org_setup: dict
):
    admin = org_setup["admin"]
    _pending_email_change(
        db,
        user=admin,
        new_email="stale@acme.com",
        expires_in=timedelta(hours=-1),
    )

    response = client.get(
        "/api/v1/me/email-change/request",
        headers=org_setup["admin_headers"],
    )
    assert response.status_code == 404


def test_no_pending_request_is_404(client: TestClient, org_setup: dict):
    response = client.get(
        "/api/v1/me/email-change/request",
        headers=org_setup["member_headers"],
    )
    assert response.status_code == 404


def test_get_is_a_pure_read(
    client: TestClient, db: Session, org_setup: dict
):
    admin = org_setup["admin"]
    req = _pending_email_change(
        db,
        user=admin,
        new_email="new@acme.com",
        expires_in=timedelta(hours=1),
    )
    for _ in range(2):
        assert (
            client.get(
                "/api/v1/me/email-change/request",
                headers=org_setup["admin_headers"],
            ).status_code
            == 200
        )

    db.refresh(req)
    assert req.status is EmailChangeStatus.PENDING
    assert req.consumed_at is None


def test_cancel_then_get_agree(
    client: TestClient, db: Session, org_setup: dict
):
    admin = org_setup["admin"]
    _pending_email_change(
        db,
        user=admin,
        new_email="new@acme.com",
        expires_in=timedelta(hours=1),
    )
    assert (
        client.delete(
            "/api/v1/me/email-change/request",
            headers=org_setup["admin_headers"],
        ).status_code
        == 204
    )
    assert (
        client.get(
            "/api/v1/me/email-change/request",
            headers=org_setup["admin_headers"],
        ).status_code
        == 404
    )


# =============================================================================
# 3. PATCH /organizations/{id}/notifications/{id}
# =============================================================================


def _org_notification(db: Session, *, organization_id, user_id, workspace_id=None):
    notification = Notification(
        organization_id=organization_id,
        workspace_id=workspace_id,
        user_id=user_id,
        title="Ownership transfer proposed",
        message="Review it before it expires.",
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.WARNING,
        delivery_channel=NotificationChannel.IN_APP,
        delivery_status=NotificationStatus.SENT,
        retry_count=0,
        is_read=False,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def test_org_notification_can_be_marked_read(
    client: TestClient, db: Session, org_setup: dict
):
    org = org_setup["org"]
    member = org_setup["member"]
    notification = _org_notification(
        db,
        organization_id=org.id,
        user_id=member.id,
    )

    response = client.patch(
        f"/api/v1/organizations/{org.id}/notifications/{notification.id}",
        headers=org_setup["member_headers"],
        json={"is_read": True},
    )
    assert response.status_code == 200
    assert response.json()["is_read"] is True

    db.refresh(notification)
    assert notification.is_read is True


def test_unread_count_actually_falls(
    client: TestClient, db: Session, org_setup: dict
):
    org = org_setup["org"]
    member = org_setup["member"]
    notification = _org_notification(
        db,
        organization_id=org.id,
        user_id=member.id,
    )
    listed = f"/api/v1/organizations/{org.id}/notifications"

    before = client.get(listed, headers=org_setup["member_headers"]).json()["unread_count"]
    assert before == 1

    client.patch(
        f"{listed}/{notification.id}",
        headers=org_setup["member_headers"],
        json={"is_read": True},
    )

    after = client.get(listed, headers=org_setup["member_headers"]).json()["unread_count"]
    assert after == 0


def test_marking_unread_again_is_supported(
    client: TestClient, db: Session, org_setup: dict
):
    org = org_setup["org"]
    member = org_setup["member"]
    notification = _org_notification(
        db,
        organization_id=org.id,
        user_id=member.id,
    )
    url = f"/api/v1/organizations/{org.id}/notifications/{notification.id}"
    client.patch(url, headers=org_setup["member_headers"], json={"is_read": True})
    response = client.patch(
        url, headers=org_setup["member_headers"], json={"is_read": False}
    )
    assert response.status_code == 200
    assert response.json()["is_read"] is False


def test_cannot_mark_another_users_notification_read(
    client: TestClient, db: Session, org_setup: dict
):
    org = org_setup["org"]
    member = org_setup["member"]
    notification = _org_notification(
        db,
        organization_id=org.id,
        user_id=member.id,
    )

    response = client.patch(
        f"/api/v1/organizations/{org.id}/notifications/{notification.id}",
        headers=org_setup["admin_headers"],
        json={"is_read": True},
    )
    assert response.status_code == 404

    db.refresh(notification)
    assert notification.is_read is False


def test_cannot_mark_another_tenants_notification_read(
    client: TestClient, db: Session, org_setup: dict
):
    foreign_org = org_setup["foreign_org"]
    other_user = _create_user(db)
    foreign_notification = _org_notification(
        db,
        organization_id=foreign_org.id,
        user_id=other_user.id,
    )

    org = org_setup["org"]
    response = client.patch(
        f"/api/v1/organizations/{org.id}/notifications/{foreign_notification.id}",
        headers=org_setup["admin_headers"],
        json={"is_read": True},
    )
    assert response.status_code == 404

    db.refresh(foreign_notification)
    assert foreign_notification.is_read is False


def test_workspace_scoped_row_is_not_reachable_through_the_org_path(
    client: TestClient, db: Session, org_setup: dict
):
    org = org_setup["org"]
    member = org_setup["member"]
    workspace = org_setup["workspace"]
    scoped = _org_notification(
        db,
        organization_id=org.id,
        user_id=member.id,
        workspace_id=workspace.id,
    )

    response = client.patch(
        f"/api/v1/organizations/{org.id}/notifications/{scoped.id}",
        headers=org_setup["member_headers"],
        json={"is_read": True},
    )
    assert response.status_code == 404

    db.refresh(scoped)
    assert scoped.is_read is False


def test_delivery_state_is_not_writable_by_a_user(
    client: TestClient, db: Session, org_setup: dict
):
    org = org_setup["org"]
    member = org_setup["member"]
    notification = _org_notification(
        db,
        organization_id=org.id,
        user_id=member.id,
    )

    client.patch(
        f"/api/v1/organizations/{org.id}/notifications/{notification.id}",
        headers=org_setup["member_headers"],
        json={"is_read": True, "delivery_status": "FAILED", "retry_count": 99},
    )

    db.refresh(notification)
    assert notification.delivery_status is NotificationStatus.SENT
    assert notification.retry_count == 0


def test_missing_is_read_is_rejected(
    client: TestClient, db: Session, org_setup: dict
):
    org = org_setup["org"]
    member = org_setup["member"]
    notification = _org_notification(
        db,
        organization_id=org.id,
        user_id=member.id,
    )
    response = client.patch(
        f"/api/v1/organizations/{org.id}/notifications/{notification.id}",
        headers=org_setup["member_headers"],
        json={},
    )
    assert response.status_code == 422