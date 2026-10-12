"""Owners are told before and when a plan limit runs out (campaign session 3, N-062).

Before: the allowance screen showed "NEAR" and "REACHED", but only to someone looking at it.
An owner learned that documents were being refused when a colleague complained. Now the hourly
`usage.limit_alerts` sweep tells the organization's owners (the people who can change the plan)
once at 80% of a monthly limit (a Usage warning, which a person may turn off) and once when it
is reached (Billing: always emailed), once per meter, limit and month, however often it runs.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.notification_delivery import NotificationDelivery
from app.models.notification import NotificationChannel
from app.services import notification_preferences_service as preferences
from app.services import usage_service
from app.workers.handlers.usage_alerts import run_limit_alerts
from tests.conftest import Fixture
from tests.security.plans import put_on_plan


@pytest.fixture()
def free(db_session: Session, tenant: Fixture) -> Fixture:
    put_on_plan(db_session, tenant.organization, "free")  # 25 document uploads a month
    db_session.commit()
    return tenant


def _use(db: Session, organization_id: uuid.UUID, quantity: int) -> None:
    usage_service.record_usage(
        db, organization_id=organization_id, event_type="document.upload", quantity=quantity,
        cost_micros=0, provider="internal",
    )
    db.commit()


def _notices(db: Session, organization_id: uuid.UUID) -> list[tuple[str, uuid.UUID]]:
    return [
        (row.title, row.user_id)
        for row in db.execute(
            select(Notification).where(Notification.organization_id == organization_id).order_by(Notification.created_at)
        ).scalars()
    ]


def _emailed(db: Session, organization_id: uuid.UUID) -> list[tuple[str, str]]:
    return [
        (row.payload["title"], row.target)
        for row in db.execute(
            select(NotificationDelivery).where(
                NotificationDelivery.organization_id == organization_id,
                NotificationDelivery.channel == NotificationChannel.EMAIL,
            ).order_by(NotificationDelivery.created_at)
        ).scalars()
    ]


def test_owners_are_warned_once_at_80_percent(db_session: Session, free: Fixture) -> None:
    _use(db_session, free.organization.id, 19)
    run_limit_alerts(db_session)
    assert _notices(db_session, free.organization.id) == [], "76% is not a warning"

    _use(db_session, free.organization.id, 1)  # 20 of 25
    assert run_limit_alerts(db_session)["sent"] == 1
    notices = _notices(db_session, free.organization.id)
    assert notices == [("You have used 80% of this month's document uploads", free.owner.user.id)]
    assert _emailed(db_session, free.organization.id) == [
        ("You have used 80% of this month's document uploads", free.owner.user.email)
    ]
    # The hourly sweep runs again (and on other workers): nothing more is sent.
    assert run_limit_alerts(db_session)["sent"] == 0
    assert len(_notices(db_session, free.organization.id)) == 1


def test_reaching_the_limit_is_always_emailed(db_session: Session, free: Fixture) -> None:
    preferences.set_email(db_session, user_id=free.owner.user.id, category_key=preferences.USAGE, enabled=False)
    db_session.commit()
    _use(db_session, free.organization.id, 21)
    run_limit_alerts(db_session)
    assert _emailed(db_session, free.organization.id) == [], "the owner turned usage warnings off"
    assert len(_notices(db_session, free.organization.id)) == 1, "the in-app notice still appears"

    _use(db_session, free.organization.id, 4)  # 25 of 25
    run_limit_alerts(db_session)
    title = "You have reached this month's document upload limit"
    assert (title, free.owner.user.email) in _emailed(db_session, free.organization.id)
    message = db_session.execute(select(Notification.message).where(Notification.title == title)).scalar_one()
    assert "25 of 25" in message and "Acme Inc." in message
    assert run_limit_alerts(db_session)["sent"] == 0


def test_members_who_cannot_change_the_plan_are_not_emailed(db_session: Session, free: Fixture) -> None:
    _use(db_session, free.organization.id, 25)
    run_limit_alerts(db_session)
    recipients = {user_id for _, user_id in _notices(db_session, free.organization.id)}
    assert recipients == {free.owner.user.id}


def test_an_unused_organization_is_not_looked_at(db_session: Session, free: Fixture) -> None:
    assert run_limit_alerts(db_session) == {"organizations": 0, "sent": 0}


def test_the_sweep_is_scheduled_and_runs_on_a_worker() -> None:
    from app.workers import handlers, profiles, scheduler

    assert "usage.limit_alerts" in handlers._HANDLERS  # noqa: SLF001
    assert any(job.job_type == "usage.limit_alerts" for job in scheduler.DEFAULT_SCHEDULE)
    assert any(
        profile.job_types is not None and "usage.limit_alerts" in profile.job_types
        for profile in profiles.PROFILES.values()
    )
