"""The browser journeys' stand-ins for the clock and the payment gateway (campaign session 4, §D).

    python scripts/e2e_stand_in.py <command> --org <slug> [options]     (prints one JSON line)

The end-to-end journeys (`frontend/e2e/tests/6*-journey-*.spec.ts`) drive the real web app, API
and worker. Two things a browser cannot do in a sandbox are played here instead:

* **the clock** — a free trial that reaches its last days or its end (`trial-ending`,
  `trial-ends`), and the hourly job that emails about it (`run-job trial.reminders`);
* **the payment gateway's side** — what the gateway does after the customer leaves Servabo (pays
  at checkout, changes seats or plan or cancels in the portal, a renewal whose card is declined,
  the retries running out, the card fixed, a refund). `--gateway` picks which (default: the
  E2E_GATEWAY environment variable, else `dodo`, the production gateway):

  - **dodo**: the stand-in Dodo (`tests/fakes/fake_dodo.py`, started by start-stack.sh on
    E2E_DODO_URL) does it, and its signed webhooks reach Servabo's real endpoint, worker
    (`--loop billing`) and reconciler, which re-fetch everything from it over HTTP. `subscribe`
    opens the checkout with Servabo's own checkout code and pays it there. Only the clock is
    played here: the grace window of a held renewal is moved (`payment-fails-again`,
    `mark-unpaid`) and the `billing.hold_notices` job's code is run for it;
  - **stripe**: each step is a Stripe event handed to Servabo's own
    `reconcile_service.reconcile_event`, the code a signed webhook reaches through the `--loop
    billing` worker, with a gateway that answers its re-fetches from Servabo's database (as the
    test suite's fake gateway does).

  Everything that follows (plan, seats, statements, dunning emails, read-only access) is
  Servabo's real behaviour;
* **the customer's DNS provider and identity provider** — `dns-publish` puts a TXT record where
  the journeys' DNS server (`scripts/e2e_dns.py`) answers Servabo's real lookup, and `saml-idp`
  / `saml-response` play the SAML provider (a signing certificate, then a signed assertion that
  answers Servabo's own AuthnRequest), with the backend's test IdP (`tests/engines/saml_idp.py`).

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


def _gateway_from_argv(argv: list[str]) -> str:
    for index, arg in enumerate(argv):
        if arg == "--gateway" and index + 1 < len(argv):
            return argv[index + 1].lower()
        if arg.startswith("--gateway="):
            return arg.split("=", 1)[1].lower()
    return (os.environ.get("E2E_GATEWAY") or "dodo").lower()


#: Where start-stack.sh serves the stand-in Dodo, and the test-mode key and webhook secret the API
#: and worker were started with. Set before the settings load, so Servabo's own code reaches it;
#: only when run as a script (a test that imports this module keeps its own environment).
DODO_URL = os.environ.get("E2E_DODO_URL", "http://127.0.0.1:8790")
if __name__ == "__main__" and _gateway_from_argv(sys.argv[1:]) == "dodo":
    from tests.fakes.fake_dodo import DEFAULT_SECRET as _DODO_SECRET

    os.environ.update(BILLING_GATEWAY="DODO", DODO_API_BASE=DODO_URL, DODO_LIVEMODE="false",
                      DODO_API_KEY=os.environ.get("E2E_DODO_API_KEY", "e2e_dodo_test_key"),
                      DODO_WEBHOOK_SECRET=_DODO_SECRET)

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


_LOCK: Any = None


def _hold_state_lock() -> None:
    """Hold the stand-in's lock until this process exits (campaign session 4).

    The browser suite runs journeys in parallel workers, each calling this script. A command reads
    the shared state, changes it and writes it back; two at once lost one's change (journey 4's
    identity provider key vanished under another journey's subscription in CI). Each command is a
    short process, so holding the lock to the end covers its whole read-change-write.
    """
    import fcntl

    global _LOCK
    STATE.parent.mkdir(parents=True, exist_ok=True)
    _LOCK = open(STATE.with_suffix(".lock"), "w")  # noqa: SIM115 - held until the process exits
    fcntl.flock(_LOCK, fcntl.LOCK_EX)


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
    """Stripe's answers to Servabo's re-fetches, kept in a small state file. No network."""

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
    # A seeded subscription: describe it as Stripe would, from Servabo's own record.
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


# ---------------------------------------------------------------------------- the stand-in Dodo


def _dodo_call(method: str, path: str, body: Optional[dict[str, Any]] = None) -> Any:
    import urllib.request

    data = json.dumps(body or {}).encode() if method != "GET" else None
    request = urllib.request.Request(f"{DODO_URL}{path}", data=data, method=method,
                                     headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - the local stand-in
        raw = response.read()
    return json.loads(raw) if raw else {}


def _dodo_catalogue(db) -> list[str]:
    """Every priced plan as a product at the stand-in Dodo, under its configured product id."""
    from app.services import quota_service

    quota_service.clear_cache()
    added = []
    for tier in quota_service.list_published_tiers(db):
        if not tier.gateway_price_id or tier.unit_amount_micros is None:
            continue
        interval = "Year" if str(tier.billing_interval or "month").lower().startswith("year") else "Month"
        _dodo_call("POST", "/_fake/product", {
            "product_id": tier.gateway_price_id, "name": tier.display_name, "interval": interval,
            "price": int(tier.unit_amount_micros) // 10_000, "currency": tier.currency or "USD"})
        added.append(tier.gateway_price_id)
    return added


def _dodo_subscription(db, organization: Organization) -> Subscription:
    row = db.execute(
        select(Subscription).join(BillingAccount, BillingAccount.id == Subscription.billing_account_id)
        .where(BillingAccount.organization_id == organization.id, Subscription.gateway == "DODO")
        .order_by(Subscription.created_at.desc()).limit(1).with_for_update(of=Subscription)
    ).scalar_one_or_none()
    if row is None:
        raise SystemExit("this organization has no Dodo subscription; run `subscribe` first")
    return row


def _settle(since: datetime) -> list[dict[str, str]]:
    """Wait until the worker has reconciled every Dodo webhook received since `since`."""
    import time

    from app.models.stripe_inbound_event import TERMINAL_STRIPE_INBOUND_STATUSES, StripeInboundEvent

    deadline = time.monotonic() + 120
    while True:
        with SessionLocal() as db:
            rows = db.execute(select(StripeInboundEvent.event_type, StripeInboundEvent.status)
                              .where(StripeInboundEvent.gateway == "DODO", StripeInboundEvent.created_at >= since)
                              .order_by(StripeInboundEvent.created_at)).all()
        waiting = [r for r in rows if r.status not in TERMINAL_STRIPE_INBOUND_STATUSES]
        if not waiting:
            seen = [{"event": r.event_type, "status": r.status.value} for r in rows]
            dead = [r for r in seen if r["status"] == "DEAD"]
            if dead:
                raise SystemExit(f"the worker gave up on {dead}")
            return seen
        if time.monotonic() > deadline:
            raise SystemExit(f"the worker did not reconcile {[r.event_type for r in waiting]} in 120 s")
        time.sleep(0.5)


def _move_hold_clock(subscription: Subscription, grace_ends_at: datetime) -> None:
    """The clock: the held renewal's grace window now ends at `grace_ends_at` (same hold)."""
    if subscription.grace_ends_at is None:
        raise SystemExit("this subscription is not on hold; run `renewal-fails` first")
    if subscription.hold_notice_grace_ends_at == subscription.grace_ends_at:
        subscription.hold_notice_grace_ends_at = grace_ends_at
    subscription.grace_ends_at = grace_ends_at


