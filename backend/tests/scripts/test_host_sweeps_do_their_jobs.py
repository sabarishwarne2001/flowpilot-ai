"""The nightly host sweeps do what their cron lines say (campaign session 3, ledger: cron rows).

`deploy/cron.d/servabo-sweepers` runs these from the host, not the worker, and their tests only
checked `--help` and dry runs. Each job is run here for real against data placed on both sides of
its line: what is past retention goes, what is not stays.

* ARCH-09 (`servabo-sweep arch09 --all --apply`): published outbox events older than 7 days.
* ARCH-07 (`servabo-sweep arch07 --all --apply`): deleted files past the reclamation window
  (row and stored object), and email-change requests past their expiry (audit retention has its
  own test, `test_audit_retention_follows_the_policy.py`).
* identity and invitations are proven by `tests/services/test_session_service.py`,
  `test_auth_token_service.py` and `test_invitation_sweep.py`.
"""

from __future__ import annotations

import hashlib
import sys
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.storage import ObjectNotFoundError, get_storage_driver
from app.models.email_change_request import EmailChangeRequest, EmailChangeStatus
from app.models.outbox_event import OutboxEvent, OutboxEventStatus
from app.models.uploaded_file import UploadedFile
from scripts import sweep_arch07, sweep_arch09
from tests.api.test_free_trial import local_storage  # noqa: F401 - fixture
from tests.conftest import Fixture


def _event(db: Session, tenant: Fixture, *, status: OutboxEventStatus, published_days_ago: int | None) -> uuid.UUID:
    event = OutboxEvent(
        organization_id=tenant.organization.id, event_type="document.completed", payload={}, status=status,
        published_at=(datetime.now(UTC) - timedelta(days=published_days_ago)) if published_days_ago is not None else None,
    )
    db.add(event)
    db.commit()
    return event.id


def test_outbox_retention_removes_only_old_published_events(
    db_session: Session, tenant: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _event(db_session, tenant, status=OutboxEventStatus.PUBLISHED, published_days_ago=10)
    recent = _event(db_session, tenant, status=OutboxEventStatus.PUBLISHED, published_days_ago=1)
    waiting = _event(db_session, tenant, status=OutboxEventStatus.PENDING, published_days_ago=None)

    monkeypatch.setattr(sys, "argv", ["sweep_arch09", "--outbox", "--apply"])
    assert sweep_arch09.main() == 0
    db_session.expire_all()
    left = set(db_session.execute(select(OutboxEvent.id).where(OutboxEvent.id.in_([old, recent, waiting]))).scalars())
    assert left == {recent, waiting}


def test_deleted_files_past_the_window_are_reclaimed_with_their_objects(
    db_session: Session, tenant: Fixture, local_storage  # noqa: F811
) -> None:
    driver = get_storage_driver()
    rows = {}
    for label, days in (("old", settings.FILE_RECLAMATION_DAYS + 2), ("recent", 0)):
        key = f"reclaim-test/{uuid.uuid4().hex}.pdf"
        data = b"%PDF-1.4 " + label.encode()
        driver.put(key, data, "application/pdf")
        row = UploadedFile(
            owner_id=tenant.owner.user.id, organization_id=tenant.organization.id, file_path=key,
            original_filename=f"{label}.pdf", mime_type="application/pdf", file_size=len(data),
            checksum_sha256=hashlib.sha256(data).hexdigest(), deleted_at=datetime.now(UTC) - timedelta(days=days),
        )
        db_session.add(row)
        db_session.commit()
        rows[label] = (row.id, key)

    result = sweep_arch07.sweep_file_reclamation(db_session, dry_run=False)
    assert result.failed == 0, result.notes
    db_session.expire_all()
    assert db_session.get(UploadedFile, rows["old"][0]) is None
    with pytest.raises(ObjectNotFoundError):
        driver.get(rows["old"][1])
    assert db_session.get(UploadedFile, rows["recent"][0]) is not None
    assert driver.get(rows["recent"][1])


def test_email_change_requests_past_expiry_are_marked_expired(db_session: Session, tenant: Fixture) -> None:
    def request(persona, expires_in: timedelta) -> uuid.UUID:
        # One pending change per person, so each request belongs to someone else.
        row = EmailChangeRequest(
            user_id=persona.user.id, new_email=f"new-{uuid.uuid4().hex[:6]}@example.com",
            token_hash=uuid.uuid4().hex, status=EmailChangeStatus.PENDING,
            expires_at=datetime.now(UTC) + expires_in,
        )
        db_session.add(row)
        db_session.commit()
        return row.id

    lapsed = request(tenant.viewer, -timedelta(hours=1))
    live = request(tenant.contributor, timedelta(hours=1))
    result = sweep_arch07.sweep_expired_requests(db_session, dry_run=False)
    assert result.failed == 0, result.notes
    db_session.expire_all()
    assert db_session.get(EmailChangeRequest, lapsed).status == EmailChangeStatus.EXPIRED
    assert db_session.get(EmailChangeRequest, live).status == EmailChangeStatus.PENDING
