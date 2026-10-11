"""A new organization starts on the Free plan (campaign session 1).

    pytest tests/api/test_new_organization_starts_on_free.py -q

`POST /organizations` created the organization, its workspace and the founder's
memberships, and never gave it a plan: `organizations.quota_tier_id` stayed NULL.
An organization with no plan resolves no tier, so the platform-key entitlement
is absent (every AI call refused) and the metered limits fall back to the
platform defaults (2,000 OCR pages and 2,000,000 input tokens a month, 20 times
Free's allowance) instead of Free's.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.models.organization import Organization
from app.models.user import User
from app.services import quota_service
from tests.security.plans import put_on_plan


def _verified_user(db: Session) -> tuple[User, dict[str, str]]:
    user = User(
        email=f"founder-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=security.get_password_hash("test-password"),
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    token = security.create_access_token(subject=str(user.id))
    return user, {"Authorization": f"Bearer {token}"}


def _publish_free(db: Session) -> None:
    """Publish the seeded Free tier, the way the deploy seed does."""
    scratch = Organization(slug=f"seed-{uuid.uuid4().hex[:8]}", name="Seed")
    db.add(scratch)
    db.flush()
    put_on_plan(db, scratch, "free")


def test_a_new_organization_is_on_the_free_plan(client, db_session: Session) -> None:
    _publish_free(db_session)
    _, headers = _verified_user(db_session)

    response = client.post(
        "/api/v1/organizations",
        json={"organization_name": "Founders Ltd"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    organization_id = uuid.UUID(response.json()["id"])

    db_session.expire_all()
    tier = quota_service.resolve_tier(db_session, organization_id=organization_id)
    assert tier is not None, "the new organization resolves no plan at all"
    assert tier.key == "free"

    held = {entry.limit_key for entry in tier.entries}
    assert "llm.platform_key" in held, "Free includes AI on the platform account"

    organization = db_session.execute(
        select(Organization).where(Organization.id == organization_id)
    ).scalar_one()
    assert organization.quota_tier_id == tier.id


def test_the_plan_seed_puts_organizations_created_before_on_free(db_session: Session) -> None:
    """The organizations created before this fix are repaired on the next deploy."""
    from tests.security.plans import _seed

    _publish_free(db_session)
    planless = Organization(slug=f"old-{uuid.uuid4().hex[:8]}", name="Created before the fix")
    db_session.add(planless)
    db_session.commit()

    assert _seed().assign_free_to_planless(db_session) >= 1  # noqa: SLF001 - the seed's own step
    db_session.commit()

    tier = quota_service.resolve_tier(db_session, organization_id=planless.id)
    assert tier is not None and tier.key == "free"
    assert _seed().assign_free_to_planless(db_session) == 0, "a second run changes nothing"
