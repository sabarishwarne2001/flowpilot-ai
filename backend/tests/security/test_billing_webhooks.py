"""Billing webhooks — only a correctly signed, fresh, in-mode event is recorded, once.

    pytest tests/security/test_billing_webhooks.py -q

F-002 found the billing receivers had no tests at all. They are unauthenticated
by design (the signature is the credential), so this is the whole security
boundary for money-moving events. Real Stripe and Standard-Webhooks (Dodo)
signatures are computed here with the standard algorithms, so a receiver that
"verifies" nothing, or the wrong thing, fails.

What is proven, per gateway where the gateway supports it:

* no signature, a wrong secret, a tampered body, a stale or future timestamp:
  refused, and NO row is written (a row per unverified POST is a free disk fill);
* a valid event is recorded once; a replay of the same event is acknowledged as a
  duplicate and adds no row (idempotency, replay);
* secret rotation: an event signed with either configured secret is accepted;
* an oversized body is refused before any signature work;
* a Stripe test-mode event never reaches a live deployment (and vice versa). The
  equivalent Dodo guard compares the deployment's configuration with itself and cannot
  fire (F-033), so it is not asserted here;
* with no secret configured NOTHING is trusted (500, so the sender retries);
* unknown gateways are a plain 404.

NOT proven here (needs the gateways, blocked in this sandbox, and is decided by
the reconciler job, not the receiver): out-of-order delivery, failed-payment
dunning and cancellation state changes. See docs/hardening/02-security-deploy.md.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.stripe_inbound_event import StripeInboundEvent
from app.services.billing import stripe_gateway

STRIPE_PATHS = ("/api/v1/billing/webhooks/stripe", "/api/v1/billing/stripe/webhook")
SECRET_A = "whsec_" + "A1b2C3d4" * 4
SECRET_B = "whsec_" + "Z9y8X7w6" * 4
DODO_SECRET_RAW = b"dodo-signing-secret-for-tests-0123456789"
DODO_SECRET = "whsec_" + base64.b64encode(DODO_SECRET_RAW).decode()


def _count(db: Session) -> int:
    db.rollback()
    return db.execute(select(func.count()).select_from(StripeInboundEvent)).scalar_one()


def _stripe_event(*, livemode: bool = False, event_id: str | None = None) -> bytes:
    body = {
        "id": event_id or f"evt_{uuid.uuid4().hex[:24]}",
        "object": "event",
        "api_version": settings.STRIPE_API_VERSION,
        "created": int(time.time()),
        "livemode": livemode,
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_123", "object": "subscription", "customer": "cus_123", "status": "active"}},
    }
    return json.dumps(body, separators=(",", ":")).encode()


def _stripe_signature(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


@pytest.fixture()
def stripe_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRETS", SecretStr(f"{SECRET_A},{SECRET_B}"))
    monkeypatch.setattr(settings, "STRIPE_LIVEMODE", False)
    stripe_gateway.reset_gateway()
    yield
    stripe_gateway.reset_gateway()


def _post(client: TestClient, path: str, payload: bytes, headers: dict[str, str]):
    return client.post(path, content=payload, headers={"content-type": "application/json", **headers})


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", STRIPE_PATHS)
def test_a_correctly_signed_event_is_recorded_once_and_a_replay_adds_nothing(
    client: TestClient, db_session: Session, stripe_configured, path: str
) -> None:
    payload = _stripe_event()
    headers = {"Stripe-Signature": _stripe_signature(payload, SECRET_A)}

    first = _post(client, path, payload, headers)
    assert first.status_code == 200 and first.json() == {"received": True, "duplicate": False}
    assert _count(db_session) == 1

    replay = _post(client, path, payload, {"Stripe-Signature": _stripe_signature(payload, SECRET_A)})
    assert replay.status_code == 200 and replay.json()["duplicate"] is True
    assert _count(db_session) == 1


@pytest.mark.parametrize("path", STRIPE_PATHS)
def test_refused_requests_write_no_row(client: TestClient, db_session: Session, stripe_configured, path: str) -> None:
    payload = _stripe_event()
    good = _stripe_signature(payload, SECRET_A)
    stale = _stripe_signature(payload, SECRET_A, timestamp=int(time.time()) - 3600)
    attempts = {
        "no header": ({}, payload),
        "garbage header": ({"Stripe-Signature": "not-a-signature"}, payload),
        "empty scheme": ({"Stripe-Signature": "t=,v1="}, payload),
        "wrong secret": ({"Stripe-Signature": _stripe_signature(payload, "whsec_" + "Q" * 32)}, payload),
        "tampered body": ({"Stripe-Signature": good}, payload.replace(b"active", b"canceled")),
        "stale timestamp (replay of an old capture)": ({"Stripe-Signature": stale}, payload),
        "signature over a different body": ({"Stripe-Signature": _stripe_signature(b"{}", SECRET_A)}, payload),
    }
    for name, (headers, body) in attempts.items():
        response = _post(client, path, body, headers)
        assert response.status_code == 400, (name, response.status_code)
        assert response.json() == {"detail": "Signature verification failed."} or "Stripe-Signature" in response.text, name
    assert _count(db_session) == 0


def test_the_refusal_does_not_say_why(client: TestClient, stripe_configured) -> None:
    payload = _stripe_event()
    wrong = _post(client, STRIPE_PATHS[0], payload, {"Stripe-Signature": _stripe_signature(payload, "whsec_" + "Q" * 32)})
    stale = _post(
        client, STRIPE_PATHS[0], payload,
        {"Stripe-Signature": _stripe_signature(payload, SECRET_A, timestamp=int(time.time()) - 3600)},
    )
    assert wrong.text == stale.text


def test_either_configured_secret_is_accepted_during_a_rotation(
    client: TestClient, db_session: Session, stripe_configured
) -> None:
    for secret in (SECRET_A, SECRET_B):
        payload = _stripe_event()
        response = _post(client, STRIPE_PATHS[0], payload, {"Stripe-Signature": _stripe_signature(payload, secret)})
        assert response.status_code == 200, secret
    assert _count(db_session) == 2


def test_an_oversized_body_is_refused_before_any_signature_work(
    client: TestClient, db_session: Session, stripe_configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "STRIPE_MAX_WEBHOOK_BODY_BYTES", 1024)
    called = []
    monkeypatch.setattr(stripe_gateway.StripeGateway, "verify_event", lambda self, **kw: called.append(kw))
    response = _post(client, STRIPE_PATHS[0], b"x" * 4096, {"Stripe-Signature": "t=1,v1=00"})
    assert response.status_code == 413
    assert called == []
    assert _count(db_session) == 0


def test_a_test_mode_event_is_refused_by_a_live_deployment_and_vice_versa(
    client: TestClient, db_session: Session, stripe_configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _stripe_event(livemode=True)
    response = _post(client, STRIPE_PATHS[0], payload, {"Stripe-Signature": _stripe_signature(payload, SECRET_A)})
    assert response.status_code == 400 and "mode" in response.text.lower()
    assert _count(db_session) == 0

    monkeypatch.setattr(settings, "STRIPE_LIVEMODE", True)
    payload = _stripe_event(livemode=False)
    response = _post(client, STRIPE_PATHS[0], payload, {"Stripe-Signature": _stripe_signature(payload, SECRET_A)})
    assert response.status_code == 400
    assert _count(db_session) == 0


def test_with_no_secret_configured_nothing_is_trusted(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """500 so Stripe retries, and never a 200 for an event that could not be verified."""
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRETS", None)
    stripe_gateway.reset_gateway()
    payload = _stripe_event()
    response = _post(client, STRIPE_PATHS[0], payload, {"Stripe-Signature": _stripe_signature(payload, SECRET_A)})
    assert response.status_code == 500
    assert _count(db_session) == 0


def test_a_verified_body_that_is_not_an_event_is_refused(client: TestClient, db_session: Session, stripe_configured) -> None:
    payload = json.dumps({"hello": "world"}).encode()
    response = _post(client, STRIPE_PATHS[0], payload, {"Stripe-Signature": _stripe_signature(payload, SECRET_A)})
    assert response.status_code == 400
    assert _count(db_session) == 0


@pytest.mark.parametrize("spelling", ["STRIPE", "Stripe", "sTrIpE"])
def test_the_generic_route_does_not_pretend_to_handle_stripe(
    client: TestClient, db_session: Session, stripe_configured, spelling: str
) -> None:
    """Any spelling other than the literal lowercase one reached the gateway-neutral
    receiver, whose Stripe adapter has no verifier: an unhandled AttributeError.
    Stripe has its own endpoint; the generic one now answers 404 for it."""
    payload = _stripe_event()
    for headers in ({"Stripe-Signature": "t=1,v1=00"}, {"Stripe-Signature": _stripe_signature(payload, SECRET_A)}):
        response = _post(client, f"/api/v1/billing/webhooks/{spelling}", payload, headers)
        assert response.status_code == 404, (spelling, response.status_code)
    assert _count(db_session) == 0


# ---------------------------------------------------------------------------
# Dodo Payments (Standard Webhooks)
# ---------------------------------------------------------------------------

DODO_PATH = "/api/v1/billing/webhooks/dodo"


@pytest.fixture()
def dodo_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "DODO_WEBHOOK_SECRET", SecretStr(DODO_SECRET))
    monkeypatch.setattr(settings, "DODO_API_KEY", SecretStr("dodo-test-key-0123456789abcdef"))
    monkeypatch.setattr(settings, "DODO_LIVEMODE", False)


def _dodo_event(*, livemode: bool = False) -> bytes:
    return json.dumps(
        {
            "type": "subscription.active",
            "business_id": "biz_1",
            "timestamp": "2026-09-30T00:00:00Z",
            "livemode": livemode,
            "data": {"subscription_id": "sub_1", "customer": {"customer_id": "cus_1"}, "payload_type": "Subscription"},
        },
        separators=(",", ":"),
    ).encode()


def _dodo_headers(payload: bytes, *, webhook_id: str, timestamp: int | None = None, secret: bytes = DODO_SECRET_RAW) -> dict[str, str]:
    ts = str(int(time.time()) if timestamp is None else timestamp)
    signed = b".".join([webhook_id.encode(), ts.encode(), payload])
    signature = base64.b64encode(hmac.new(secret, signed, hashlib.sha256).digest()).decode()
    return {"webhook-id": webhook_id, "webhook-timestamp": ts, "webhook-signature": f"v1,{signature}"}


def test_dodo_valid_event_is_recorded_once_and_a_replay_adds_nothing(
    client: TestClient, db_session: Session, dodo_configured
) -> None:
    payload = _dodo_event()
    webhook_id = f"msg_{uuid.uuid4().hex[:20]}"
    first = _post(client, DODO_PATH, payload, _dodo_headers(payload, webhook_id=webhook_id))
    assert first.status_code == 200, first.text[:200]
    assert _count(db_session) == 1

    replay = _post(client, DODO_PATH, payload, _dodo_headers(payload, webhook_id=webhook_id))
    assert replay.status_code == 200
    assert _count(db_session) == 1


def test_dodo_refused_requests_write_no_row(client: TestClient, db_session: Session, dodo_configured) -> None:
    payload = _dodo_event()
    wid = f"msg_{uuid.uuid4().hex[:20]}"
    good = _dodo_headers(payload, webhook_id=wid)
    stale = _dodo_headers(payload, webhook_id=wid, timestamp=int(time.time()) - 7200)
    attempts = {
        "no headers": ({}, payload),
        "missing signature": ({k: v for k, v in good.items() if k != "webhook-signature"}, payload),
        "wrong secret": (_dodo_headers(payload, webhook_id=wid, secret=b"another-secret-entirely-0123456789"), payload),
        "tampered body": (good, payload.replace(b"active", b"cancelled")),
        "stale timestamp": (stale, payload),
        "non-numeric timestamp": ({**good, "webhook-timestamp": "yesterday"}, payload),
        "id swapped after signing": ({**good, "webhook-id": "msg_other"}, payload),
    }
    for name, (headers, body) in attempts.items():
        response = _post(client, DODO_PATH, body, headers)
        assert response.status_code == 401, (name, response.status_code)
        assert response.content == b"", name
    assert _count(db_session) == 0


def test_dodo_oversized_body_is_refused_before_verification(
    client: TestClient, db_session: Session, dodo_configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DODO_MAX_WEBHOOK_BODY_BYTES", 1024)
    response = _post(client, DODO_PATH, b"x" * 4096, {"webhook-id": "m", "webhook-timestamp": "1", "webhook-signature": "v1,AA=="})
    assert response.status_code == 413
    assert _count(db_session) == 0


def test_dodo_with_no_secret_configured_nothing_is_trusted(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DODO_WEBHOOK_SECRET", None)
    payload = _dodo_event()
    response = _post(client, DODO_PATH, payload, _dodo_headers(payload, webhook_id="msg_x"))
    assert response.status_code == 500
    assert _count(db_session) == 0


@pytest.mark.parametrize("gateway", ["paypal", "unknown", "..%2f..%2fadmin", "stripe%00"])
def test_an_unknown_gateway_is_a_plain_404(client: TestClient, gateway: str) -> None:
    response = client.post(f"/api/v1/billing/webhooks/{gateway}", content=b"{}")
    # A NUL character in the URL is refused at the edge as a 400 (F-037, added after this
    # case was written); every other unknown gateway is a plain 404.
    assert response.status_code in ((400,) if "%00" in gateway else (404, 405))
