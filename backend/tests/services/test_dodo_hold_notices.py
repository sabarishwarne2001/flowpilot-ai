"""Reminders while a Dodo renewal is on hold, and the read-only notice (website and payments session, C1).

When a Dodo renewal is declined the subscription goes `on_hold` and the organization keeps full
access through a grace window (13 days by default) while Dodo retries. The owner was told once
("Payment failed"); then, if no further webhook came, the organization turned read-only when the
window closed without a word, and nobody was warned beforehand. The Stripe path sends a reminder,
a final notice before access changes and a read-only notice (N-072). The `billing.hold_notices`
job now does the same on Dodo, by the clock: a reminder a week before the window closes, a final
notice two days before, and the read-only notice once it has closed, each sent once per hold.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models.notification import Notification
from app.services.billing import dunning_service
from tests.services.test_dodo_reconciliation import (  # noqa: F401 - fixtures
    deliver,
    dodo,
    org,
    row,
    subscribe,
)

REMINDER = "Payment still outstanding"
FINAL = "Final notice before access changes"
READ_ONLY = "Organization is read-only"


def _titles(db, organization_id) -> list[str]:
    db.expire_all()
    return list(db.execute(select(Notification.title).where(Notification.organization_id == organization_id)
                           .order_by(Notification.created_at)).scalars())


def _sweep(db, now) -> None:
    from app.workers import handlers

    assert "billing.hold_notices" in handlers._HANDLERS, "no job tells the owner before a Dodo hold turns read-only"
    from app.services.billing import hold_notice_service

    hold_notice_service.send_due(db, now=now)


def test_the_owner_is_reminded_warned_and_told_before_and_when_access_changes(dodo, client, db, org) -> None:
    organization_id = org["organization"].id
    sub_id = subscribe(dodo, client, db, org)
    dodo.renew(sub_id, succeed=False)
    deliver(dodo, client, db)
    grace = row(db, sub_id).grace_ends_at
    assert grace is not None and _titles(db, organization_id).count("Payment failed") == 1

    # Early in the hold: nothing more to say yet.
    _sweep(db, grace - timedelta(days=10))
    assert not {REMINDER, FINAL, READ_ONLY} & set(_titles(db, organization_id))

    # A week left: a reminder, once however often the job runs.
    _sweep(db, grace - timedelta(days=6))
    _sweep(db, grace - timedelta(days=5))
    assert _titles(db, organization_id).count(REMINDER) == 1

    # Two days left: the final notice.
    _sweep(db, grace - timedelta(days=1))
    assert _titles(db, organization_id).count(FINAL) == 1

    # The window closes: read-only, and the owner is told so, once.
    _sweep(db, grace + timedelta(minutes=5))
    _sweep(db, grace + timedelta(hours=2))
    titles = _titles(db, organization_id)
    assert titles.count(READ_ONLY) == 1 and titles.count(REMINDER) == 1 and titles.count(FINAL) == 1


def test_a_job_that_was_down_sends_only_what_is_true_now(dodo, client, db, org) -> None:
    organization_id = org["organization"].id
    sub_id = subscribe(dodo, client, db, org)
    dodo.renew(sub_id, succeed=False)
    deliver(dodo, client, db)
    grace = row(db, sub_id).grace_ends_at
    _sweep(db, grace + timedelta(days=1))
    titles = _titles(db, organization_id)
    assert titles.count(READ_ONLY) == 1 and REMINDER not in titles and FINAL not in titles


def test_a_fixed_card_stops_the_reminders_and_a_new_hold_starts_them_again(dodo, client, db, org) -> None:
    organization_id = org["organization"].id
    sub_id = subscribe(dodo, client, db, org)
    dodo.renew(sub_id, succeed=False)
    deliver(dodo, client, db)
    first_grace = row(db, sub_id).grace_ends_at
    _sweep(db, first_grace - timedelta(days=6))
    dodo.fix_card(sub_id)
    deliver(dodo, client, db)
    assert row(db, sub_id).grace_ends_at is None
    _sweep(db, first_grace - timedelta(days=1))
    assert FINAL not in _titles(db, organization_id)
    assert dunning_service.access_state(db, organization_id=organization_id) is dunning_service.BillingAccessState.ACTIVE

    # The next renewal is declined too: a new hold, and its own reminders.
    dodo.renew(sub_id, succeed=False)
    deliver(dodo, client, db)
    second_grace = row(db, sub_id).grace_ends_at
    assert second_grace is not None and second_grace != first_grace
    _sweep(db, second_grace - timedelta(days=6))
    assert _titles(db, organization_id).count(REMINDER) == 2
