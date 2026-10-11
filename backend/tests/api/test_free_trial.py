"""Free is a one-week trial, then subscribe (owner decision N-052, built in campaign session 2).

    pytest tests/api/test_free_trial.py -q

The clock belongs to the owner ACCOUNT and starts with their first organization; a second
organization does not restart it. After the end, an organization still on Free is read-only:
uploads, re-processing, the assistant, new workspaces and every other write answer a 402
`TRIAL_ENDED` (remedy `SUBSCRIBE`), and the worker skips its queued engine work. Reading,
deleting, member management, export and checkout stay open. A live paid subscription lifts it.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import settings
from app.core.storage import reset_storage_driver
from app.models.organization import Organization, OrganizationMember, OrganizationRole
from app.models.user import User
from app.models.work_item import WorkItem
from app.workers import tenant_gate
from tests.api.test_seat_purchase_api import _on_business, fake_gateway  # noqa: F401 - fixture
from tests.security.plans import put_on_plan


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(72, 72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture()
def local_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    reset_storage_driver()
    yield tmp_path
    reset_storage_driver()


def _set_trial_end(db: Session, organization: Organization, ends_at: datetime) -> None:
    organization.trial_ends_at = ends_at
    db.commit()


@pytest.fixture()
def on_trial(db_session: Session, tenant, local_storage):
    put_on_plan(db_session, tenant.organization, "free")
    _set_trial_end(db_session, tenant.organization, datetime.now(timezone.utc) + timedelta(days=4))
    return tenant


@pytest.fixture()
def trial_over(db_session: Session, tenant, local_storage):
    put_on_plan(db_session, tenant.organization, "free")
    _set_trial_end(db_session, tenant.organization, datetime.now(timezone.utc) - timedelta(hours=1))
    return tenant


def _upload(client, tenant, name: str = "doc.pdf"):
    return client.post(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items",
        files={"file": (name, _pdf(), "application/pdf")},
        headers=tenant.contributor.headers,
    )


def _assert_trial_ended(response) -> None:
    assert response.status_code == 402, response.text
    body = response.json()
    assert body["code"] == "TRIAL_ENDED", body
    assert body["details"]["reason"] == "TRIAL_ENDED"
    assert body["details"]["remedy"] == "SUBSCRIBE"
    assert body["details"]["trial_ended_at"]
    assert body["details"]["export_allowed"] is True


def _verified_user(db: Session) -> tuple[User, dict[str, str]]:
    user = User(
        email=f"founder-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=security.get_password_hash("test-password"),
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    return user, {"Authorization": f"Bearer {security.create_access_token(subject=str(user.id))}"}


def _publish_free(db: Session) -> None:
    scratch = Organization(slug=f"seed-{uuid.uuid4().hex[:8]}", name="Seed")
    db.add(scratch)
    db.flush()
    put_on_plan(db, scratch, "free")


# --- The clock ----------------------------------------------------------------------------------


def test_a_first_organization_starts_a_seven_day_trial_on_the_owners_account(client, db_session: Session) -> None:
    _publish_free(db_session)
    user, headers = _verified_user(db_session)
    before = datetime.now(timezone.utc)

    response = client.post("/api/v1/organizations", json={"organization_name": "Trial Co"}, headers=headers)
    assert response.status_code == 201, response.text

    db_session.expire_all()
    organization = db_session.get(Organization, uuid.UUID(response.json()["id"]))
    account = db_session.get(User, user.id)
    assert organization.trial_ends_at is not None, "a new organization has no trial clock"
    assert account.trial_ends_at == organization.trial_ends_at
    length = organization.trial_ends_at - before
    assert timedelta(days=7) - timedelta(minutes=1) <= length <= timedelta(days=7, minutes=1)


def test_a_second_organization_does_not_restart_the_accounts_trial(client, db_session: Session) -> None:
    _publish_free(db_session)
    user, headers = _verified_user(db_session)
    first = client.post("/api/v1/organizations", json={"organization_name": "First Co"}, headers=headers)
    assert first.status_code == 201, first.text
    two_days_left = datetime.now(timezone.utc) + timedelta(days=2)
    account = db_session.get(User, user.id)
    account.trial_ends_at = two_days_left
    db_session.commit()

    second = client.post("/api/v1/organizations", json={"organization_name": "Second Co"}, headers=headers)
    assert second.status_code == 201, second.text

    db_session.expire_all()
    organization = db_session.get(Organization, uuid.UUID(second.json()["id"]))
    assert organization.trial_ends_at == two_days_left, "a second organization restarted the trial"


def test_an_account_whose_trial_ended_gets_no_new_trial_from_a_new_organization(client, db_session: Session) -> None:
    _publish_free(db_session)
    user, headers = _verified_user(db_session)
    account = db_session.get(User, user.id)
    account.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=30)
    db_session.commit()

    created = client.post("/api/v1/organizations", json={"organization_name": "Again Co"}, headers=headers)
    assert created.status_code == 201, created.text

    db_session.expire_all()
    organization = db_session.get(Organization, uuid.UUID(created.json()["id"]))
    assert organization.trial_ends_at <= datetime.now(timezone.utc)
    summary = client.get(f"/api/v1/organizations/{organization.id}/billing/access-summary", headers=headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["trial"]["state"] == "ENDED"


# --- During the trial ---------------------------------------------------------------------------


def test_during_the_trial_everything_works_and_every_member_sees_the_days_left(
    client, db_session: Session, on_trial
) -> None:
    assert _upload(client, on_trial).status_code == 201

    summary = client.get(
        f"/api/v1/organizations/{on_trial.organization.id}/billing/access-summary", headers=on_trial.viewer.headers
    )
    assert summary.status_code == 200, summary.text
    trial = summary.json()["trial"]
    assert trial["state"] == "ACTIVE"
    assert trial["days_left"] == 4
    assert trial["ends_at"]

    allowance = client.get(
        f"/api/v1/workspaces/{on_trial.workspace.id}/usage/plan-allowance", headers=on_trial.viewer.headers
    )
    assert allowance.status_code == 200, allowance.text
    assert allowance.json()["trial"]["state"] == "ACTIVE"


# --- After the trial: new work is refused --------------------------------------------------------


def test_after_the_trial_an_upload_is_refused_with_trial_ended(client, db_session: Session, trial_over) -> None:
    _assert_trial_ended(_upload(client, trial_over))
    assert db_session.execute(select(func.count()).select_from(WorkItem)).scalar_one() == 0


def test_after_the_trial_a_new_workspace_is_refused(client, db_session: Session, trial_over) -> None:
    _assert_trial_ended(
        client.post(
            f"/api/v1/organizations/{trial_over.organization.id}/workspaces",
            json={"workspace_name": "Second"},
            headers=trial_over.owner.headers,
        )
    )


@pytest.mark.parametrize("path", ["messages/stream", "messages"])
def test_after_the_trial_the_assistant_is_refused(client, db_session: Session, on_trial, path) -> None:
    created = client.post(
        f"/api/v1/workspaces/{on_trial.workspace.id}/assistant/conversations",
        json={"title": "Before the end"},
        headers=on_trial.contributor.headers,
    )
    assert created.status_code in (200, 201), created.text
    _set_trial_end(db_session, on_trial.organization, datetime.now(timezone.utc) - timedelta(minutes=5))

    _assert_trial_ended(
        client.post(
            f"/api/v1/workspaces/{on_trial.workspace.id}/assistant/conversations/{created.json()['id']}/{path}",
            json={"content": "What is the total of the last invoice?"},
            headers=on_trial.contributor.headers,
        )
    )


def test_after_the_trial_reprocessing_is_refused(client, db_session: Session, on_trial) -> None:
    uploaded = _upload(client, on_trial)
    assert uploaded.status_code == 201, uploaded.text
    item = db_session.get(WorkItem, uuid.UUID(uploaded.json()["id"]))
    for stage in ("EXTRACTING", "EXTRACTED", "ENRICHING", "COMPLETED"):  # the pipeline, one step at a time
        item.pipeline_stage = stage
        db_session.flush()
    db_session.commit()
    _set_trial_end(db_session, on_trial.organization, datetime.now(timezone.utc) - timedelta(minutes=5))

    _assert_trial_ended(
        client.post(
            f"/api/v1/workspaces/{on_trial.workspace.id}/work-items/{item.id}/reprocess",
            headers=on_trial.contributor.headers,
        )
    )


def test_the_admission_service_itself_refuses_so_intake_without_a_session_is_covered(
    db_session: Session, trial_over
) -> None:
    """Public request links and connectors admit documents without a signed-in request."""
    from app.services import plan_admission, trial_service

    with pytest.raises(trial_service.TrialEndedError):
        plan_admission.admit_document(
            db_session, organization_id=trial_over.organization.id, workspace_id=trial_over.workspace.id,
            size_bytes=1000, page_count=1,
        )


# --- After the trial: what stays open -------------------------------------------------------------


def test_after_the_trial_reading_deleting_and_member_management_stay_open(
    client, db_session: Session, on_trial
) -> None:
    uploaded = _upload(client, on_trial)
    assert uploaded.status_code == 201, uploaded.text
    _set_trial_end(db_session, on_trial.organization, datetime.now(timezone.utc) - timedelta(minutes=5))

    listed = client.get(f"/api/v1/workspaces/{on_trial.workspace.id}/work-items", headers=on_trial.viewer.headers)
    assert listed.status_code == 200, listed.text

    membership = db_session.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == on_trial.organization.id,
            OrganizationMember.user_id == on_trial.viewer.user.id,
        )
    ).scalar_one()
    role_change = client.patch(
        f"/api/v1/organizations/{on_trial.organization.id}/members/{membership.id}",
        json={"role": OrganizationRole.ADMIN.value},
        headers=on_trial.owner.headers,
    )
    assert role_change.status_code == 200, role_change.text

    deleted = client.delete(
        f"/api/v1/workspaces/{on_trial.workspace.id}/work-items/{uploaded.json()['id']}",
        headers=on_trial.ws_admin.headers,
    )
    assert deleted.status_code == 204, deleted.text


def test_after_the_trial_every_member_is_told_and_the_owner_can_still_reach_billing(
    client, db_session: Session, trial_over
) -> None:
    summary = client.get(
        f"/api/v1/organizations/{trial_over.organization.id}/billing/access-summary",
        headers=trial_over.contributor.headers,
    )
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["state"] == "TRIAL_ENDED"
    assert body["is_read_only"] is True
    assert body["trial"]["state"] == "ENDED"
    assert body["plan_contacts"], "members are told whom to ask"

    plans = client.get(
        f"/api/v1/organizations/{trial_over.organization.id}/billing/plans", headers=trial_over.owner.headers
    )
    assert plans.status_code == 200, plans.text


# --- The worker ------------------------------------------------------------------------------------


@pytest.mark.parametrize("job_type", ["automation.execute", "knowledge.reindex", "document.verify"])
def test_after_the_trial_queued_engine_work_is_skipped(db_session: Session, trial_over, job_type) -> None:
    calls: list[dict] = []
    handler = tenant_gate.gated(job_type, lambda payload: calls.append(payload) or {"ran": True})

    result = handler({"workspace_id": str(trial_over.workspace.id)})

    assert calls == [], "a job ran for an organization whose trial is over"
    assert result["outcome"] == "SKIPPED"
    assert "trial" in result["reason"]


def test_during_the_trial_queued_engine_work_runs(db_session: Session, on_trial) -> None:
    handler = tenant_gate.gated("knowledge.reindex", lambda payload: {"ran": True})
    assert handler({"workspace_id": str(on_trial.workspace.id)}) == {"ran": True}


# --- Subscribing -------------------------------------------------------------------------------------


def test_subscribing_lifts_the_trial_at_once(
    client, db_session: Session, trial_over, fake_gateway  # noqa: F811
) -> None:
    _assert_trial_ended(_upload(client, trial_over))

    _on_business(db_session, trial_over, fake_gateway, seats=5)

    accepted = _upload(client, trial_over, "after-subscribing.pdf")
    assert accepted.status_code == 201, accepted.text
    summary = client.get(
        f"/api/v1/organizations/{trial_over.organization.id}/billing/access-summary",
        headers=trial_over.contributor.headers,
    )
    assert summary.json()["state"] == "ACTIVE"
    assert summary.json()["trial"]["state"] == "NONE"


def test_a_paid_plan_that_lapses_to_free_after_the_trial_is_asked_to_subscribe(
    client, db_session: Session, trial_over, fake_gateway  # noqa: F811
) -> None:
    from sqlalchemy import text

    _on_business(db_session, trial_over, fake_gateway, seats=5)
    db_session.execute(text("UPDATE subscriptions SET status = 'canceled'::subscription_status, canceled_at = now()"))
    db_session.commit()
    put_on_plan(db_session, trial_over.organization, "free")

    _assert_trial_ended(_upload(client, trial_over))


# --- The two emails ------------------------------------------------------------------------------


@pytest.fixture()
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    from app.core import platform_email

    sent: list[dict] = []

    def capture(*, recipient, subject, html_body, text_body, reply_to=None, **_):
        sent.append({"to": recipient, "subject": subject, "text": text_body, "html": html_body})
        return True, "captured"

    monkeypatch.setattr(platform_email, "send_platform_email", capture)
    return sent


def _account_trial(db: Session, tenant, ends_at: datetime) -> None:
    tenant.owner.user.trial_ends_at = ends_at
    tenant.organization.trial_ends_at = ends_at
    db.commit()


def test_the_owner_is_emailed_once_two_days_before_the_end(db_session: Session, on_trial, outbox) -> None:
    from app.workers.handlers.trial import run_reminders

    _account_trial(db_session, on_trial, datetime.now(timezone.utc) + timedelta(days=1, hours=20))

    assert run_reminders(db_session)["ending"] == 1
    assert run_reminders(db_session)["ending"] == 0, "the reminder went twice"
    assert [mail["to"] for mail in outbox] == [on_trial.owner.user.email]
    assert "trial ends" in outbox[0]["subject"]
    assert on_trial.organization.name in outbox[0]["text"]
    assert f"/organizations/{on_trial.organization.slug}/billing" in outbox[0]["text"]


def test_the_owner_is_emailed_once_when_the_trial_ends(db_session: Session, trial_over, outbox) -> None:
    from app.workers.handlers.trial import run_reminders

    _account_trial(db_session, trial_over, datetime.now(timezone.utc) - timedelta(hours=2))

    assert run_reminders(db_session)["ended"] == 1
    assert run_reminders(db_session)["ended"] == 0
    assert len(outbox) == 1 and "has ended" in outbox[0]["subject"]
    assert "Nothing has been deleted" in outbox[0]["text"]


def test_an_owner_who_subscribed_is_not_emailed(
    db_session: Session, trial_over, outbox, fake_gateway  # noqa: F811
) -> None:
    from app.workers.handlers.trial import run_reminders

    _account_trial(db_session, trial_over, datetime.now(timezone.utc) - timedelta(hours=2))
    _on_business(db_session, trial_over, fake_gateway, seats=5)

    assert run_reminders(db_session) == {"ending": 0, "ended": 0}
    assert outbox == []


def test_the_reminder_sweep_is_registered_and_scheduled() -> None:
    from app.workers import handlers, profiles, scheduler

    assert "trial.reminders" in handlers._HANDLERS  # noqa: SLF001
    assert any(job.job_type == "trial.reminders" for job in scheduler.DEFAULT_SCHEDULE)
    assert any(
        profile.job_types is not None and "trial.reminders" in profile.job_types
        for profile in profiles.PROFILES.values()
    ), "no worker profile claims the sweep, so it would never run"
