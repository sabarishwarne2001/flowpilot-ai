"""Outgoing webhooks, live: signed, retried, and visible.

An organization ADMIN registers an https endpoint for document.completed. A
document is processed, and the REAL outbox relay and delivery loops run
against a recording stand-in for the network (nothing leaves the machine):

* the secret is shown once, at creation; a MEMBER may not manage endpoints;
* the receiver gets a POST whose X-FlowPilot-Signature is
  "t=<unix>,v1=<HMAC-SHA256(secret, '<t>.<body>')>" over the exact bytes sent;
* a 503 is retried later (backoff), never dropped; the next attempt succeeds;
* every attempt is listed, with the signature header redacted;
* redelivering a delivered event is refused (it would send a duplicate);
* "Send test event" pushes a signed webhook.test ping through the same path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from sqlalchemy import update

from app import worker
from app.models.webhook_delivery import WebhookDelivery
from app.services import webhook_dispatch
from tests.engines.conftest import Engines

URL = "https://hooks.acme.example.com/flowpilot"


@dataclass
class _Response:
    status_code: int
    headers: dict = field(default_factory=dict)
    body: bytes = b"ok"
    resolved_ip: str = "203.0.113.10"


class _Receiver:
    """Plays the customer's server: records every request, answers from a script."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.answers: list[int] = []

    def request(self, method: str, url: str, *, headers: dict, body: bytes, **_kw) -> _Response:
        self.requests.append({"method": method, "url": url, "headers": headers, "body": body})
        return _Response(self.answers.pop(0) if self.answers else 200)


@pytest.fixture
def receiver(monkeypatch) -> _Receiver:
    stub = _Receiver()
    monkeypatch.setattr(webhook_dispatch.egress, "http_client", lambda *_a, **_kw: stub)
    return stub


def _run(loop) -> None:
    """One pass of a worker loop until it finds nothing to do."""
    shutdown = worker.GracefulShutdown()

    def stop(_seconds: float) -> None:
        shutdown.requested = True

    original, worker._idle_sleep = worker._idle_sleep, stop
    try:
        loop(shutdown=shutdown, batch_size=10, lease_seconds=60, idle_sleep_seconds=0)
    finally:
        worker._idle_sleep = original


def _deliver() -> None:
    _run(worker.run_relay_loop)
    _run(worker.run_delivery_loop)


def _verify(secret: str, request: dict) -> dict:
    parts = dict(p.split("=", 1) for p in request["headers"]["X-FlowPilot-Signature"].split(","))
    expected = hmac.new(secret.encode(), f"{parts['t']}.{request['body'].decode()}".encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(parts["v1"], expected), "signature does not verify"
    return json.loads(request["body"])


def _make_due(engines: Engines) -> None:
    engines.refresh()
    engines.db.execute(update(WebhookDelivery).where(WebhookDelivery.organization_id == engines.org)
                       .values(available_at=datetime.now(timezone.utc)))
    engines.db.commit()


def test_a_document_event_is_signed_retried_and_delivered(engines: Engines, receiver: _Receiver) -> None:
    t = engines.tenant
    body = {"url": URL, "event_types": ["document.completed"], "description": "AP system"}
    assert engines.post("/webhooks/endpoints", body, org=True, as_user=t.contributor).status_code == 403
    created = engines.post("/webhooks/endpoints", body, org=True, as_user=t.org_admin)
    assert created.status_code == 201, created.text
    secret, endpoint = created.json()["secret"], created.json()["endpoint"]
    assert secret not in engines.get(f"/webhooks/endpoints/{endpoint['id']}", org=True).text

    work_item_id = engines.process("hook.pdf", [["TAX INVOICE", "Invoice No: INV-HOOK-1"]], marker="INV-HOOK-1",
                                   classification="Invoice", entities={"invoice_number": "INV-HOOK-1"})
    receiver.answers = [503]
    _deliver()
    assert len(receiver.requests) == 1, receiver.requests
    first = _verify(secret, receiver.requests[0])
    assert first["type"] == "document.completed" and str(work_item_id) in json.dumps(first["data"]), first

    deliveries = engines.get(f"/webhooks/endpoints/{endpoint['id']}/deliveries", org=True).json()
    assert len(deliveries) == 1 and deliveries[0]["status"] == "FAILED", deliveries
    _deliver()
    assert len(receiver.requests) == 1, "a failed delivery was retried before its backoff elapsed"

    _make_due(engines)
    _deliver()
    assert len(receiver.requests) == 2
    second = _verify(secret, receiver.requests[1])
    assert second["id"] == first["id"], "a retry must carry the same delivery id so receivers can deduplicate"
    assert receiver.requests[1]["headers"]["X-FlowPilot-Attempt"] == "2"

    delivery = engines.get(f"/webhooks/endpoints/{endpoint['id']}/deliveries", org=True).json()[0]
    assert delivery["status"] == "DELIVERED", delivery
    attempts = sorted(engines.get(f"/webhooks/deliveries/{delivery['id']}/attempts", org=True).json(),
                      key=lambda a: a["attempt_number"])
    assert [a["response_status"] for a in attempts] == [503, 200], attempts
    assert all(secret not in json.dumps(a) and parts_hidden(a) for a in attempts), attempts
    again = engines.post(f"/webhooks/deliveries/{delivery['id']}/redeliver", org=True)
    assert again.status_code == 409, again.text


def parts_hidden(attempt: dict) -> bool:
    headers = attempt.get("request_headers") or {}
    return "v1=" not in str(headers.get("X-FlowPilot-Signature", ""))


def test_send_test_event_pings_the_endpoint_through_the_signed_path(engines: Engines, receiver: _Receiver) -> None:
    t = engines.tenant
    created = engines.post("/webhooks/endpoints", {"url": URL, "event_types": ["document.completed"]}, org=True)
    assert created.status_code == 201, created.text
    secret, endpoint_id = created.json()["secret"], created.json()["endpoint"]["id"]

    assert engines.post(f"/webhooks/endpoints/{endpoint_id}/test", org=True, as_user=t.contributor).status_code == 403
    ping = engines.post(f"/webhooks/endpoints/{endpoint_id}/test", org=True, as_user=t.org_admin)
    assert ping.status_code == 202, ping.text
    assert ping.json()["event_type"] == "webhook.test" and ping.json()["status"] == "PENDING", ping.json()

    _deliver()
    assert len(receiver.requests) == 1, receiver.requests
    envelope = _verify(secret, receiver.requests[0])
    assert envelope["type"] == "webhook.test" and envelope["data"]["endpoint_id"] == endpoint_id, envelope
    deliveries = engines.get(f"/webhooks/endpoints/{endpoint_id}/deliveries", org=True).json()
    assert [(d["event_type"], d["status"]) for d in deliveries] == [("webhook.test", "DELIVERED")], deliveries
