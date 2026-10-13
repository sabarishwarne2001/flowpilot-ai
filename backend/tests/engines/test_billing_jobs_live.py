"""The billing jobs, run by the worker's own handlers against the fake payment gateway (campaign session 4).

    pytest tests/engines/test_billing_jobs_live.py -q

Session 2 left `billing.seat_sync`, `billing.assemble_invoice`, `billing.reconcile`, `usage.reconcile`
and the `--loop stripe` worker blocked for want of a payment gateway. With the fake gateway (no
network, nothing leaves the machine) and signed test events, each runs here as the worker runs it,
against committed rows:

* the daily seat-drift sweep finds more members than paid seats and queues `billing.seat_sync`,
  which sets the subscription's quantity at the gateway (with proration) and records it;
* a signed renewal (`invoice.created`) posted to the webhook is applied by the `--loop stripe`
  worker: the period closes and its overage goes onto the renewal (F-339); `billing.reconcile`
  replays one named event (charging nothing twice) and drains what is pending;
* the hourly `billing.assemble_invoice` backstop closes a period whose renewal never arrived (its
  overage on the next invoice), and the operator's replay closes one named Stripe invoice;
* `usage.reconcile`, the operator's supplier check, compares a Groq statement export with what
  was metered for the month.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.invoice import Invoice, InvoiceStatus
from app.models.stripe_inbound_event import StripeInboundEvent, StripeInboundStatus
from app.models.subscription import Subscription
from app.services import job_service
from app.services.billing import invoice_service

from tests.conftest import TestSessionLocal
from tests.engines.conftest import drain
from tests.services.test_arch15_gate_15_1_15_2_inbound import (  # noqa: F401
    WEBHOOK_URL,
    FakeStripeGateway,
    billing_org,
    event_body,
    gateway,
    sign,
    stripe_settings,
)
from tests.services.test_arch15_gate_15_3_15_4_subscriptions_seats import add_member, install_subscription
from tests.services.test_arch15_gate_15_5_15_6_invoices import PERIOD_END, PERIOD_START, add_usage
from tests.services.test_overage_is_billed_at_period_close import OVERAGE_CENTS, SEAT_CENTS, _renewal


@pytest.fixture()
def db(db_session, test_sessions):
    """The billing fixtures write through the committed test session the job handlers read."""
    return db_session


def _queue(db, job_type: str, payload: dict | None = None) -> None:
    """Queue a job as the scheduler or an operator does: in its own transaction."""
    from app.workers.handlers import register_all

    register_all(replace=True)
    db.commit()
    with db.begin():
        job_service.enqueue(db, job_type=job_type, payload=payload or {})


def _deliver(client, kind: str, invoice_id: str) -> StripeInboundEvent:
    body = event_body(kind, {"object": "invoice", "id": invoice_id})
    response = client.post(WEBHOOK_URL, content=body, headers={"Stripe-Signature": sign(body),
                                                              "Content-Type": "application/json"})
    assert response.status_code == 200, response.text
    with TestSessionLocal() as fresh:
        return fresh.execute(select(StripeInboundEvent).where(StripeInboundEvent.event_type == kind)
                             .order_by(StripeInboundEvent.received_at.desc()).limit(1)).scalar_one()


def _status(row_id: uuid.UUID) -> StripeInboundStatus:
    with TestSessionLocal() as fresh:
        return fresh.get(StripeInboundEvent, row_id).status


def _statement(stripe_invoice_id: str) -> Invoice | None:
    with TestSessionLocal() as fresh:
        return invoice_service.get_by_stripe_id(fresh, stripe_invoice_id=stripe_invoice_id)


def test_seat_drift_queues_a_seat_sync_that_bills_the_extra_members(db, gateway, billing_org) -> None:  # noqa: F811
    subscription = install_subscription(db, billing_org, gateway, seats=1)
    for _ in range(2):  # members admitted around the seat check (data from before it, a support fix)
        add_member(db, billing_org["organization"])
    db.commit()

    _queue(db, "billing.seat_drift")
    runs = drain(only=["billing.seat_drift", "billing.seat_sync"])

    assert [run.job_type for run in runs] == ["billing.seat_drift", "billing.seat_sync"], runs
    assert runs[1].result["synced"] == 1, runs[1].result
    assert gateway.seat_calls == [("sub_gate15", 3, settings.BILLING_SEAT_PRORATION_BEHAVIOR)]
    with TestSessionLocal() as fresh:
        assert fresh.get(Subscription, subscription.id).seats_purchased == 3


def test_the_stripe_worker_closes_a_period_and_the_reconcile_job_replays_it(
    db, client, gateway, billing_org  # noqa: F811
) -> None:
    from app.worker import run_stripe_inbound_loop

    install_subscription(db, billing_org, gateway, seats=2)
    add_usage(db, billing_org["organization"].id, quantity=2_500_000)
    db.commit()
    _renewal(gateway, "in_live")

    row = _deliver(client, "invoice.created", "in_live")
    shutdown = SimpleNamespace(requested=False)
    worker = threading.Thread(target=run_stripe_inbound_loop, daemon=True,
                              kwargs={"shutdown": shutdown, "batch_size": 10, "lease_seconds": 60,
                                      "idle_sleep_seconds": 0.05})
    worker.start()
    try:
        deadline = time.monotonic() + 30
        while _status(row.id) is not StripeInboundStatus.PROCESSED and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        shutdown.requested = True
        worker.join(timeout=10)
    assert _status(row.id) is StripeInboundStatus.PROCESSED
    statement = _statement("in_live")
    assert statement is not None and statement.assembly_notes["overage_billing"]["status"] == "ON_INVOICE"
    assert [(i["invoice"], i["amount"]) for i in gateway.invoice_items] == [("in_live", OVERAGE_CENTS)]

    # The operator's replay of that one event charges nothing twice.
    _queue(db, "billing.reconcile", {"inbound_event_id": str(row.id)})
    [replay] = drain(only=["billing.reconcile"])
    assert replay.result["status"] == "PROCESSED", replay.result
    assert len(gateway.invoice_items) == 1

    # The batch form drains what is pending: the renewal's payment.
    _renewal(gateway, "in_live", status="paid", paid=2 * SEAT_CENTS + OVERAGE_CENTS)
    paid = _deliver(client, "invoice.paid", "in_live")
    _queue(db, "billing.reconcile")
    [batch] = drain(only=["billing.reconcile"])
    assert batch.result["processed"] == 1 and _status(paid.id) is StripeInboundStatus.PROCESSED, batch.result
    assert _statement("in_live").status is InvoiceStatus.PAID


def test_the_backstop_closes_a_period_whose_renewal_never_arrived(db, gateway, billing_org) -> None:  # noqa: F811
    subscription = install_subscription(db, billing_org, gateway, seats=2)
    # The period ended long ago and no renewal moved it on: its webhook was lost.
    subscription.current_period_start, subscription.current_period_end = PERIOD_START, PERIOD_END
    add_usage(db, billing_org["organization"].id, quantity=2_500_000)
    db.commit()

    _queue(db, "billing.assemble_invoice")  # what the hourly schedule queues
    [run] = drain(only=["billing.assemble_invoice"])

    assert run.result["assembled"] == 1, run.result
    with TestSessionLocal() as fresh:
        [statement] = fresh.execute(select(Invoice).where(Invoice.subscription_id == subscription.id)).scalars()
        assert statement.stripe_invoice_id is None and statement.status is InvoiceStatus.OPEN
        assert statement.assembly_notes["overage_billing"]["status"] == "NEXT_INVOICE"
    [item] = gateway.invoice_items
    assert item["invoice"] is None and item["subscription"] == "sub_gate15" and item["amount"] == OVERAGE_CENTS

    # A period that ended within the grace (its renewal may still be on its way) is left alone,
    # and a closed one is never closed twice.
    subscription.current_period_start = datetime.now(timezone.utc) - timedelta(days=30)
    subscription.current_period_end = datetime.now(timezone.utc) - timedelta(minutes=30)
    db.commit()
    _queue(db, "billing.assemble_invoice")
    [quiet] = drain(only=["billing.assemble_invoice"])
    assert quiet.result["targets"] == 0 and len(gateway.invoice_items) == 1, quiet.result


def test_the_operator_replays_one_renewal_by_its_stripe_id(db, gateway, billing_org) -> None:  # noqa: F811
    install_subscription(db, billing_org, gateway, seats=2)
    add_usage(db, billing_org["organization"].id, quantity=2_500_000)
    db.commit()
    _renewal(gateway, "in_replayed")

    _queue(db, "billing.assemble_invoice", {"stripe_invoice_id": "in_replayed"})
    [run] = drain(only=["billing.assemble_invoice"])

    assert run.result["assembled"] == 1, run.result
    assert _statement("in_replayed") is not None
    assert [(i["invoice"], i["amount"]) for i in gateway.invoice_items] == [("in_replayed", OVERAGE_CENTS)]


def test_the_supplier_statement_check_runs_as_a_job(db) -> None:
    from app.models.organization import Organization, OrganizationStatus
    from tests.services.test_arch14_gate_14_5_reconciliation import seed_ledger

    organization = Organization(slug=f"recon-{uuid.uuid4().hex[:8]}", name="Recon Co.",
                                status=OrganizationStatus.ACTIVE)
    db.add(organization)
    db.flush()
    may = datetime(2026, 5, 1, tzinfo=timezone.utc)
    seed_ledger(db, org=organization.id, quantity=1_000_000, cost=590_000, at=datetime(2026, 5, 14, 9, tzinfo=timezone.utc))
    db.commit()

    csv_text = "Date,Model,Direction,Tokens,Cost\n2026-05-14,llama-3.3-70b,input,1000000,0.59\n"
    _queue(db, "usage.reconcile", {"provider": "groq", "period_start": may.isoformat(),
                                   "period_end": datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat(),
                                   "now": datetime(2026, 6, 10, tzinfo=timezone.utc).isoformat(),
                                   "fetch_options": {"csv_text": csv_text, "source_key": "groq:2026-05"}})
    [run] = drain(only=["usage.reconcile"])

    assert run.result["status"] == "COMPLETED", run.result
    assert run.result["statement_cost_micros"] == 590_000 and run.result["ledger_cost_micros"] == 590_000
    assert int(run.result["drift_micros"]) == 0 and run.result["alert_raised"] is False, run.result
