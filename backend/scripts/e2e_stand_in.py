"""The browser journeys' stand-ins for the clock and the payment gateway (campaign session 4, §D).

    python scripts/e2e_stand_in.py <command> --org <slug> [options]     (prints one JSON line)

The end-to-end journeys (`frontend/e2e/tests/6*-journey-*.spec.ts`) drive the real web app, API
and worker. Two things a browser cannot do in a sandbox are played here instead:

* **the clock** — a free trial that reaches its last days or its end (`trial-ending`,
  `trial-ends`), and the hourly job that emails about it (`run-job trial.reminders`);
* **the payment gateway's side** — what Stripe does after the customer leaves FlowPilot (pays at
  checkout, changes seats or plan or cancels in the portal, a renewal whose card is declined, the
  retries running out, the card fixed). Each is a Stripe event handed to FlowPilot's own
  `reconcile_service.reconcile_event`, the code a signed webhook reaches through the `--loop
  stripe` worker, with a gateway that answers its re-fetches from FlowPilot's database (as the
  test suite's fake gateway does). Everything that follows (plan, seats, statements, dunning
  emails, read-only access) is FlowPilot's real behaviour.

Development and test databases only: it refuses to run when ENVIRONMENT is production.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402

if str(settings.ENVIRONMENT).strip().lower() == "production":
    raise SystemExit("e2e_stand_in.py refuses to run against a production deployment.")

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.billing_account import BillingAccount  # noqa: E402
from app.models.organization import Organization, OrganizationMember, OrganizationRole  # noqa: E402
from app.models.subscription import Subscription  # noqa: E402
from app.services.billing import reconcile_service, stripe_gateway  # noqa: E402
from app.services.billing.stripe_gateway import (  # noqa: E402
    StripeCustomerSnapshot,
    StripeEvent,
    StripeGateway,
    StripeInvoiceSnapshot,
    StripeObjectNotFoundError,
)

NOW = datetime.now(timezone.utc)
STATE = Path(os.environ.get("E2E_LOG_DIR", "/tmp")) / "e2e-stand-in-gateway.json"


def _load() -> dict[str, Any]:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"subscriptions": {}, "invoices": {}}


def _save(state: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, default=str))


def _epoch(moment: datetime) -> int:
    return int(moment.timestamp())


class StandInGateway(StripeGateway):
    """Stripe's answers to FlowPilot's re-fetches, kept in a small state file. No network."""

    def __init__(self, state: dict[str, Any], organization_id: uuid.UUID) -> None:
        super().__init__(api_key="sk_test_e2e_stand_in", api_version="2026-07-29.dahlia")
        self.state = state
        self.organization_id = organization_id

    def _stripe_client(self) -> Any:  # pragma: no cover - a stand-in never calls Stripe
        raise stripe_gateway.StripeNotConfiguredError("the e2e stand-in never calls Stripe")

    def fetch_subscription(self, subscription_id: str):
        raw = self.state["subscriptions"].get(subscription_id)
        if raw is None:
            raise StripeObjectNotFoundError(f"no such subscription {subscription_id}")
        return self._snapshot_subscription(raw, state_version=int(raw["_version"]))

    def fetch_customer(self, customer_id: str) -> StripeCustomerSnapshot:
        return StripeCustomerSnapshot(id=customer_id, email=None, currency="USD",
                                      metadata={"organization_id": str(self.organization_id)}, raw={})

    def fetch_invoice(self, invoice_id: str) -> StripeInvoiceSnapshot:
        raw = self.state["invoices"].get(invoice_id)
        if raw is None:
            raise StripeObjectNotFoundError(f"no such invoice {invoice_id}")
        return StripeInvoiceSnapshot(
            id=invoice_id, customer_id=raw["customer"], subscription_id=raw["subscription"],
            status=raw["status"], currency="USD", total_cents=int(raw["total"]),
            subtotal_cents=int(raw["total"]), tax_cents=0, amount_paid_cents=int(raw["amount_paid"]),
            period_start=datetime.fromtimestamp(raw["period_start"], tz=timezone.utc),
            period_end=datetime.fromtimestamp(raw["period_end"], tz=timezone.utc),
            paid=raw["status"] == "paid", raw=raw)

    def fetch_invoice_total_cents(self, invoice_id: str) -> int:
        return self.fetch_invoice(invoice_id).total_cents

    def add_invoice_items(self, *, customer_id, currency, items, invoice_id=None, subscription_id=None):
        ids = []
        for item in items:
            if invoice_id and invoice_id in self.state["invoices"]:
                self.state["invoices"][invoice_id]["total"] += int(item.amount_cents)
            ids.append(f"ii_{uuid.uuid4().hex[:12]}")
        return ids


def _organization(db, slug: str) -> Organization:
    organization = db.execute(select(Organization).where(Organization.slug == slug)).scalar_one_or_none()
    if organization is None:
        raise SystemExit(f"no organization {slug!r}")
    return organization


def _account(db, organization: Organization) -> BillingAccount:
    account = db.execute(select(BillingAccount).where(BillingAccount.organization_id == organization.id)
                         ).scalar_one_or_none()
    if account is None:
        # As checkout records it: a customer at the gateway, billed to the owner's address.
        owner = db.execute(select(OrganizationMember).where(
            OrganizationMember.organization_id == organization.id,
            OrganizationMember.role == OrganizationRole.OWNER)).scalars().first()
        customer = f"cus_si_{uuid.uuid4().hex[:12]}"
        account = BillingAccount(organization_id=organization.id, gateway="STRIPE", stripe_customer_id=customer,
                                 gateway_customer_id=customer, currency="USD",
                                 billing_email=owner.user.email if owner else f"billing@{organization.slug}.test")
        db.add(account)
        db.flush()
    return account


def _subscription_id(db, account: BillingAccount, state: dict[str, Any]) -> str:
    for sub_id, raw in state["subscriptions"].items():
        if raw["customer"] == account.stripe_customer_id:
            return sub_id
    row = db.execute(select(Subscription).where(Subscription.billing_account_id == account.id)
                     .order_by(Subscription.created_at.desc()).limit(1)).scalar_one_or_none()
    if row is None or not row.stripe_subscription_id:
        raise SystemExit("this organization has no subscription; run `subscribe` first")
    # A seeded subscription: describe it as Stripe would, from FlowPilot's own record.
    state["subscriptions"][row.stripe_subscription_id] = _raw_subscription(
        row.stripe_subscription_id, account.stripe_customer_id, row.status.value.lower(), row.quota_tier_key,
        row.seats_purchased, row.current_period_start, row.current_period_end)
    return row.stripe_subscription_id


def _raw_subscription(sub_id: str, customer: str, status: str, plan: str, seats: int, start: datetime,
                      end: datetime) -> dict[str, Any]:
    return {"id": sub_id, "object": "subscription", "customer": customer, "status": status, "currency": "usd",
            "cancel_at_period_end": False, "canceled_at": None, "metadata": {"quota_tier_key": plan},
            "_version": _epoch(NOW) * 1000,
            "items": {"object": "list", "data": [{
                "id": f"si_{sub_id}", "object": "subscription_item", "quantity": int(seats),
                "current_period_start": _epoch(start), "current_period_end": _epoch(end),
                "price": {"id": f"price_e2e_test_{plan}", "currency": "usd"}}]}}


def _bump(raw: dict[str, Any]) -> None:
    raw["_version"] = int(raw["_version"]) + 1


def _apply(db, gateway: StandInGateway, kind: str, object_id: str, object_type: str) -> dict[str, Any]:
    event = StripeEvent(id=f"evt_si_{uuid.uuid4().hex[:16]}", type=kind, created=NOW, livemode=False,
                        api_version="2026-07-29.dahlia",
                        payload={"data": {"object": {"id": object_id, "object": object_type}}})
    outcome = reconcile_service.reconcile_event(db, event)
    return {"event": kind, "handled": outcome.handled, "ignored_reason": outcome.ignored_reason,
            "detail": {k: str(v) for k, v in (outcome.detail or {}).items()}}


def _plan_price_cents(db, plan: str) -> int:
    from app.services import quota_service

    tier = quota_service.published_tier_by_key(db, key=plan)
    return int((tier.unit_amount_micros or 0) // 10_000) if tier else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="The browser journeys' clock and gateway stand-ins.")
    parser.add_argument("command", choices=[
        "subscribe", "seats", "change-plan", "cancel", "renewal-fails", "payment-fails-again", "mark-unpaid",
        "card-fixed", "trial-ending", "trial-ends", "run-job"])
    parser.add_argument("--org", help="organization slug")
    parser.add_argument("--plan", default="developer")
    parser.add_argument("--seats", type=int, default=1)
    parser.add_argument("--job", help="job type for run-job")
    args = parser.parse_args(argv)

    if args.command == "run-job":
        from app.services import job_service
        from app.workers.handlers import register_all

        register_all()
        with SessionLocal() as db, db.begin():
            job = job_service.enqueue(db, job_type=args.job, payload={})
        print(json.dumps({"queued": args.job, "job_id": str(job.id)}))
        return 0

    state = _load()
    out: list[dict[str, Any]] = []
    with SessionLocal() as db:
        with db.begin():
            organization = _organization(db, args.org)

            if args.command in ("trial-ending", "trial-ends"):
                ends = NOW + timedelta(days=2) if args.command == "trial-ending" else NOW - timedelta(hours=1)
                organization.trial_ends_at = ends
                owners = db.execute(select(OrganizationMember).where(
                    OrganizationMember.organization_id == organization.id,
                    OrganizationMember.role == OrganizationRole.OWNER)).scalars()
                for member in owners:
                    member.user.trial_ends_at = ends
                print(json.dumps({"organization": args.org, "trial_ends_at": ends.isoformat()}))
                return 0

            account = _account(db, organization)
            gateway = StandInGateway(state, organization.id)
            previous = stripe_gateway.set_gateway(gateway)
            try:
                if args.command == "subscribe":
                    sub_id = f"sub_si_{uuid.uuid4().hex[:12]}"
                    state["subscriptions"][sub_id] = _raw_subscription(
                        sub_id, account.stripe_customer_id, "active", args.plan, args.seats,
                        NOW - timedelta(minutes=1), NOW + timedelta(days=30))
                    first = f"in_si_{uuid.uuid4().hex[:12]}"
                    total = _plan_price_cents(db, args.plan) * args.seats
                    state["invoices"][first] = {
                        "customer": account.stripe_customer_id, "subscription": sub_id, "status": "paid",
                        "billing_reason": "subscription_create", "total": total, "amount_paid": total,
                        "period_start": _epoch(NOW), "period_end": _epoch(NOW)}
                    out.append(_apply(db, gateway, "customer.subscription.created", sub_id, "subscription"))
                    out.append(_apply(db, gateway, "invoice.paid", first, "invoice"))
                else:
                    sub_id = _subscription_id(db, account, state)
                    raw = state["subscriptions"][sub_id]
                    item = raw["items"]["data"][0]
                    if args.command == "seats":
                        item["quantity"] = args.seats
                        _bump(raw)
                        out.append(_apply(db, gateway, "customer.subscription.updated", sub_id, "subscription"))
                    elif args.command == "change-plan":
                        raw["metadata"]["quota_tier_key"] = args.plan
                        item["price"]["id"] = f"price_e2e_test_{args.plan}"
                        _bump(raw)
                        out.append(_apply(db, gateway, "customer.subscription.updated", sub_id, "subscription"))
                    elif args.command == "cancel":
                        raw["status"], raw["canceled_at"] = "canceled", _epoch(NOW)
                        _bump(raw)
                        out.append(_apply(db, gateway, "customer.subscription.deleted", sub_id, "subscription"))
                    elif args.command == "renewal-fails":
                        # The period ends: Stripe opens the renewal (FlowPilot closes the period on it),
                        # charges the card an hour later, and the card is declined.
                        start = item["current_period_start"]
                        end = min(item["current_period_end"], _epoch(NOW))
                        start = min(start, end - 3600)
                        renewal = f"in_si_{uuid.uuid4().hex[:12]}"
                        total = _plan_price_cents(db, raw["metadata"]["quota_tier_key"]) * int(item["quantity"])
                        state["invoices"][renewal] = {
                            "customer": account.stripe_customer_id, "subscription": sub_id, "status": "draft",
                            "billing_reason": "subscription_cycle", "total": total, "amount_paid": 0,
                            "period_start": start, "period_end": end}
                        raw["_renewal"] = renewal
                        out.append(_apply(db, gateway, "invoice.created", renewal, "invoice"))
                        state["invoices"][renewal]["status"] = "open"
                        raw["status"] = "past_due"
                        _bump(raw)
                        out.append(_apply(db, gateway, "invoice.payment_failed", renewal, "invoice"))
                        out.append(_apply(db, gateway, "customer.subscription.updated", sub_id, "subscription"))
                    elif args.command == "payment-fails-again":
                        out.append(_apply(db, gateway, "invoice.payment_failed", raw["_renewal"], "invoice"))
                    elif args.command == "mark-unpaid":
                        # Stripe's retries ran out and the dashboard marks the subscription unpaid (N-072).
                        raw["status"] = "unpaid"
                        _bump(raw)
                        out.append(_apply(db, gateway, "customer.subscription.updated", sub_id, "subscription"))
                    elif args.command == "card-fixed":
                        renewal = raw.get("_renewal")
                        if renewal:
                            invoice = state["invoices"][renewal]
                            invoice["status"], invoice["amount_paid"] = "paid", invoice["total"]
                            out.append(_apply(db, gateway, "invoice.paid", renewal, "invoice"))
                        raw["status"] = "active"
                        _bump(raw)
                        out.append(_apply(db, gateway, "customer.subscription.updated", sub_id, "subscription"))
            finally:
                stripe_gateway.set_gateway(previous)
    _save(state)
    print(json.dumps({"organization": args.org, "applied": out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
