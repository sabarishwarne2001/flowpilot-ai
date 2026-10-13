"""Billing and security notices reach people by email, not only the bell (campaign session 3, N-062).

Before: every organization notice (`organization_notification_service.emit`) was an in-app row and
nothing else. "Payment failed", "Organization is read-only", "Subscription ended", a role change,
a new SCIM key or a lapsed verified domain were never emailed, so an owner who did not happen to
sign in learned about a failed payment when the organization went read-only. And there was no
way for a person to choose which emails they get.

Now: Security and Billing notices are always emailed (no switch) to the people they concern:
billing to owners and Billing members (administrators keep the in-app notice), security to
owners and administrators. Workflow notices (an automation paused, a document failed) are
emailed when they are warnings, unless the person turned that category off. Essential mail goes
through the platform relay, with a link to act and a link to the person's email preferences.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.job import Job
from app.models.notification import Notification, NotificationChannel, NotificationPriority, NotificationType
from app.models.notification_delivery import NotificationDelivery, NotificationDeliveryStatus
from app.models.organization import OrganizationRole
from app.services import notification_preferences_service as preferences
from app.services import organization_notification_service as notices
from app.services.identity import security_emitters
from app.services.notification import outbox_dispatcher
from app.workers.handlers import register_all
from tests.conftest import Fixture, Persona, _make_user, _seat


@pytest.fixture(autouse=True)
def _handlers():
    register_all(replace=True)


@pytest.fixture
def finance(db_session: Session, tenant: Fixture) -> Persona:
    person = _make_user(db_session, f"finance-{tenant.organization.slug}@acme.com")
    _seat(db_session, tenant.organization, person, OrganizationRole.BILLING)
    db_session.commit()
    return person


def _emails(db: Session, organization_id) -> dict[str, NotificationDelivery]:
    rows = db.execute(
        select(NotificationDelivery).where(
            NotificationDelivery.organization_id == organization_id,
            NotificationDelivery.channel == NotificationChannel.EMAIL,
        )
    ).scalars().all()
    return {row.target: row for row in rows}


def _in_app(db: Session, organization_id, title: str) -> set:
    return set(
        db.execute(
            select(Notification.user_id).where(
                Notification.organization_id == organization_id, Notification.title == title
            )
        ).scalars()
    )


def test_a_failed_payment_is_emailed_to_owners_and_billing_members(
    db_session: Session, tenant: Fixture, finance: Persona
) -> None:
    notices.notify_subscription_state_changed(
        db_session, organization_id=tenant.organization.id, plan_name="Team",
        previous_status="active", status="past_due", grace_ends_on="20 Oct 2026",
    )
    db_session.flush()

    emails = _emails(db_session, tenant.organization.id)
    assert set(emails) == {tenant.owner.user.email, finance.user.email}
    # Administrators still see it in the app; plain members are not told about money.
    assert _in_app(db_session, tenant.organization.id, "Payment failed") == {
        tenant.owner.user.id, tenant.org_admin.user.id, finance.user.id
    }
    # Each email is a queued delivery job (the outbox), not a send inside the request.
    jobs = db_session.execute(select(Job).where(Job.job_type == outbox_dispatcher.JOB_TYPE)).scalars().all()
    assert {job.payload["delivery_id"] for job in jobs} >= {str(row.id) for row in emails.values()}
    assert all(row.payload["category"] == preferences.BILLING for row in emails.values())


def test_a_plan_change_is_emailed_even_though_it_is_routine(
    db_session: Session, tenant: Fixture, finance: Persona
) -> None:
    notices.notify_plan_changed(
        db_session, organization_id=tenant.organization.id, previous_plan_key="starter", plan_name="Business"
    )
    db_session.flush()
    assert set(_emails(db_session, tenant.organization.id)) == {tenant.owner.user.email, finance.user.email}


def test_a_dunning_step_is_emailed(db_session: Session, tenant: Fixture, finance: Persona) -> None:
    from types import SimpleNamespace

    from app.services.billing import dunning_service

    invoice = SimpleNamespace(number="INV-0042", amount_due_micros=49_000_000, currency="USD")
    dunning_service._notify(  # noqa: SLF001 - the step's own notice, as the sweep sends it
        db_session, organization_id=tenant.organization.id, invoice=invoice, step=dunning_service.DunningStep.NOTIFY_1
    )
    db_session.flush()
    assert set(_emails(db_session, tenant.organization.id)) == {tenant.owner.user.email, finance.user.email}


def test_a_security_change_is_emailed_to_owners_and_administrators(
    db_session: Session, tenant: Fixture, finance: Persona
) -> None:
    security_emitters.notify_scim_key_created(
        db_session, organization_id=tenant.organization.id, display_name="Okta", actor=tenant.org_admin.user
    )
    db_session.flush()
    emails = _emails(db_session, tenant.organization.id)
    assert set(emails) == {tenant.owner.user.email, tenant.org_admin.user.email}
    assert all(row.payload["category"] == preferences.SECURITY for row in emails.values())


def test_a_role_change_is_emailed_to_the_person(db_session: Session, tenant: Fixture) -> None:
    notices.notify_role_changed(
        db_session, organization_id=tenant.organization.id, target_user_id=tenant.contributor.user.id,
        organization_name="Acme Inc.", previous_role="MEMBER", new_role="ADMIN", actor_display="Olivia",
    )
    db_session.flush()
    assert set(_emails(db_session, tenant.organization.id)) == {tenant.contributor.user.email}


def test_a_notice_that_already_has_its_own_email_is_not_sent_twice(db_session: Session, tenant: Fixture) -> None:
    notices.notify_ownership_transfer_proposed(
        db_session, organization_id=tenant.organization.id, recipient_user_id=tenant.org_admin.user.id,
        organization_name="Acme Inc.", initiator_display="Olivia",
    )
    db_session.flush()
    assert _emails(db_session, tenant.organization.id) == {}


def _automation_warning(db: Session, tenant: Fixture, priority=NotificationPriority.WARNING) -> None:
    notices.emit_to_roles(
        db, organization_id=tenant.organization.id, roles=notices.SECURITY_ROLES,
        title="Automatic approval paused: Invoices", message="The error rate rose above the limit.",
        notification_type=NotificationType.AUTOMATION, priority=priority,
    )
    db.flush()


def test_a_workflow_warning_is_emailed_unless_the_person_turned_it_off(
    db_session: Session, tenant: Fixture
) -> None:
    preferences.set_email(db_session, user_id=tenant.owner.user.id, category_key=preferences.WORKFLOW, enabled=False)
    _automation_warning(db_session, tenant)
    assert set(_emails(db_session, tenant.organization.id)) == {tenant.org_admin.user.email}
    # Turning email off never hides the notice itself.
    assert tenant.owner.user.id in _in_app(db_session, tenant.organization.id, "Automatic approval paused: Invoices")


def test_a_routine_workflow_notice_is_not_emailed(db_session: Session, tenant: Fixture) -> None:
    _automation_warning(db_session, tenant, priority=NotificationPriority.INFO)
    assert _emails(db_session, tenant.organization.id) == {}


def test_essential_categories_cannot_be_turned_off(db_session: Session, tenant: Fixture) -> None:
    for key in (preferences.SECURITY, preferences.BILLING):
        with pytest.raises(preferences.PreferenceError):
            preferences.set_email(db_session, user_id=tenant.owner.user.id, category_key=key, enabled=False)


def test_a_workspace_notice_obeys_the_persons_choice_too(db_session: Session, tenant: Fixture) -> None:
    """The automation `notify_role` action and document notices go through `resolve_preferences`."""
    notification = Notification(
        title="Document failed", message="We could not read invoice.pdf.",
        notification_type=NotificationType.DOCUMENT, priority=NotificationPriority.ERROR,
        delivery_channel=NotificationChannel.IN_APP, workspace_id=tenant.workspace.id,
        organization_id=tenant.organization.id, user_id=tenant.contributor.user.id,
    )
    db_session.add(notification)
    db_session.flush()
    channels = lambda: {p.channel for p in outbox_dispatcher.resolve_preferences(  # noqa: E731
        db_session, user=tenant.contributor.user, notification=notification)}
    assert NotificationChannel.EMAIL in channels()
    preferences.set_email(db_session, user_id=tenant.contributor.user.id, category_key=preferences.WORKFLOW, enabled=False)
    assert channels() == {NotificationChannel.IN_APP}


def test_essential_mail_goes_through_the_platform_relay_with_a_link_to_act(
    db_session: Session, tenant: Fixture, finance: Persona, monkeypatch
) -> None:
    from pydantic import SecretStr

    from app.services.email_service import email_service

    monkeypatch.setattr(settings, "PLATFORM_SMTP_HOST", "relay.flowpilot.test")
    monkeypatch.setattr(settings, "PLATFORM_SMTP_USERNAME", "apikey")
    monkeypatch.setattr(settings, "PLATFORM_SMTP_PASSWORD", SecretStr("test-only"))
    sent: list[dict] = []

    def fake_send_html_email(**kwargs):
        sent.append(kwargs)
        return True, "ok"

    monkeypatch.setattr(email_service, "send_html_email", fake_send_html_email)

    notices.notify_subscription_state_changed(
        db_session, organization_id=tenant.organization.id, plan_name="Team",
        previous_status="active", status="unpaid", grace_ends_on=None,
    )
    db_session.flush()
    delivery = _emails(db_session, tenant.organization.id)[finance.user.email]
    claimed = outbox_dispatcher.claim_delivery(db_session, delivery_id=delivery.id)
    assert asyncio.run(outbox_dispatcher.send_delivery(db_session, claimed)) is True
    assert claimed.status is NotificationDeliveryStatus.DELIVERED

    (mail,) = sent
    assert mail["settings"].smtp_host == "relay.flowpilot.test"
    assert mail["recipient"] == finance.user.email
    assert mail["subject"] == "Organization is read-only"
    frontend = settings.FRONTEND_URL.rstrip("/")
    assert f"{frontend}/organizations/{tenant.organization.slug}/billing" in mail["html_body"]
    assert f"{frontend}/settings?section=notifications" in mail["html_body"]
    assert "Acme Inc." in mail["text_body"]
