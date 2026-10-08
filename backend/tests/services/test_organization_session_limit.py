"""An organization's maximum session age ends its members' sessions (F-204).

The identity console's Security tab reads "Sessions end after N hours" from the organization's
security policy, and the owner can set that limit (PUT /identity/security-policy
max_session_age_s). Nothing enforced it: every session lasted the platform's 12 hours whatever
the organization asked for. A session belongs to a person, not to one organization, so the
strictest limit among the organizations they are an active member of applies; the platform's own
limit still applies on top.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.models.identity import TenantSecurityPolicy
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.user_session import SessionRevokedReason
from app.services import session_service as svc


@pytest.fixture(autouse=True)
def _level_two_limits(monkeypatch):
    monkeypatch.setattr(settings, "SESSION_ABSOLUTE_LIFETIME_HOURS", 12)
    monkeypatch.setattr(settings, "SESSION_IDLE_TIMEOUT_MINUTES", 30)
    monkeypatch.setattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 10)


def _member_of(db, user, *, max_session_age_s, status=MembershipStatus.ACTIVE,
               org_status=OrganizationStatus.ACTIVE):
    org = Organization(slug=f"f204-{uuid.uuid4().hex[:8]}", name="F-204", status=org_status)
    db.add(org)
    db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id,
                              role=OrganizationRole.MEMBER, status=status))
    db.add(TenantSecurityPolicy(organization_id=org.id, max_session_age_s=max_session_age_s))
    db.flush()
    return org


def _signed_in(db, user, *, ago):
    return svc.create_session(db, user=user, authenticated_at=datetime.now(UTC) - ago)


def test_the_organization_limit_ends_the_session(db, user):
    _member_of(db, user, max_session_age_s=3600)
    issued = _signed_in(db, user, ago=timedelta(minutes=61))

    with pytest.raises(svc.ExpiredRefreshTokenError):
        svc.rotate_session(db, refresh_token=issued.plaintext_token)
    db.refresh(issued.session)
    assert issued.session.revoked_reason is SessionRevokedReason.EXPIRED


def test_within_the_organization_limit_the_session_refreshes(db, user):
    _member_of(db, user, max_session_age_s=3600)
    issued = _signed_in(db, user, ago=timedelta(minutes=50))

    svc.rotate_session(db, refresh_token=issued.plaintext_token)


def test_the_strictest_organization_wins(db, user):
    _member_of(db, user, max_session_age_s=8 * 3600)
    _member_of(db, user, max_session_age_s=2 * 3600)
    issued = _signed_in(db, user, ago=timedelta(hours=2, minutes=1))

    with pytest.raises(svc.ExpiredRefreshTokenError):
        svc.rotate_session(db, refresh_token=issued.plaintext_token)


def test_a_longer_organization_limit_does_not_extend_the_platform_limit(db, user):
    _member_of(db, user, max_session_age_s=48 * 3600)
    issued = _signed_in(db, user, ago=timedelta(hours=12, minutes=1))

    with pytest.raises(svc.ExpiredRefreshTokenError):
        svc.rotate_session(db, refresh_token=issued.plaintext_token)


@pytest.mark.parametrize(
    ("status", "org_status"),
    [
        (MembershipStatus.DEACTIVATED, OrganizationStatus.ACTIVE),
        (MembershipStatus.INVITED, OrganizationStatus.ACTIVE),
        (MembershipStatus.ACTIVE, OrganizationStatus.ARCHIVED),
    ],
)
def test_an_organization_the_person_cannot_enter_does_not_limit_them(db, user, status, org_status):
    _member_of(db, user, max_session_age_s=3600, status=status, org_status=org_status)
    issued = _signed_in(db, user, ago=timedelta(minutes=61))

    svc.rotate_session(db, refresh_token=issued.plaintext_token)