DODO_STEPS = ("subscribe", "seats", "change-plan", "cancel", "cancel-at-period-end", "renewal-fails",
              "payment-fails-again", "mark-unpaid", "card-fixed", "refund", "dodo-catalogue")


def _dodo(args: argparse.Namespace) -> int:
    """The Dodo side of a journey step: the stand-in Dodo acts, Servabo's worker reconciles."""
    from app.services.billing import hold_notice_service, portal_service
    from app.services import quota_service

    since = datetime.now(timezone.utc) - timedelta(seconds=1)
    out: dict[str, Any] = {"organization": args.org, "gateway": "dodo"}
    with SessionLocal() as db:
        with db.begin():
            if args.command == "dodo-catalogue":
                print(json.dumps({"products": _dodo_catalogue(db)}))
                return 0
            organization = _organization(db, args.org)
            if args.command == "subscribe":
                # The owner picks the plan in Servabo (its own checkout code opens Dodo's checkout)
                # and pays on Dodo's page.
                _dodo_catalogue(db)
                session = portal_service.create_checkout_session(
                    db, organization_id=organization.id, quota_tier_key=args.plan, seats=args.seats)
                checkout_id = session.url.rstrip("/").rsplit("/", 1)[-1]
            else:
                subscription = _dodo_subscription(db, organization)
                sub_id = subscription.gateway_subscription_id
                out["subscription_id"] = sub_id
                if args.command in ("payment-fails-again", "mark-unpaid"):
                    # Dodo keeps retrying the card; time passes in the grace window (the clock), and
                    # the hourly billing.hold_notices job says what the owner must hear by then.
                    already = (subscription.hold_notice_step
                               if subscription.hold_notice_grace_ends_at == subscription.grace_ends_at else None)
                    if args.command == "mark-unpaid":
                        ends = NOW - timedelta(minutes=1)
                    else:
                        ends = NOW + (timedelta(days=6) if already is None else timedelta(days=1))
                    _move_hold_clock(subscription, ends)
                    db.flush([subscription])
                    sent = hold_notice_service.send_due(db)
                    out["notices"] = [s["step"] for s in sent]
                    print(json.dumps(out, default=str))
                    return 0
        if args.command == "subscribe":
            paid = _dodo_call("POST", "/_fake/pay", {"checkout_id": checkout_id})
            out["subscription_id"] = paid["subscription_id"]
        elif args.command == "seats" or args.command == "change-plan":
            current = _dodo_call("GET", f"/subscriptions/{sub_id}")
            product = current["product_id"]
            if args.command == "change-plan":
                quota_service.clear_cache()
                product = quota_service.published_tier_by_key(db, key=args.plan).gateway_price_id
            _dodo_call("POST", f"/subscriptions/{sub_id}/change-plan", {
                "product_id": product, "proration_billing_mode": "prorated_immediately",
                "quantity": args.seats if args.command == "seats" else current["quantity"]})
        elif args.command in ("cancel", "cancel-at-period-end"):
            _dodo_call("POST", "/_fake/cancel", {"subscription_id": sub_id,
                                                  "at_period_end": args.command == "cancel-at-period-end"})
        elif args.command == "renewal-fails":
            _dodo_call("POST", "/_fake/renew", {"subscription_id": sub_id, "succeed": False})
        elif args.command == "card-fixed":
            _dodo_call("POST", "/_fake/fix-card", {"subscription_id": sub_id})
        elif args.command == "refund":
            payments = [p for p in _dodo_call("GET", "/_fake/state")["payments"]
                        if p["subscription_id"] == sub_id and p["status"] == "succeeded"]
            if not payments:
                raise SystemExit("this subscription has no payment to refund")
            refund = _dodo_call("POST", "/_fake/refund", {"payment_id": payments[-1]["payment_id"],
                                                         "amount": args.amount})
            out["refund"] = {"refund_id": refund["refund_id"], "amount": refund["amount"]}
        else:
            raise SystemExit(f"{args.command} is not a Dodo step")
    out["applied"] = _settle(since)
    print(json.dumps(out, default=str))
    return 0


