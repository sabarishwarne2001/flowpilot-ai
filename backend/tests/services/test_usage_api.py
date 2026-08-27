"""Unit and integration tests for tenant usage limit API endpoints (ARCH-14)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import security
from app.main import app
from app.models.organization import Organization, OrganizationMember, OrganizationRole
from app.models.spend_limit import SpendLimitPeriod
from app.models.user import User


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def db(db_session: Session) -> Session:
    return db_session


@pytest.fixture()
def org(db: Session) -> Organization:
    organization = Organization(
        name="Usage Test Org",
        slug=f"usage-org-{uuid.uuid4().hex[:8]}",
    )
    db.add(organization)
    db.flush([organization])
    return organization


def _create_user_with_org_role(
    db: Session, organization: Organization, role: OrganizationRole
) -> tuple[User, dict[str, str]]:
    user = User(
        email=f"user-{role.value.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=security.get_password_hash("ValidPass123!"),
        is_active=True,
    )
    if hasattr(User, "email_verified_at"):
        user.email_verified_at = datetime.now(timezone.utc)

    db.add(user)
    db.flush([user])

    membership = OrganizationMember(
        organization_id=organization.id,
        user_id=user.id,
        role=role,
        status="ACTIVE",
    )
    db.add(membership)
    db.commit()

    token = security.create_access_token(subject=user.id)
    headers = {"Authorization": f"Bearer {token}"}
    return user, headers


@pytest.fixture()
def admin_user(db: Session, org: Organization) -> tuple[User, dict[str, str]]:
    return _create_user_with_org_role(db, org, OrganizationRole.ADMIN)


@pytest.fixture()
def member_user(db: Session, org: Organization) -> tuple[User, dict[str, str]]:
    return _create_user_with_org_role(db, org, OrganizationRole.MEMBER)


# ============================================================================
# 1. Organization Admin can create and update a limit
# ============================================================================


def test_org_admin_can_create_limit(
    client: TestClient, org: Organization, admin_user: tuple[User, dict[str, str]]
):
    _, headers = admin_user
    payload = {
        "limit_key": "ocr.page",
        "period": "MONTH",
        "max_quantity": "500",
        "max_cost_micros": 5_000_000,
        "hard_stop": True,
        "note": "Monthly OCR hard limit ceiling",
    }

    response = client.put(
        f"/api/v1/organizations/{org.id}/usage-limits",
        json=payload,
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["organization_id"] == str(org.id)
    assert data["limit_key"] == "ocr.page"
    assert data["period"] == "MONTH"
    assert Decimal(data["max_quantity"]) == Decimal("500")
    assert data["max_cost_micros"] == 5_000_000
    assert data["hard_stop"] is True
    assert data["is_active"] is True
    assert data["note"] == "Monthly OCR hard limit ceiling"


def test_org_admin_can_update_existing_limit(
    client: TestClient, org: Organization, admin_user: tuple[User, dict[str, str]]
):
    _, headers = admin_user

    # First creation
    client.put(
        f"/api/v1/organizations/{org.id}/usage-limits",
        json={"limit_key": "ocr.page", "period": "MONTH", "max_quantity": "100"},
        headers=headers,
    )

    # Subsequent update (supersedes previous limit)
    update_response = client.put(
        f"/api/v1/organizations/{org.id}/usage-limits",
        json={
            "limit_key": "ocr.page",
            "period": "MONTH",
            "max_quantity": "250",
            "max_cost_micros": 2_500_000,
            "hard_stop": False,
        },
        headers=headers,
    )

    assert update_response.status_code == 200
    updated_data = update_response.json()
    assert Decimal(updated_data["max_quantity"]) == Decimal("250")
    assert updated_data["max_cost_micros"] == 2_500_000
    assert updated_data["hard_stop"] is False


# ============================================================================
# 2. Non-admin receives 403 Forbidden
# ============================================================================


def test_non_admin_cannot_create_limit(
    client: TestClient, org: Organization, member_user: tuple[User, dict[str, str]]
):
    _, headers = member_user
    payload = {
        "limit_key": "ocr.page",
        "period": "MONTH",
        "max_quantity": "100",
    }

    response = client.put(
        f"/api/v1/organizations/{org.id}/usage-limits",
        json=payload,
        headers=headers,
    )

    assert response.status_code == 403


# ============================================================================
# 3. User cannot write to a different organization
# ============================================================================


def test_user_cannot_write_to_different_organization(
    client: TestClient,
    db: Session,
    admin_user: tuple[User, dict[str, str]],
):
    _, headers = admin_user

    # Create a separate, unrelated organization
    other_org = Organization(
        name="Foreign Org",
        slug=f"foreign-org-{uuid.uuid4().hex[:8]}",
    )
    db.add(other_org)
    db.commit()

    payload = {
        "limit_key": "ocr.page",
        "period": "MONTH",
        "max_quantity": "100",
    }

    response = client.put(
        f"/api/v1/organizations/{other_org.id}/usage-limits",
        json=payload,
        headers=headers,
    )

    assert response.status_code in (403, 404)


# ============================================================================
# 4. Missing both ceilings returns 422 Unprocessable Entity
# ============================================================================


def test_missing_both_ceilings_returns_422(
    client: TestClient, org: Organization, admin_user: tuple[User, dict[str, str]]
):
    _, headers = admin_user
    payload = {
        "limit_key": "ocr.page",
        "period": "MONTH",
        "hard_stop": True,
        # max_quantity and max_cost_micros are both omitted
    }

    response = client.put(
        f"/api/v1/organizations/{org.id}/usage-limits",
        json=payload,
        headers=headers,
    )

    assert response.status_code == 422
    assert "At least one of max_quantity or max_cost_micros is required" in response.text


# ============================================================================
# 5. Invalid limit_key returns domain error
# ============================================================================


def test_invalid_limit_key_returns_domain_error(
    client: TestClient, org: Organization, admin_user: tuple[User, dict[str, str]]
):
    _, headers = admin_user
    payload = {
        "limit_key": "nonexistent.fake.event.type",
        "period": "MONTH",
        "max_quantity": "100",
    }

    response = client.put(
        f"/api/v1/organizations/{org.id}/usage-limits",
        json=payload,
        headers=headers,
    )

    assert response.status_code in (400, 422)
    assert "SPEND_LIMIT_MISCONFIGURED" in response.text or "not a valid limit key" in response.text