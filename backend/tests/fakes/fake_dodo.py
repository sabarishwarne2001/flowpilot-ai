"""A stand-in for Dodo Payments, over HTTP (website and payments session, C1).

    fake = FakeDodo(webhook_secret=SECRET)        # in a test
    with fake.serving() as base_url:              # a real HTTP server on 127.0.0.1
        settings.DODO_API_BASE = base_url         # the real adapter talks to it
        ...
        fake.deliver(client)                      # its signed webhooks, to the real endpoint

    python -m tests.fakes.fake_dodo --port 8790 --webhook-url http://127.0.0.1:8000/api/v1/billing/webhooks/dodo
                                                   # the browser journeys' Dodo (start-stack.sh)

It answers every endpoint `app/services/billing/dodo_gateway.py` calls, with the fields that
module reads (docs/billing/DODO.md lists them and where each comes from):

    POST  /checkouts                                    a checkout session (checkout_url, session_id)
    GET   /subscriptions/{id}                           the subscription
    POST  /subscriptions/{id}/change-plan               change product or quantity
    POST  /subscriptions/{id}/change-plan/preview       what that change would charge now
    PATCH /subscriptions/{id}                           cancel at the next billing date, or now
    POST  /customers/{id}/customer-portal/session       the customer portal (link)
    GET   /customers/{id}   PATCH /customers/{id}       the customer, and changing its email or name
    GET   /payments/{id}                                a payment, with its refunds and disputes
    GET   /invoices/payments/{id}                       the payment's tax invoice (a PDF)
    GET   /products         POST /products   GET /products/{id}       for scripts/dodo_setup.py

and plays the customer's side as Dodo would: a checkout page with a Pay button (`GET /checkout/{id}`),
renewals that succeed or fail, the card fixed in the portal (`GET /portal/{customer}`: update the card,
cancel), cancellations, refunds and disputes.
Each one changes the stand-in's state and sends the events Dodo sends, signed as Standard Webhooks
(`webhook-id`, `webhook-timestamp`, `webhook-signature: v1,<base64 HMAC-SHA256>`), either to a
webhook URL or into `outbox` for a test to deliver through its own client. The control operations
are also served as `POST /_fake/<operation>` with a JSON body, for scripts/e2e_stand_in.py.

Money is in the currency's smallest unit, as Dodo's API states it (cents for USD). Nothing here ever
reaches Dodo: it is a test double, used by the tests and by the browser journeys only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterator, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

#: A Standard Webhooks secret: `whsec_` and base64 of the key bytes.
DEFAULT_SECRET = "whsec_" + base64.b64encode(b"servabo-fake-dodo-signing-key-0001").decode()

PRORATION_MODES = ("prorated_immediately", "full_immediately", "difference_immediately", "do_not_bill")


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def sign(secret: str, message_id: str, timestamp: int, body: bytes) -> str:
    """The `webhook-signature` header Dodo sends: `v1,<base64 HMAC-SHA256 of id.timestamp.body>`."""
    raw = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
    key = base64.b64decode(raw)
    digest = hmac.new(key, b".".join([message_id.encode(), str(timestamp).encode(), body]), hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


@dataclass
class Delivery:
    """One signed webhook, as Dodo sends it."""

    type: str
    body: bytes
    headers: dict[str, str]

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.body)


@dataclass
class FakeDodo:
    webhook_secret: str = DEFAULT_SECRET
    webhook_url: Optional[str] = None
    business_id: str = "bus_servabo_fake"
    #: Tax Dodo adds at checkout, as a fraction of the pre-tax price (0.18: India's GST).
    tax_rate: float = 0.0
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    #: Where the checkout page is served from (set by `serving()`).
    base_url: str = "http://127.0.0.1"

    products: dict[str, dict[str, Any]] = field(default_factory=dict)
    customers: dict[str, dict[str, Any]] = field(default_factory=dict)
    subscriptions: dict[str, dict[str, Any]] = field(default_factory=dict)
    payments: dict[str, dict[str, Any]] = field(default_factory=dict)
    checkouts: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Every API call received: (method, path, JSON body or None).
    calls: list[tuple[str, str, Optional[dict[str, Any]]]] = field(default_factory=list)
    #: Signed webhooks not yet delivered (no `webhook_url`), oldest first.
    outbox: list[Delivery] = field(default_factory=list)
    #: Paths that answer with this status instead (a test makes Dodo fail).
    failures: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ catalogue

    def add_product(self, *, name: str, price: int, currency: str = "USD", interval: str = "Month",
                    product_id: Optional[str] = None, tax_category: str = "saas") -> str:
        """A subscription product: `price` per unit per `interval` (Month or Year), in minor units."""
        pid = product_id or _id("pdt")
        with self._lock:
            self.products[pid] = {
                "product_id": pid, "business_id": self.business_id, "name": name, "tax_category": tax_category,
                "is_recurring": True, "created_at": _iso(self.clock()),
                "price": {"type": "recurring_price", "price": int(price), "currency": currency,
                          "payment_frequency_count": 1, "payment_frequency_interval": interval,
                          "subscription_period_count": 10, "subscription_period_interval": "Year",
                          "tax_inclusive": False, "discount": 0, "purchasing_power_parity": False},
                "metadata": {},
            }
        return pid

    def _price(self, product_id: str) -> tuple[int, str, str]:
        product = self.products.get(product_id)
        if product is None:
            raise LookupError(product_id)
        price = product["price"]
        return int(price["price"]), price["currency"], price["payment_frequency_interval"]

    # ------------------------------------------------------------------ objects

    def _customer(self, *, email: str, name: str = "") -> dict[str, Any]:
        for customer in self.customers.values():
            if customer["email"] == email:
                return customer
        cid = _id("cus")
        customer = {"customer_id": cid, "business_id": self.business_id, "email": email, "name": name or email,
                    "phone_number": None, "created_at": _iso(self.clock()), "metadata": {}}
        self.customers[cid] = customer
        return customer

    def _period(self, start: datetime, interval: str) -> datetime:
        return start + (timedelta(days=365) if interval == "Year" else timedelta(days=30))

    def _subscription_view(self, sub: dict[str, Any]) -> dict[str, Any]:
        customer = self.customers[sub["customer_id"]]
        view = {k: v for k, v in sub.items() if not k.startswith("_")}
        view["customer"] = {"customer_id": customer["customer_id"], "email": customer["email"],
                            "name": customer["name"]}
        return view

    def _payment_view(self, payment: dict[str, Any]) -> dict[str, Any]:
        customer = self.customers[payment["customer_id"]]
        view = {k: v for k, v in payment.items() if not k.startswith("_")}
        view["customer"] = {"customer_id": customer["customer_id"], "email": customer["email"],
                            "name": customer["name"]}
        return view

    def _charge(self, sub: dict[str, Any], *, pre_tax: int, succeed: bool = True, reason: str = "renewal") -> dict[str, Any]:
        tax = int(round(pre_tax * self.tax_rate))
        pid = _id("pay")
        payment = {
            "payment_id": pid, "business_id": self.business_id, "subscription_id": sub["subscription_id"],
            "customer_id": sub["customer_id"], "total_amount": pre_tax + tax, "tax": tax,
            "currency": sub["currency"], "status": "succeeded" if succeed else "failed",
            "created_at": _iso(self.clock()), "payment_method": "card", "card_last_four": "4242",
            "refunds": [], "disputes": [], "metadata": dict(sub["metadata"]),
            "product_cart": [{"product_id": sub["product_id"], "quantity": sub["quantity"]}],
            "_reason": reason,
        }
        self.payments[pid] = payment
        return payment

    # ------------------------------------------------------------------ webhooks

    def _emit(self, event_type: str, data: dict[str, Any], payload_type: str) -> Delivery:
        body = json.dumps({"business_id": self.business_id, "type": event_type,
                           "timestamp": _iso(self.clock()),
                           "data": {"payload_type": payload_type, **data}}, separators=(",", ":")).encode()
        message_id = _id("msg")
        timestamp = int(time.time())
        delivery = Delivery(type=event_type, body=body, headers={
            "webhook-id": message_id, "webhook-timestamp": str(timestamp),
            "webhook-signature": sign(self.webhook_secret, message_id, timestamp, body),
            "content-type": "application/json"})
        if self.webhook_url:
            _post(self.webhook_url, delivery)
        else:
            self.outbox.append(delivery)
        return delivery

    def _emit_subscription(self, event_type: str, sub: dict[str, Any]) -> Delivery:
        return self._emit(event_type, self._subscription_view(sub), "Subscription")

    def _emit_payment(self, payment: dict[str, Any]) -> Delivery:
        kind = "payment.succeeded" if payment["status"] == "succeeded" else "payment.failed"
        return self._emit(kind, self._payment_view(payment), "Payment")

    def deliver(self, client: Any, path: str = "/api/v1/billing/webhooks/dodo") -> list[Any]:
        """Post every queued webhook through `client` (a TestClient), oldest first."""
        responses = []
        while self.outbox:
            delivery = self.outbox.pop(0)
            responses.append(client.post(path, content=delivery.body, headers=delivery.headers))
        return responses

    # ------------------------------------------------------------------ the customer's side

    def checkout(self, *, product_id: str, quantity: int = 1, email: str = "owner@example.com",
                 metadata: Optional[dict[str, str]] = None, return_url: str = "https://app.example/return",
                 customer_id: Optional[str] = None) -> str:
        """What POST /checkouts does: a session the customer then pays (`pay`)."""
        with self._lock:
            self._price(product_id)
            cid = _id("cks")
            self.checkouts[cid] = {"session_id": cid, "product_id": product_id, "quantity": int(quantity),
                                   "email": email, "customer_id": customer_id, "metadata": dict(metadata or {}),
                                   "return_url": return_url, "status": "open", "subscription_id": None}
            return cid

    def pay(self, checkout_id: str, *, succeed: bool = True) -> dict[str, Any]:
        """The customer pays at checkout: Dodo creates the customer, the subscription and the payment,
        and sends `subscription.active` and `payment.succeeded` (or `payment.failed`)."""
        with self._lock:
            session = self.checkouts[checkout_id]
            if session["subscription_id"]:
                return self.subscriptions[session["subscription_id"]]
            customer = (self.customers[session["customer_id"]] if session["customer_id"]
                        else self._customer(email=session["email"]))
            price, currency, interval = self._price(session["product_id"])
            now = self.clock()
            sid = _id("sub")
            sub = {
                "subscription_id": sid, "business_id": self.business_id, "customer_id": customer["customer_id"],
                "product_id": session["product_id"], "quantity": session["quantity"],
                "status": "active" if succeed else "failed", "currency": currency,
                "recurring_pre_tax_amount": price * session["quantity"],
                "payment_frequency_interval": interval, "payment_frequency_count": 1,
                "created_at": _iso(now), "previous_billing_date": _iso(now),
                "next_billing_date": _iso(self._period(now, interval)),
                "cancel_at_next_billing_date": False, "cancelled_at": None,
                "metadata": dict(session["metadata"]), "addons": [], "on_demand": False,
            }
            self.subscriptions[sid] = sub
            session.update(status="paid" if succeed else "failed", subscription_id=sid)
            payment = self._charge(sub, pre_tax=price * session["quantity"], succeed=succeed, reason="checkout")
            if succeed:
                self._emit_subscription("subscription.active", sub)
            else:
                self._emit_subscription("subscription.failed", sub)
            self._emit_payment(payment)
            return sub

    def renew(self, subscription_id: str, *, succeed: bool = True) -> dict[str, Any]:
        """The period ends and Dodo charges the renewal. A declined card puts it `on_hold`."""
        with self._lock:
            sub = self.subscriptions[subscription_id]
            now = self.clock()
            if sub["cancel_at_next_billing_date"]:
                sub.update(status="cancelled", cancelled_at=_iso(now))
                self._emit_subscription("subscription.cancelled", sub)
                return sub
            payment = self._charge(sub, pre_tax=sub["recurring_pre_tax_amount"], succeed=succeed)
            if succeed:
                sub.update(status="active", previous_billing_date=_iso(now),
                           next_billing_date=_iso(self._period(now, sub["payment_frequency_interval"])))
                self._emit_subscription("subscription.renewed", sub)
            else:
                sub["status"] = "on_hold"
                sub["_failed_payment"] = payment["payment_id"]
                self._emit_subscription("subscription.on_hold", sub)
            self._emit_payment(payment)
            return sub

    def fix_card(self, subscription_id: str) -> dict[str, Any]:
        """The customer updates the card in the portal; Dodo charges the held renewal."""
        with self._lock:
            sub = self.subscriptions[subscription_id]
            now = self.clock()
            payment = self._charge(sub, pre_tax=sub["recurring_pre_tax_amount"], reason="recovered")
            sub.update(status="active", previous_billing_date=_iso(now),
                       next_billing_date=_iso(self._period(now, sub["payment_frequency_interval"])))
            sub.pop("_failed_payment", None)
            self._emit_subscription("subscription.active", sub)
            self._emit_payment(payment)
            return sub

    def cancel(self, subscription_id: str, *, at_period_end: bool = True) -> dict[str, Any]:
        with self._lock:
            sub = self.subscriptions[subscription_id]
            if at_period_end:
                sub["cancel_at_next_billing_date"] = True
                self._emit_subscription("subscription.updated", sub)
            else:
                sub.update(status="cancelled", cancelled_at=_iso(self.clock()))
                self._emit_subscription("subscription.cancelled", sub)
            return sub

    def expire(self, subscription_id: str) -> dict[str, Any]:
        """Payment retries ran out: Dodo ends the subscription."""
        with self._lock:
            sub = self.subscriptions[subscription_id]
            sub.update(status="expired", cancelled_at=_iso(self.clock()))
            self._emit_subscription("subscription.expired", sub)
            return sub

    def refund(self, payment_id: str, *, amount: Optional[int] = None, succeed: bool = True) -> dict[str, Any]:
        """Dodo refunds a payment (the whole of it unless `amount` says otherwise)."""
        with self._lock:
            payment = self.payments[payment_id]
            already = sum(r["amount"] for r in payment["refunds"] if r["status"] == "succeeded")
            value = int(amount if amount is not None else payment["total_amount"] - already)
            refund = {"refund_id": _id("ref"), "payment_id": payment_id, "business_id": self.business_id,
                      "amount": value, "currency": payment["currency"], "status": "succeeded" if succeed else "failed",
                      "is_partial": value < payment["total_amount"], "reason": "requested_by_customer",
                      "created_at": _iso(self.clock())}
            payment["refunds"].append(refund)
            self._emit("refund.succeeded" if succeed else "refund.failed", refund, "Refund")
            return refund

    def dispute(self, payment_id: str, *, status: str = "dispute_opened", dispute_id: Optional[str] = None) -> dict[str, Any]:
        """A chargeback. `status` is Dodo's `dispute_status` (dispute_opened, dispute_challenged,
        dispute_won, dispute_lost, dispute_accepted, dispute_cancelled, dispute_expired)."""
        with self._lock:
            payment = self.payments[payment_id]
            found = next((d for d in payment["disputes"] if d["dispute_id"] == dispute_id), None) if dispute_id else None
            if found is None:
                found = {"dispute_id": dispute_id or _id("dsp"), "payment_id": payment_id,
                         "business_id": self.business_id, "amount": str(payment["total_amount"]),
                         "currency": payment["currency"], "dispute_stage": "chargeback",
                         "created_at": _iso(self.clock())}
                payment["disputes"].append(found)
            found["dispute_status"] = status
            event = {"dispute_opened": "dispute.opened", "dispute_challenged": "dispute.challenged",
                     "dispute_won": "dispute.won", "dispute_lost": "dispute.lost",
                     "dispute_accepted": "dispute.accepted", "dispute_cancelled": "dispute.cancelled",
                     "dispute_expired": "dispute.expired"}[status]
            self._emit(event, found, "Dispute")
            return found

    # ------------------------------------------------------------------ the API

    def handle(self, method: str, target: str, body: Optional[dict[str, Any]]) -> tuple[int, str, bytes]:
        """Answer one API request: (status, content type, body)."""
        parts = urlsplit(target)
        path = parts.path.rstrip("/") or "/"
        with self._lock:
            self.calls.append((method, path, body))
            for prefix, status in self.failures.items():
                if path.startswith(prefix):
                    return _json(status, {"code": "FAKE_FAILURE", "message": f"fake failure for {prefix}"})
            try:
                return self._route(method, path, parse_qs(parts.query), body or {})
            except LookupError as exc:
                return _json(404, {"code": "NOT_FOUND", "message": f"no such object {exc}"})
            except ValueError as exc:
                return _json(422, {"code": "INVALID_REQUEST", "message": str(exc)})

    def _route(self, method: str, path: str, query: dict[str, list[str]], body: dict[str, Any]) -> tuple[int, str, bytes]:
        seg = path.strip("/").split("/")
        if method == "POST" and path == "/checkouts":
            cart = body.get("product_cart") or []
            if not cart:
                raise ValueError("product_cart is required")
            customer = body.get("customer") or {}
            cid = self.checkout(product_id=cart[0]["product_id"], quantity=int(cart[0].get("quantity") or 1),
                                email=customer.get("email") or "owner@example.com",
                                customer_id=customer.get("customer_id"), metadata=body.get("metadata") or {},
                                return_url=body.get("return_url") or "")
            return _json(200, {"session_id": cid, "checkout_url": f"{self.base_url}/checkout/{cid}"})
        if seg[0] == "checkout" and len(seg) == 2 and method == "GET":
            return 200, "text/html; charset=utf-8", _checkout_page(self.checkouts[seg[1]], self.products).encode()
        if seg[0] == "checkout" and len(seg) == 3 and seg[2] == "pay" and method == "POST":
            session = self.checkouts[seg[1]]
            succeed = (query.get("outcome") or ["paid"])[0] != "declined"
            sub = self.pay(seg[1], succeed=succeed)
            status = "active" if succeed else "failed"
            target = session["return_url"]
            joiner = "&" if "?" in target else "?"
            location = f"{target}{joiner}{urlencode({'subscription_id': sub['subscription_id'], 'status': status})}"
            return 303, location, b""
        if seg[0] == "portal" and len(seg) == 2 and method == "GET":
            customer = self.customers.get(seg[1])
            if customer is None:
                raise LookupError(seg[1])
            mine = [s for s in self.subscriptions.values() if s["customer_id"] == customer["customer_id"]]
            return 200, "text/html; charset=utf-8", _portal_page(customer, mine, (query.get("done") or [""])[0]).encode()
        if seg[0] == "portal" and len(seg) == 3 and method == "POST" and seg[2] in ("card", "cancel"):
            customer = self.customers.get(seg[1])
            if customer is None:
                raise LookupError(seg[1])
            for sub in [s for s in self.subscriptions.values() if s["customer_id"] == customer["customer_id"]]:
                if seg[2] == "card" and sub["status"] == "on_hold":
                    self.fix_card(sub["subscription_id"])
                elif seg[2] == "cancel" and sub["status"] == "active" and not sub["cancel_at_next_billing_date"]:
                    self.cancel(sub["subscription_id"], at_period_end=True)
            return 303, f"/portal/{customer['customer_id']}?done={seg[2]}", b""
        if seg[0] == "subscriptions" and len(seg) >= 2:
            sub = self.subscriptions.get(seg[1])
            if sub is None:
                raise LookupError(seg[1])
            if len(seg) == 2 and method == "GET":
                return _json(200, self._subscription_view(sub))
            if len(seg) == 2 and method == "PATCH":
                if body.get("cancel_at_next_billing_date") is not None:
                    sub["cancel_at_next_billing_date"] = bool(body["cancel_at_next_billing_date"])
                if body.get("status") == "cancelled":
                    sub.update(status="cancelled", cancelled_at=_iso(self.clock()))
                self._emit_subscription("subscription.updated", sub)
                return _json(200, self._subscription_view(sub))
            if seg[2:] == ["change-plan", "preview"] and method == "POST":
                return _json(200, self._preview(sub, body))
            if seg[2:] == ["change-plan"] and method == "POST":
                self._change_plan(sub, body)
                return 200, "application/json", b""
        if seg[0] == "customers" and len(seg) >= 2:
            customer = self.customers.get(seg[1])
            if customer is None:
                raise LookupError(seg[1])
            if seg[2:] == ["customer-portal", "session"] and method == "POST":
                return _json(200, {"link": f"{self.base_url}/portal/{customer['customer_id']}"})
            if len(seg) == 2 and method == "GET":
                return _json(200, customer)
            if len(seg) == 2 and method == "PATCH":
                for key in ("email", "name", "phone_number", "metadata"):
                    if key in body:
                        customer[key] = body[key]
                return _json(200, customer)
        if seg[0] == "payments" and len(seg) == 2 and method == "GET":
            payment = self.payments.get(seg[1])
            if payment is None:
                raise LookupError(seg[1])
            return _json(200, self._payment_view(payment))
        if seg[:2] == ["invoices", "payments"] and len(seg) == 3 and method == "GET":
            payment = self.payments.get(seg[2])
            if payment is None:
                raise LookupError(seg[2])
            return 200, "application/pdf", _pdf(f"Dodo Payments tax invoice for payment {payment['payment_id']}")
        if seg[0] == "products":
            if len(seg) == 1 and method == "GET":
                return _json(200, {"items": list(self.products.values())})
            if len(seg) == 1 and method == "POST":
                price = body.get("price") or {}
                pid = self.add_product(name=str(body.get("name") or "Product"), price=int(price.get("price") or 0),
                                       currency=str(price.get("currency") or "USD"),
                                       interval=str(price.get("payment_frequency_interval") or "Month"),
                                       tax_category=str(body.get("tax_category") or "saas"))
                return _json(200, self.products[pid])
            if len(seg) == 2 and method == "GET":
                if seg[1] not in self.products:
                    raise LookupError(seg[1])
                return _json(200, self.products[seg[1]])
        if seg[0] == "_fake" and len(seg) == 2 and method == "POST":
            return _json(200, self._control(seg[1], body))
        if seg[0] == "_fake" and seg[1:] == ["state"] and method == "GET":
            return _json(200, {"subscriptions": [self._subscription_view(s) for s in self.subscriptions.values()],
                               "payments": [self._payment_view(p) for p in self.payments.values()]})
        return _json(404, {"code": "NOT_FOUND", "message": f"{method} {path}"})

    def _remaining_fraction(self, sub: dict[str, Any]) -> float:
        start = datetime.fromisoformat(sub["previous_billing_date"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(sub["next_billing_date"].replace("Z", "+00:00"))
        total = (end - start).total_seconds()
        left = (end - self.clock()).total_seconds()
        return max(0.0, min(1.0, left / total)) if total > 0 else 0.0

    def _preview_amount(self, sub: dict[str, Any], product_id: str, quantity: int, mode: str) -> int:
        new_price, _, _ = self._price(product_id)
        new_total, old_total = new_price * quantity, sub["recurring_pre_tax_amount"]
        if mode == "do_not_bill":
            return 0
        if mode == "full_immediately":
            return new_total
        if mode == "difference_immediately":
            return max(0, new_total - old_total)
        return max(0, int(round((new_total - old_total) * self._remaining_fraction(sub))))

    def _preview(self, sub: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        product_id, quantity, mode = self._change_args(body)
        pre_tax = self._preview_amount(sub, product_id, quantity, mode)
        tax = int(round(pre_tax * self.tax_rate))
        new_plan = dict(self._subscription_view(sub), product_id=product_id, quantity=quantity)
        return {"immediate_charge": {"line_items": [], "summary": {
            "currency": sub["currency"], "customer_credits": 0, "settlement_amount": pre_tax + tax,
            "settlement_currency": sub["currency"], "total_amount": pre_tax + tax, "tax": tax}},
            "new_plan": new_plan}

    def _change_args(self, body: dict[str, Any]) -> tuple[str, int, str]:
        product_id = str(body.get("product_id") or "")
        if not product_id:
            raise ValueError("product_id is required")
        self._price(product_id)
        mode = str(body.get("proration_billing_mode") or "")
        if mode not in PRORATION_MODES:
            raise ValueError(f"proration_billing_mode must be one of {PRORATION_MODES}")
        quantity = int(body.get("quantity") or 1)
        if quantity < 1:
            raise ValueError("quantity must be at least 1")
        return product_id, quantity, mode

    def _change_plan(self, sub: dict[str, Any], body: dict[str, Any]) -> None:
        product_id, quantity, mode = self._change_args(body)
        pre_tax = self._preview_amount(sub, product_id, quantity, mode)
        new_price, _, interval = self._price(product_id)
        sub.update(product_id=product_id, quantity=quantity, recurring_pre_tax_amount=new_price * quantity,
                   payment_frequency_interval=interval)
        if mode in ("full_immediately",):
            now = self.clock()
            sub.update(previous_billing_date=_iso(now), next_billing_date=_iso(self._period(now, interval)))
        self._emit_subscription("subscription.plan_changed", sub)
        if pre_tax > 0:
            self._emit_payment(self._charge(sub, pre_tax=pre_tax, reason="plan_change"))

    def _control(self, operation: str, body: dict[str, Any]) -> dict[str, Any]:
        """The customer's and Dodo's actions, for a script in another process."""
        if operation == "checkout":
            # As POST /checkouts, for a script that has no checkout of Servabo's to pay.
            return {"session_id": self.checkout(product_id=body["product_id"], quantity=int(body.get("quantity", 1)),
                                                email=body.get("email", "owner@example.com"),
                                                metadata=body.get("metadata") or {})}
        if operation == "pay":
            return self._subscription_view(self.pay(body["checkout_id"], succeed=body.get("succeed", True)))
        if operation == "renew":
            return self._subscription_view(self.renew(body["subscription_id"], succeed=body.get("succeed", True)))
        if operation == "fix-card":
            return self._subscription_view(self.fix_card(body["subscription_id"]))
        if operation == "cancel":
            return self._subscription_view(self.cancel(body["subscription_id"],
                                                       at_period_end=body.get("at_period_end", True)))
        if operation == "expire":
            return self._subscription_view(self.expire(body["subscription_id"]))
        if operation == "refund":
            return self.refund(body["payment_id"], amount=body.get("amount"))
        if operation == "dispute":
            return self.dispute(body["payment_id"], status=body.get("status", "dispute_opened"),
                                dispute_id=body.get("dispute_id"))
        if operation == "product":
            pid = self.add_product(name=body["name"], price=int(body["price"]), currency=body.get("currency", "USD"),
                                   interval=body.get("interval", "Month"), product_id=body.get("product_id"))
            return self.products[pid]
        raise ValueError(f"unknown operation {operation!r}")

    # ------------------------------------------------------------------ serving

    @contextmanager
    def serving(self, host: str = "127.0.0.1", port: int = 0) -> Iterator[str]:
        """Serve the API on a real port for the duration; yields its base URL."""
        server = self.start(host, port)
        try:
            yield self.base_url
        finally:
            server.shutdown()
            server.server_close()

    def start(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, method: str) -> None:
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                body: Optional[dict[str, Any]] = None
                if raw:
                    if "application/x-www-form-urlencoded" in (self.headers.get("content-type") or ""):
                        body = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                    else:
                        try:
                            body = json.loads(raw)
                        except ValueError:
                            body = None
                status, kind, data = fake.handle(method, self.path, body)
                self.send_response(status)
                if status in (301, 302, 303):
                    self.send_header("Location", kind)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                self._answer("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._answer("POST")

            def do_PATCH(self) -> None:  # noqa: N802
                self._answer("PATCH")

            def log_message(self, *args: Any) -> None:  # quiet
                return

        server = ThreadingHTTPServer((host, port), Handler)
        self.base_url = f"http://{host}:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True, name="fake-dodo").start()
        return server


def _json(status: int, value: Any) -> tuple[int, str, bytes]:
    return status, "application/json", json.dumps(value).encode()


def _post(url: str, delivery: Delivery) -> None:
    import urllib.request

    request = urllib.request.Request(url, data=delivery.body, headers=delivery.headers, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - a local test endpoint
        response.read()


def _pdf(text: str) -> bytes:
    """A one-page PDF saying `text` (enough for a viewer to open)."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _checkout_page(session: dict[str, Any], products: dict[str, dict[str, Any]]) -> str:
    product = products[session["product_id"]]
    price = product["price"]
    amount = price["price"] * session["quantity"] / 100
    from html import escape

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Dodo Payments test checkout</title></head>
<body><main><h1>Test checkout</h1>
<p>{escape(product['name'])} &times; {session['quantity']}: {price['currency']} {amount:,.2f} per {price['payment_frequency_interval'].lower()}</p>
<p>Test mode stand-in for Dodo Payments. No card is charged.</p>
<form method="post" action="/checkout/{session['session_id']}/pay"><button type="submit">Pay</button></form>
<form method="post" action="/checkout/{session['session_id']}/pay?outcome=declined"><button type="submit">Decline the card</button></form>
</main></body></html>"""


def _portal_page(customer: dict[str, Any], subscriptions: list[dict[str, Any]], done: str) -> str:
    from html import escape

    rows = "".join(f"<li>{escape(s['product_id'])} &times; {s['quantity']}: {escape(s['status'])}"
                   f"{' (cancels at the next billing date)' if s['cancel_at_next_billing_date'] else ''}</li>"
                   for s in subscriptions)
    said = {"card": "<p role='status'>Card updated. Any payment on hold was retried.</p>",
            "cancel": "<p role='status'>Your subscription will end at the next billing date.</p>"}.get(done, "")
    cid = escape(customer["customer_id"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Dodo Payments test portal</title></head>
<body><main><h1>Billing portal</h1><p>{escape(customer['email'])}</p>{said}<ul>{rows}</ul>
<p>Test mode stand-in for Dodo Payments' customer portal. No card is charged.</p>
<form method="post" action="/portal/{cid}/card"><button type="submit">Update card</button></form>
<form method="post" action="/portal/{cid}/cancel"><button type="submit">Cancel subscription</button></form>
</main></body></html>"""


def main() -> None:
    """Serve a stand-in Dodo for the browser journeys until stopped."""
    import argparse
    import os

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--webhook-url", default="http://127.0.0.1:8000/api/v1/billing/webhooks/dodo")
    parser.add_argument("--secret", default=os.environ.get("DODO_WEBHOOK_SECRET") or DEFAULT_SECRET)
    parser.add_argument("--tax-rate", type=float, default=0.0)
    parser.add_argument("--products", help="JSON file: [{product_id, name, price, currency, interval}]")
    args = parser.parse_args()
    fake = FakeDodo(webhook_secret=args.secret, webhook_url=args.webhook_url, tax_rate=args.tax_rate)
    if args.products:
        for product in json.loads(open(args.products, encoding="utf-8").read()):
            fake.add_product(name=product["name"], price=int(product["price"]), currency=product.get("currency", "USD"),
                             interval=product.get("interval", "Month"), product_id=product["product_id"])
    fake.start(args.host, args.port)
    print(f"fake Dodo serving at {fake.base_url}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