def _identity_provider(args: argparse.Namespace) -> int:
    """The customer's SAML identity provider (Okta, Entra ID), stood in by the backend's test IdP.

    `saml-idp` makes the provider's signing key and certificate (the certificate is what the owner
    pastes into the identity console); `saml-response` answers Servabo's AuthnRequest with a signed
    assertion for one person, addressed to Servabo's own ACS URL, as the provider would after they
    signed in there.
    """
    from tests.engines.saml_idp import TestIdp, keypair

    state = _load()
    providers = state.setdefault("identity_providers", {})
    domain = (args.domain or "").lower()
    entity_id, sso_url = f"http://idp.{domain}/metadata", f"http://idp.{domain}/sso"
    if args.command == "saml-idp":
        key, cert = keypair(f"idp.{domain}")
        providers[domain] = {"key": key, "cert": cert}
        _save(state)
        print(json.dumps({"entity_id": entity_id, "sso_url": sso_url, "certificate": cert}))
        return 0
    import base64
    import zlib

    from lxml import etree

    from app.api.v1.saml import _sp_urls

    held = providers[domain]
    request_id = etree.fromstring(zlib.decompress(base64.b64decode(args.saml_request), -15)).get("ID")
    sp_entity_id, acs_url, _ = _sp_urls()
    idp = TestIdp(domain=domain, entity_id=entity_id, sso_url=sso_url, key=held["key"], cert=held["cert"])
    answer = idp.response(email=args.email, acs_url=acs_url, sp_entity_id=sp_entity_id, in_response_to=request_id,
                          attributes={"displayName": [args.email.split("@")[0].replace(".", " ").title()]})
    print(json.dumps({"acs_url": acs_url, "saml_response": answer}))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="The browser journeys' clock and gateway stand-ins.")
    parser.add_argument("command", choices=[
        "subscribe", "seats", "change-plan", "cancel", "cancel-at-period-end", "renewal-fails", "payment-fails-again", "mark-unpaid",
        "card-fixed", "refund", "dodo-catalogue", "trial-ending", "trial-ends", "run-job", "dns-publish", "saml-idp",
        "saml-response"])
    parser.add_argument("--gateway", choices=["dodo", "stripe"], type=str.lower,
                        default=_gateway_from_argv([]), help="whose side to play (default: E2E_GATEWAY, else dodo)")
    parser.add_argument("--amount", type=int, help="refund: in cents (default: the whole payment)")
    parser.add_argument("--org", help="organization slug")
    parser.add_argument("--plan", default="developer")
    parser.add_argument("--seats", type=int, default=1)
    parser.add_argument("--job", help="job type for run-job")
    parser.add_argument("--name", help="dns-publish: the DNS name")
    parser.add_argument("--value", help="dns-publish: the TXT value")
    parser.add_argument("--domain", help="saml-idp, saml-response: the customer's email domain")
    parser.add_argument("--email", help="saml-response: who signs in")
    parser.add_argument("--saml-request", help="saml-response: the SAMLRequest Servabo sent (redirect binding)")
    args = parser.parse_args(argv)

    # run-job waits on the worker and touches no shared state; neither do the Dodo steps (the
    # stand-in Dodo keeps its own state, behind its own lock).
    if args.command != "run-job" and not (args.gateway == "dodo" and args.command in DODO_STEPS):
        _hold_state_lock()

    if args.command == "dns-publish":
        # The customer publishes the TXT record at their DNS provider; the journeys' DNS server
        # (scripts/e2e_dns.py, started by start-stack.sh) answers it to Servabo's resolver.
        from scripts import e2e_dns

        records = os.environ.get("E2E_DNS_RECORDS") or str(
            Path(os.environ.get("E2E_LOG_DIR", "/tmp/servabo-e2e-logs")) / "dns-records.json")
        e2e_dns.publish(Path(records), args.name, args.value)
        print(json.dumps({"published": args.name, "value": args.value}))
        return 0

    if args.command in ("saml-idp", "saml-response"):
        return _identity_provider(args)

    if args.command == "run-job":
        # Queue a job for the running worker, as its schedule would. With --org, the job is about
        # that organization (its first workspace in the payload) and the command waits for the
        # worker's outcome: the journeys use it to show what an archived tenant does not run.
        import time

        from app.models.job import Job
        from app.models.workspace import Workspace
        from app.services import job_service
        from app.workers.handlers import register_all

        register_all()
        payload: dict[str, Any] = {}
        organization_id = None
        if args.org:
            with SessionLocal() as db:
                organization = _organization(db, args.org)
                organization_id = organization.id
                workspace = db.execute(select(Workspace).where(Workspace.organization_id == organization.id)
                                       .order_by(Workspace.created_at).limit(1)).scalar_one()
                payload = {"workspace_id": str(workspace.id)}
        with SessionLocal() as db, db.begin():
            job_id = job_service.enqueue(db, job_type=args.job, payload=payload, organization_id=organization_id).id
        outcome: dict[str, Any] = {"queued": args.job, "job_id": str(job_id)}
        if args.org:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                with SessionLocal() as db:
                    job = db.get(Job, job_id)
                    status = getattr(job.status, "value", job.status)
                    if status in ("SUCCEEDED", "DEAD"):
                        outcome.update(status=status, result=job.result)
                        break
                time.sleep(1)
        print(json.dumps(outcome, default=str))
        return 0

    if args.gateway == "dodo" and args.command not in ("trial-ending", "trial-ends"):
        return _dodo(args)
    if args.command in ("refund", "dodo-catalogue"):
        raise SystemExit(f"{args.command} is played on the Dodo stand-in only (--gateway dodo)")

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
                    elif args.command == "cancel-at-period-end":
                        # The owner cancels in the portal: the plan runs to the end of the period.
                        raw["cancel_at_period_end"] = True
                        raw["cancel_at"] = item["current_period_end"]  # Stripe dates it
                        _bump(raw)
                        out.append(_apply(db, gateway, "customer.subscription.updated", sub_id, "subscription"))
                    elif args.command == "cancel":
                        raw["status"], raw["canceled_at"] = "canceled", _epoch(NOW)
                        _bump(raw)
                        out.append(_apply(db, gateway, "customer.subscription.deleted", sub_id, "subscription"))
                    elif args.command == "renewal-fails":
                        # The period ends: Stripe opens the renewal (Servabo closes the period on it),
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
